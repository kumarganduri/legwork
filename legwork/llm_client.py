"""OpenAI-compatible chat-completions client with its own infra-retry
policy, separate from the wrapper-repair retry budget (T6).

Design doc: docs/designs/legwork-jit-ai-runtime.md, T3 and "Decided"
section. Legwork does not bundle a multi-provider shim in v1 — the caller
points this at any OpenAI-compatible chat-completions endpoint (direct
OpenAI, or a proxy like LiteLLM for another provider) via env vars.

Infra-retry policy: a transient timeout or rate-limit gets its own short
backoff-and-retry that does NOT consume one of the 3 wrapper-repair
attempts (a network blip shouldn't burn the retry loop's real budget on
codegen it never actually got to try). Auth failure is never retried — a
bad key won't fix itself by waiting.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 60
# 1 initial attempt + this many retries (3 total tries) on timeout/rate-limit.
INFRA_RETRY_ATTEMPTS = 2
INFRA_RETRY_BASE_DELAY_SECONDS = 2.0  # exponential backoff: 2s, then 4s


class LLMTimeoutError(Exception):
    """The endpoint didn't respond in time, even after infra retries."""


class LLMRateLimitError(Exception):
    """The endpoint rate-limited the request, even after infra retries."""


class LLMAuthError(Exception):
    """Bad/missing API key, or config env vars missing entirely. Never
    retried — a bad key won't fix itself."""


class LLMMalformedOutputError(Exception):
    """The endpoint returned a 200 but the response body isn't shaped like
    a chat-completion (missing choices/message/content) — Legwork can't
    extract usable text from it. Counts as a wrapper-repair attempt at the
    caller level (T6), not an infra retry: reissuing the identical request
    to a misbehaving endpoint isn't likely to help."""


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMConfig:
    endpoint: str  # base URL, e.g. "https://api.openai.com/v1"
    api_key: str
    model: str

    @classmethod
    def from_env(cls) -> LLMConfig:
        """From LEGWORK_LLM_* environment variables, falling back to the key
        file (~/.legwork.env, or LEGWORK_ENV_FILE) for any that aren't set.
        The file is what makes `legwork hub` work with a model: Claude Desktop
        and Cursor start it with no shell to `source` from, and the
        alternative, the key in their MCP config, stores it in plain text."""
        names = ("LEGWORK_LLM_ENDPOINT", "LEGWORK_LLM_API_KEY", "LEGWORK_LLM_MODEL")
        values = {name: os.environ.get(name) for name in names}
        path = env_file_path()
        if not all(values.values()) and path.exists():
            from_file = _read_env_file(path)
            values = {name: values[name] or from_file.get(name) for name in names}
        missing = [name for name in names if not values[name]]
        if missing:
            raise LLMAuthError(
                "Missing " + ", ".join(missing) + ". Legwork needs an OpenAI-compatible endpoint, key and model: "
                f"set them as environment variables or put them in {path} (chmod 600), one per line, "
                "e.g. LEGWORK_LLM_MODEL=gpt-5."
            )
        return cls(endpoint=values["LEGWORK_LLM_ENDPOINT"].rstrip("/"), api_key=values["LEGWORK_LLM_API_KEY"], model=values["LEGWORK_LLM_MODEL"])


def env_file_path() -> Path:
    return Path(os.environ.get("LEGWORK_ENV_FILE") or Path.home() / ".legwork.env").expanduser()


def _read_env_file(path: Path) -> dict[str, str]:
    """LEGWORK_LLM_* lines of a shell-style file (`export NAME=value` or
    `NAME=value`, optionally quoted). Nothing is executed. Like ssh with a
    private key, it refuses a file other users can read."""
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise LLMAuthError(f"{path} can be read by other users. Make it private first: chmod 600 {path}")
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name.startswith("LEGWORK_LLM_"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        found[name] = value
    return found


def _post_chat_completion(config: LLMConfig, messages: list[ChatMessage], timeout: float) -> str:
    url = f"{config.endpoint}/chat/completions"
    body = json.dumps(
        {
            "model": config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise LLMAuthError(
                f"LLM endpoint auth failed ({exc.code}) — check your API key env var"
            ) from exc
        if exc.code == 429:
            raise LLMRateLimitError("LLM endpoint rate-limited (429)") from exc
        if exc.code >= 500:
            raise LLMTimeoutError(
                f"LLM endpoint returned {exc.code} — treating as transient"
            ) from exc
        raise LLMAuthError(
            f"Unexpected LLM endpoint response ({exc.code}): {exc.reason}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        # urllib.error.URLError wraps socket.timeout for stdlib requests too.
        raise LLMTimeoutError(f"Couldn't reach LLM endpoint: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LLMMalformedOutputError(f"LLM endpoint response wasn't valid JSON: {raw[:200]!r}") from exc

    # Routers like OpenRouter report an upstream failure as HTTP 200 with an
    # "error" body (seen: 503 "provider overloaded"). That's the network's
    # problem, not the model's reply, so it gets the infra retry instead of
    # costing a wrapper-repair attempt.
    error = payload.get("error") if isinstance(payload, dict) else None
    if error and not payload.get("choices"):
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else error
        if code in (401, 403):
            raise LLMAuthError(f"LLM endpoint auth failed ({code}): {message}")
        if code == 429:
            raise LLMRateLimitError(f"LLM endpoint rate-limited: {message}")
        raise LLMTimeoutError(f"LLM endpoint reported an upstream error ({code}): {message}")
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMMalformedOutputError(
            f"LLM endpoint response missing choices[0].message.content: {payload!r}"
        ) from exc


def complete(
    config: LLMConfig,
    messages: list[ChatMessage],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Call the chat-completions endpoint with the infra-retry policy.

    Timeout/rate-limit get up to INFRA_RETRY_ATTEMPTS retries with
    exponential backoff (2s, 4s) — this budget is entirely separate from
    the wrapper-repair retry loop (T6). Auth failures and malformed
    responses are never retried here; they propagate immediately.
    """
    last_error: LLMTimeoutError | LLMRateLimitError | None = None
    for attempt in range(INFRA_RETRY_ATTEMPTS + 1):
        try:
            return _post_chat_completion(config, messages, timeout)
        except (LLMTimeoutError, LLMRateLimitError) as exc:
            last_error = exc
            if attempt < INFRA_RETRY_ATTEMPTS:
                time.sleep(INFRA_RETRY_BASE_DELAY_SECONDS * (2**attempt))
                continue
            raise
    raise last_error  # pragma: no cover — loop always returns or raises above
