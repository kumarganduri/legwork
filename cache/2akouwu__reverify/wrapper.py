import json
import subprocess
import sys
from typing import Any, Dict, Union

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reverify-wrapper")


def _run_reverify_verify(target: str, claim_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Invoke `reverify verify <target> --json --claim <json>` and return parsed JSON.
    """
    # Serialize the claim safely (no shell involved, so quoting is fine)
    claim_json = json.dumps(claim_obj, separators=(",", ":"))

    cmd = [
        sys.executable,
        "-m",
        "reverify.cli",
        "verify",
        str(target),
        "--json",
        "--claim",
        claim_json,
    ]

    proc = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"reverify verify failed (exit {proc.returncode}): {proc.stderr.strip()}")

    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError("reverify verify produced no output")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse reverify JSON output: {e}\nOutput was:\n{stdout[:1000]}") from e

    if not isinstance(data, dict):
        # Expect a top-level JSON object; future-proof but still assert a basic shape.
        raise RuntimeError(f"Unexpected JSON shape from reverify verify: expected object, got {type(data).__name__}")

    return data


@mcp.tool()
def re_verify_claim(target: str, claim: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Verify a single claim against a binary (or hex bytes) using Reverify.

    Parameters:
    - target: Path to a binary file, or a hex string of bytes.
              For claims that don't need an input file (e.g., emulate_result),
              pass a minimal valid hex string like "00".
    - claim: Claim as a dict or JSON string. Example:
        {
          "kind": "emulate_result",
          "code": "b805000000b90300000001c8c3",
          "arch": "x86",
          "expect_registers": {"eax": 8}
        }

    Returns:
    - Parsed JSON object returned by `reverify verify --json`.
    """
    if isinstance(claim, str):
        claim_obj = json.loads(claim)
    else:
        claim_obj = claim
    return _run_reverify_verify(target, claim_obj)


if __name__ == "__main__":
    # Self-test: run the simplest documented claim (emulate_result) against a trivial target hex.
    # We do NOT assert on the domain-specific verdict (VERIFIED/REFUTED/etc.),
    # only that the command ran and returned a JSON object.
    test_claim = {
        "kind": "emulate_result",
        "code": "b805000000b90300000001c8c3",  # mov eax,5; mov ecx,3; add eax,ecx; ret
        "arch": "x86",
        "expect_registers": {"eax": 8},
    }
    result = re_verify_claim("00", test_claim)  # minimal hex input avoids '-' stdin pitfalls
    # Basic shape checks: top-level object
    if not isinstance(result, dict):
        raise SystemExit("Self-test failed: expected dict JSON from reverify verify")
    # A minimal sanity: ensure there's at least one key in the result
    if not result:
        raise SystemExit("Self-test failed: empty JSON object from reverify verify")
    print("OK")