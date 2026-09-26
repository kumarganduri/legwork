from __future__ import annotations

from typing import List, Literal, Dict, Any
from mcp.server.fastmcp import FastMCP

# AnyJev imports per README usage
from anyjev import Decider, Question
from anyjev.backends.vllm import VLLMBackend

mcp = FastMCP("anyjev")


@mcp.tool()
def decide_choice_vllm(
    endpoint_url: str,
    model_path_or_id: str,
    input_text: str,
    options: List[str],
    question: str = "Select one option.",
    level: Literal["raw", "L0"] = "L0",
) -> Dict[str, Any]:
    """
    Decide a multiple-choice question using AnyJev with a vLLM backend.

    Requirements (per README):
    - A vLLM server must be running and reachable at `endpoint_url`.
      For raw/L0 decisions, serve with `--task generate`.
      For L2 you would need a pooled hidden-state server and a fitted head; this tool exposes raw/L0 only.

    Parameters:
    - endpoint_url: Base URL of the running vLLM server (e.g., "http://localhost:8000").
    - model_path_or_id: Path or HF repo id served by vLLM (e.g., "./qwen-b18" or "Qwen/Qwen2.5-7B-Instruct").
    - input_text: The user input/content to decide on.
    - options: List of option strings (K-way choice, K <= 26 per README).
    - question: The phrasing of the typed question (default: "Select one option.").
    - level: "raw" (restricted softmax over label tokens) or "L0" (zero-label position-bias correction).

    Returns:
    - A dict with keys:
        - "level": the decision level reported by AnyJev (or the requested level as fallback)
        - "distribution": mapping option -> probability (sums to ~1)
        - "name": the question name used ("choice")
    """
    if not options or not isinstance(options, list):
        raise ValueError("options must be a non-empty list of strings")
    if level not in ("raw", "L0"):
        raise ValueError('level must be "raw" or "L0" for this tool')

    backend = VLLMBackend(endpoint_url, model_path_or_id)
    decider = Decider(backend, level=level)

    q = Question.choice(question, options, name="choice")
    decisions = decider.decide(input_text, [q])

    # decisions is expected to be a mapping name -> Decision
    decision = decisions.get("choice")
    if decision is None:
        raise RuntimeError("AnyJev did not return a decision for the 'choice' question")

    # Extract distribution and level with fallbacks
    distribution = getattr(decision, "distribution", None)
    if distribution is None:
        # Attempt alternative attribute or method names if the API exposes differently
        # but keep this conservative per README (distribution is shown explicitly).
        raise RuntimeError("Decision object did not expose a 'distribution' attribute")

    decided_level = getattr(decision, "level", level)

    # Ensure JSON-serializable return
    return {
        "level": str(decided_level),
        "distribution": {str(k): float(v) for k, v in dict(distribution).items()},
        "name": "choice",
    }


def _self_test() -> None:
    """
    Self-test: exercise the simplest documented function with trivial input.

    Per README, the basic Python API shows:
        from anyjev import Decider, Question
        route = Question.choice("Which team...", ["billing","technical","sales","other"], name="route")

    We verify that Question.choice constructs an object with the expected 'name' field.
    """
    q = Question.choice("Which team should handle this?", ["billing", "technical"], name="route")
    # Check basic shape only (type/keys), not any domain-specific semantics.
    if not hasattr(q, "name"):
        raise AssertionError("Question.choice did not produce an object with attribute 'name'")
    if q.name != "route":
        raise AssertionError(f"Question.name expected 'route', got '{q.name}'")
    # Avoid backend calls in self-test to keep it trivial and fast.


if __name__ == "__main__":
    _self_test()
    print("OK")