from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from legwork.llm_client import (
    INFRA_RETRY_ATTEMPTS,
    RATE_LIMIT_RETRY_ATTEMPTS,
    ChatMessage,
    LLMAuthError,
    LLMConfig,
    LLMMalformedOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
    complete,
)

CONFIG = LLMConfig(endpoint="https://api.example.com/v1", api_key="sk-test", model="gpt-test")
MESSAGES = [ChatMessage(role="user", content="hi")]


def _fake_response(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


# --- LLMConfig.from_env -------------------------------------------------


def test_from_env_success(monkeypatch):
    monkeypatch.setenv("LEGWORK_LLM_ENDPOINT", "https://api.example.com/v1/")
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "sk-abc")
    monkeypatch.setenv("LEGWORK_LLM_MODEL", "gpt-test")
    config = LLMConfig.from_env()
    assert config.endpoint == "https://api.example.com/v1"  # trailing slash stripped
    assert config.api_key == "sk-abc"
    assert config.model == "gpt-test"


def test_from_env_missing_vars_raises_auth_error(monkeypatch):
    monkeypatch.delenv("LEGWORK_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("LEGWORK_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LEGWORK_LLM_MODEL", raising=False)
    with pytest.raises(LLMAuthError, match="LEGWORK_LLM_ENDPOINT"):
        LLMConfig.from_env()



def _clear_env(monkeypatch):
    for name in ("LEGWORK_LLM_ENDPOINT", "LEGWORK_LLM_API_KEY", "LEGWORK_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _key_file(tmp_path, monkeypatch, text, mode=0o600):
    path = tmp_path / "legwork.env"
    path.write_text(text)
    path.chmod(mode)
    monkeypatch.setenv("LEGWORK_ENV_FILE", str(path))
    return path


def test_from_env_reads_the_key_file_when_the_environment_has_nothing(tmp_path, monkeypatch):
    # How the hub gets a key in Claude Desktop and Cursor, which have no shell to source from.
    _clear_env(monkeypatch)
    _key_file(tmp_path, monkeypatch, (
        "# my legwork key\n"
        "export LEGWORK_LLM_ENDPOINT=https://api.example.com/v1/\n"
        "LEGWORK_LLM_API_KEY='sk-from-file'\n"
        'export LEGWORK_LLM_MODEL="gpt-test"\n'
        "OPENAI_API_KEY=not-ours\n"
        "$(rm -rf ~)\n"
    ))
    config = LLMConfig.from_env()
    assert (config.endpoint, config.api_key, config.model) == ("https://api.example.com/v1", "sk-from-file", "gpt-test")


def test_environment_variables_win_over_the_key_file(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LEGWORK_LLM_MODEL", "from-env")
    _key_file(tmp_path, monkeypatch, "LEGWORK_LLM_ENDPOINT=https://e/v1\nLEGWORK_LLM_API_KEY=k\nLEGWORK_LLM_MODEL=from-file\n")
    assert LLMConfig.from_env().model == "from-env"


def test_a_key_file_other_users_can_read_is_refused(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    path = _key_file(tmp_path, monkeypatch, "LEGWORK_LLM_API_KEY=k\n", mode=0o644)
    with pytest.raises(LLMAuthError, match=f"chmod 600 {path}"):
        LLMConfig.from_env()


def test_the_missing_key_message_names_the_key_file(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    _key_file(tmp_path, monkeypatch, "LEGWORK_LLM_ENDPOINT=https://e/v1\n")
    with pytest.raises(LLMAuthError, match="LEGWORK_LLM_API_KEY, LEGWORK_LLM_MODEL") as info:
        LLMConfig.from_env()
    assert "legwork.env (chmod 600)" in str(info.value)

# --- successful call ------------------------------------------------------


def test_complete_returns_content_on_success():
    body = {"choices": [{"message": {"content": "wrapper code here"}}]}
    with patch("legwork.llm_client.urllib.request.urlopen", return_value=_fake_response(body)):
        result = complete(CONFIG, MESSAGES)
    assert result == "wrapper code here"


def test_complete_sends_bearer_token_and_model():
    body = {"choices": [{"message": {"content": "ok"}}]}
    with patch(
        "legwork.llm_client.urllib.request.urlopen", return_value=_fake_response(body)
    ) as mock_urlopen:
        complete(CONFIG, MESSAGES)
    request = mock_urlopen.call_args[0][0]
    assert request.headers["Authorization"] == f"Bearer {CONFIG.api_key}"
    sent_body = json.loads(request.data.decode("utf-8"))
    assert sent_body["model"] == CONFIG.model
    assert sent_body["messages"] == [{"role": "user", "content": "hi"}]


# --- auth failure: never retried ------------------------------------------


def test_401_raises_auth_error_without_retry():
    http_error = urllib.error.HTTPError(url="", code=401, msg="Unauthorized", hdrs=None, fp=None)
    with (
        patch("legwork.llm_client.urllib.request.urlopen", side_effect=http_error) as mock_urlopen,
        patch("legwork.llm_client.time.sleep") as mock_sleep,
    ):
        with pytest.raises(LLMAuthError, match="auth failed"):
            complete(CONFIG, MESSAGES)
    assert mock_urlopen.call_count == 1  # no retry
    mock_sleep.assert_not_called()


# --- timeout: infra-retried, separate from wrapper-repair budget ----------


def test_timeout_retries_then_succeeds():
    url_error = urllib.error.URLError("timed out")
    body = {"choices": [{"message": {"content": "ok after retry"}}]}
    with (
        patch(
            "legwork.llm_client.urllib.request.urlopen",
            side_effect=[url_error, _fake_response(body)],
        ) as mock_urlopen,
        patch("legwork.llm_client.time.sleep") as mock_sleep,
    ):
        result = complete(CONFIG, MESSAGES)
    assert result == "ok after retry"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once_with(2.0)  # first backoff delay


def test_timeout_exhausts_retries_then_raises():
    url_error = urllib.error.URLError("timed out")
    with (
        patch("legwork.llm_client.urllib.request.urlopen", side_effect=url_error) as mock_urlopen,
        patch("legwork.llm_client.time.sleep") as mock_sleep,
    ):
        with pytest.raises(LLMTimeoutError):
            complete(CONFIG, MESSAGES)
    assert mock_urlopen.call_count == INFRA_RETRY_ATTEMPTS + 1  # 1 initial + 2 retries
    assert mock_sleep.call_count == INFRA_RETRY_ATTEMPTS
    # exponential backoff: 2s, then 4s
    mock_sleep.assert_any_call(2.0)
    mock_sleep.assert_any_call(4.0)


def test_rate_limit_retries_then_raises():
    http_error = urllib.error.HTTPError(url="", code=429, msg="Too Many Requests", hdrs=None, fp=None)
    with (
        patch("legwork.llm_client.urllib.request.urlopen", side_effect=http_error) as mock_urlopen,
        patch("legwork.llm_client.time.sleep"),
    ):
        with pytest.raises(LLMRateLimitError):
            complete(CONFIG, MESSAGES)
    assert mock_urlopen.call_count == RATE_LIMIT_RETRY_ATTEMPTS + 1


def test_rate_limits_wait_long_enough_for_the_limit_to_reset():
    # 2s and 4s burned whole build attempts on OpenAI's per-minute limits (2026-10-01).
    http_error = urllib.error.HTTPError(url="", code=429, msg="Too Many Requests", hdrs=None, fp=None)
    with (
        patch("legwork.llm_client.urllib.request.urlopen", side_effect=http_error),
        patch("legwork.llm_client.time.sleep") as sleep,
        pytest.raises(LLMRateLimitError),
    ):
        complete(CONFIG, MESSAGES)
    assert [c.args[0] for c in sleep.call_args_list] == [10.0, 20.0, 40.0, 60.0]


def test_rate_limits_follow_retry_after_capped_at_two_minutes():
    import email.message

    def limited(seconds):
        headers = email.message.Message()
        headers["Retry-After"] = seconds
        return urllib.error.HTTPError(url="", code=429, msg="Too Many Requests", hdrs=headers, fp=None)

    ok = _fake_response({"choices": [{"message": {"content": "done"}}]})
    with (
        patch("legwork.llm_client.urllib.request.urlopen", side_effect=[limited("7"), limited("900"), ok]),
        patch("legwork.llm_client.time.sleep") as sleep,
    ):
        assert complete(CONFIG, MESSAGES) == "done"
    assert [c.args[0] for c in sleep.call_args_list] == [7.0, 120.0]


def test_5xx_treated_as_transient_and_retried():
    http_error = urllib.error.HTTPError(url="", code=503, msg="Service Unavailable", hdrs=None, fp=None)
    body = {"choices": [{"message": {"content": "recovered"}}]}
    with (
        patch(
            "legwork.llm_client.urllib.request.urlopen",
            side_effect=[http_error, _fake_response(body)],
        ),
        patch("legwork.llm_client.time.sleep"),
    ):
        result = complete(CONFIG, MESSAGES)
    assert result == "recovered"


# --- malformed response: not retried, distinct from timeout ---------------


def test_malformed_json_raises_malformed_output_error_without_retry():
    resp = MagicMock()
    resp.read.return_value = b"not json at all"
    resp.__enter__.return_value = resp
    with (
        patch("legwork.llm_client.urllib.request.urlopen", return_value=resp) as mock_urlopen,
        patch("legwork.llm_client.time.sleep") as mock_sleep,
    ):
        with pytest.raises(LLMMalformedOutputError):
            complete(CONFIG, MESSAGES)
    assert mock_urlopen.call_count == 1
    mock_sleep.assert_not_called()


def test_missing_choices_raises_malformed_output_error():
    body = {"unexpected": "shape"}
    with patch("legwork.llm_client.urllib.request.urlopen", return_value=_fake_response(body)):
        with pytest.raises(LLMMalformedOutputError, match="missing choices"):
            complete(CONFIG, MESSAGES)


def test_unexpected_4xx_raises_auth_error():
    http_error = urllib.error.HTTPError(url="", code=418, msg="I'm a teapot", hdrs=None, fp=None)
    with patch("legwork.llm_client.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(LLMAuthError, match="Unexpected LLM endpoint response"):
            complete(CONFIG, MESSAGES)



@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"message": "Upstream error from Nvidia: Service temporarily overloaded", "code": 503}, LLMTimeoutError),
        ({"message": "slow down", "code": 429}, LLMRateLimitError),
    ],
)
def test_router_errors_inside_a_200_are_retried_as_infrastructure(error, expected):
    retries = RATE_LIMIT_RETRY_ATTEMPTS if expected is LLMRateLimitError else INFRA_RETRY_ATTEMPTS
    """OpenRouter reports upstream failures as HTTP 200 with an error body;
    that cost a wrapper-repair attempt before (provider tests, 2026-09-27)."""
    body = {"id": "gen-1", "error": error}
    with (
        patch("legwork.llm_client.urllib.request.urlopen", return_value=_fake_response(body)) as mock_urlopen,
        patch("legwork.llm_client.time.sleep"),
        pytest.raises(expected),
    ):
        complete(CONFIG, MESSAGES)
    assert mock_urlopen.call_count == retries + 1


def test_router_auth_error_inside_a_200_fails_fast():
    body = {"error": {"message": "No auth credentials found", "code": 401}}
    with patch("legwork.llm_client.urllib.request.urlopen", return_value=_fake_response(body)), pytest.raises(LLMAuthError):
        complete(CONFIG, MESSAGES)


def test_an_account_out_of_credit_stops_at_once_with_the_reason():
    """OpenAI sends 429 for an empty account too; waiting never fixes it."""
    import io

    body = b'{"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted", "message": "You have no credits remaining."}}'
    http_error = urllib.error.HTTPError(url="", code=429, msg="Too Many Requests", hdrs=None, fp=io.BytesIO(body))
    with (
        patch("legwork.llm_client.urllib.request.urlopen", side_effect=http_error) as mock_urlopen,
        patch("legwork.llm_client.time.sleep") as sleep,
        pytest.raises(LLMAuthError, match="out of credit: You have no credits remaining"),
    ):
        complete(CONFIG, MESSAGES)
    assert mock_urlopen.call_count == 1 and not sleep.called
