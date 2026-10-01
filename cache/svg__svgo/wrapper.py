import os
import sys
import shutil
import subprocess
import tempfile
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("svgo")


def _svgo_bin() -> str:
    """
    Resolve the local svgo binary installed via npm (./node_modules/.bin/svgo).
    """
    if os.name == "nt":
        bin_path = os.path.abspath(os.path.join(".", "node_modules", ".bin", "svgo.cmd"))
    else:
        bin_path = os.path.abspath(os.path.join(".", "node_modules", ".bin", "svgo"))
    if not os.path.exists(bin_path):
        raise FileNotFoundError(
            f"svgo binary not found at {bin_path}. Run 'npm install svgo' in this directory."
        )
    return bin_path


def _run_svgo_on_file(input_path: str, output_path: str, multipass: bool) -> None:
    """
    Invoke svgo CLI to optimize a single file.
    """
    svgo = _svgo_bin()
    cmd = [svgo, input_path, "-o", output_path]
    if multipass:
        cmd.insert(1, "--multipass")  # place flag early but after binary
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"svgo failed: {proc.stderr.strip() or proc.stdout.strip()}")


@mcp.tool()
def svgo_optimize_file(input_path: str, output_path: Optional[str] = None, multipass: bool = True) -> str:
    """
    Optimize a single SVG file with SVGO.

    Args:
      input_path: Path to an existing .svg file (read-only).
      output_path: Optional path for the optimized SVG. If omitted, writes to ./out/<name>.min.svg.
      multipass: If true, run SVGO in multipass mode.

    Returns:
      Absolute path to the optimized SVG file.
    """
    in_path = os.path.abspath(input_path)
    if not os.path.isfile(in_path):
        raise FileNotFoundError(f"Input file not found: {in_path}")

    if output_path is None:
        out_dir = os.path.abspath(os.path.join(".", "out"))
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(in_path))[0]
        out_path = os.path.join(out_dir, f"{base}.min.svg")
    else:
        out_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    _run_svgo_on_file(in_path, out_path, multipass=multipass)
    if not os.path.exists(out_path):
        raise RuntimeError("svgo reported success but no output file was produced")
    return out_path


@mcp.tool()
def svgo_optimize_text(svg: str, multipass: bool = True) -> str:
    """
    Optimize an SVG provided as a string.

    Args:
      svg: SVG content as a UTF-8 string.
      multipass: If true, run SVGO in multipass mode.

    Returns:
      Optimized SVG content as a string.
    """
    work_root = os.path.abspath(os.path.join(".", ".svgo_tmp"))
    os.makedirs(work_root, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="job_", dir=work_root)
    try:
        in_path = os.path.join(tmpdir, "in.svg")
        out_path = os.path.join(tmpdir, "out.svg")
        with open(in_path, "w", encoding="utf-8") as f:
            f.write(svg)

        _run_svgo_on_file(in_path, out_path, multipass=multipass)

        with open(out_path, "r", encoding="utf-8") as f:
            optimized = f.read()
        if not optimized.strip():
            raise RuntimeError("svgo produced empty output")
        return optimized
    finally:
        # Clean up temp dir to avoid buildup; ignore errors
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    # Self-test: optimize a tiny inline SVG and ensure we get non-empty output.
    # This proves the svgo CLI is installed and works offline.
    sample_svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="#ff0000"/></svg>'
    try:
        out = svgo_optimize_text(sample_svg, multipass=True)
        if not out or not isinstance(out, str):
            raise RuntimeError("Self-test failed: empty or invalid output from svgo_optimize_text")
        # Also test file path workflow
        test_dir = os.path.abspath("./.svgo_selftest")
        os.makedirs(test_dir, exist_ok=True)
        in_fp = os.path.join(test_dir, "test.svg")
        with open(in_fp, "w", encoding="utf-8") as f:
            f.write(sample_svg)
        out_fp = svgo_optimize_file(in_fp)
        if not (out_fp and os.path.isfile(out_fp) and os.path.getsize(out_fp) > 0):
            raise RuntimeError("Self-test failed: svgo_optimize_file did not produce a valid file")
        print("SVGO MCP wrapper self-test passed.")
    except Exception as e:
        print(f"Self-test error: {e}", file=sys.stderr)
        raise