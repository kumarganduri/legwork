import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional, Literal, Dict, Any

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("sharp-mcp")

# Single, shared Node.js script that performs operations using sharp.
# It reads a single JSON argument from process.argv[2] and writes a single-line JSON result to stdout.
_NODE_SCRIPT = r"""
const fs = require('fs');
const path = require('path');
(async () => {
  const args = JSON.parse(process.argv[2] || "{}");
  const sharp = require('sharp');

  function normalizeFormat(fmt) {
    if (!fmt) return null;
    const f = fmt.toLowerCase();
    if (f === 'jpg') return 'jpeg';
    return f;
  }

  function applyFormat(pipe, fmt, quality) {
    if (!fmt) return pipe;
    const f = normalizeFormat(fmt);
    if (f === 'jpeg') return pipe.jpeg(quality ? { quality } : {});
    if (f === 'png') return pipe.png({});
    if (f === 'webp') return pipe.webp(quality ? { quality } : {});
    if (f === 'avif') return pipe.avif(quality ? { quality } : {});
    if (f === 'gif') return pipe.gif({});
    // If unknown, just return the pipe unchanged; sharp will infer from output extension if possible.
    return pipe;
  }

  function pickMetadata(md) {
    // Avoid returning Buffer-like fields; only include simple scalars from common metadata.
    const out = {};
    const keys = [
      'format', 'width', 'height', 'space', 'channels', 'density',
      'depth', 'isProgressive', 'hasProfile', 'hasAlpha', 'orientation'
    ];
    for (const k of keys) {
      if (Object.prototype.hasOwnProperty.call(md, k)) out[k] = md[k];
    }
    return out;
  }

  if (args.op === 'resize') {
    let pipe = sharp(args.input);
    if (args.autoOrient) pipe = pipe.rotate();
    const fit = args.fit || 'inside';
    if (args.width || args.height) {
      pipe = pipe.resize({
        width: args.width || null,
        height: args.height || null,
        fit
      });
    }
    pipe = applyFormat(pipe, args.format, args.quality || null);
    await pipe.toFile(args.output);
    process.stdout.write(JSON.stringify({ output: path.resolve(args.output) }));
  } else if (args.op === 'create') {
    const create = {
      width: args.width,
      height: args.height,
      channels: 4,
      background: {
        r: args.r, g: args.g, b: args.b, alpha: args.alpha
      }
    };
    let pipe = sharp({ create });
    pipe = applyFormat(pipe, args.format || 'png', args.quality || null);
    await pipe.toFile(args.output);
    process.stdout.write(JSON.stringify({ output: path.resolve(args.output) }));
  } else if (args.op === 'metadata') {
    const md = await sharp(args.input).metadata();
    process.stdout.write(JSON.stringify({ metadata: pickMetadata(md) }));
  } else {
    console.error("Unknown op");
    process.exit(2);
  }
})().catch(err => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
"""

def _find_node_base_dir() -> str:
    """
    Find a directory whose node_modules contains sharp.
    Search in:
    - current working directory and its parents
    - directory of this file and its parents
    Fallback to current working directory.
    """
    def search_up(start: str) -> Optional[str]:
        cur = os.path.abspath(start)
        while True:
            if os.path.isdir(os.path.join(cur, "node_modules", "sharp")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return None

    for base in [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]:
        found = search_up(base)
        if found:
            return found
    return os.getcwd()


def _run_node(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the embedded Node.js sharp script for a given operation.
    Returns parsed JSON from stdout.
    Raises on non-zero exit or parse error.
    """
    args = dict(payload)
    args["op"] = op
    node_base = _find_node_base_dir()

    # Use a temporary file for the JS to avoid shell quoting pitfalls and size limits with -e
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(_NODE_SCRIPT)
        js_path = tf.name

    try:
        proc = subprocess.run(
            ["node", js_path, json.dumps(args)],
            cwd=node_base,
            capture_output=True,
            text=True,
            timeout=180
        )
        if proc.returncode != 0:
            raise RuntimeError(f"sharp node script failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
        stdout = proc.stdout.strip()
        if not stdout:
            raise RuntimeError("sharp node script returned empty output")
        try:
            return json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse sharp output as JSON: {e}\nOutput was:\n{stdout}") from e
    finally:
        try:
            os.unlink(js_path)
        except Exception:
            pass


def _ensure_parent_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


@mcp.tool()
def resize_image(
    input_path: str,
    output_path: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    format: Optional[Literal["jpeg", "jpg", "png", "webp", "gif", "avif"]] = None,
    fit: Literal["cover", "contain", "fill", "inside", "outside"] = "inside",
    auto_orient: bool = False,
    quality: Optional[int] = None
) -> Image:
    """
    Resize and/or convert an image using sharp.
    - input_path: path to an existing image file.
    - output_path: where to write the result (extension may imply format).
    - width/height: target dimensions (either or both).
    - format: optional explicit output format (jpeg/jpg/png/webp/gif/avif).
    - fit: resizing fit mode (default inside).
    - auto_orient: rotate based on EXIF.
    - quality: optional quality for lossy formats (jpeg/webp/avif).
    Returns an Image that points to the written file.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"input_path not found: {input_path}")
    if width is None and height is None and format is None and not auto_orient:
        # Nothing to do; still allow a no-op rewrite to output_path
        pass
    if format:
        fmt = format.lower()
        if fmt not in {"jpeg", "jpg", "png", "webp", "gif", "avif"}:
            raise ValueError(f"Unsupported format: {format}")
    _ensure_parent_dir(output_path)

    result = _run_node("resize", {
        "input": os.path.abspath(input_path),
        "output": os.path.abspath(output_path),
        "width": int(width) if width is not None else None,
        "height": int(height) if height is not None else None,
        "format": format,
        "fit": fit,
        "autoOrient": bool(auto_orient),
        "quality": int(quality) if quality is not None else None,
    })
    out_path = os.path.abspath(result.get("output") or output_path)
    if not os.path.exists(out_path):
        raise RuntimeError(f"sharp did not produce output at {out_path}")
    return Image(path=out_path)


@mcp.tool()
def create_solid_image(
    width: int,
    height: int,
    r: int,
    g: int,
    b: int,
    alpha: float,
    output_path: str,
    format: Literal["png", "jpeg", "jpg", "webp", "gif", "avif"] = "png",
    quality: Optional[int] = None
) -> Image:
    """
    Create a solid RGBA image using sharp and write it to output_path.
    - width/height: image dimensions in pixels.
    - r/g/b: 0-255 color channels.
    - alpha: 0.0-1.0 transparency.
    - format: output format (default png).
    - quality: optional quality for lossy formats (jpeg/webp/avif).
    Returns an Image that points to the written file.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    for name, v in [("r", r), ("g", g), ("b", b)]:
        if not (0 <= int(v) <= 255):
            raise ValueError(f"{name} must be in 0..255")
    if not (0.0 <= float(alpha) <= 1.0):
        raise ValueError("alpha must be in 0.0..1.0")
    _ensure_parent_dir(output_path)

    result = _run_node("create", {
        "width": int(width),
        "height": int(height),
        "r": int(r),
        "g": int(g),
        "b": int(b),
        "alpha": float(alpha),
        "output": os.path.abspath(output_path),
        "format": format,
        "quality": int(quality) if quality is not None else None,
    })
    out_path = os.path.abspath(result.get("output") or output_path)
    if not os.path.exists(out_path):
        raise RuntimeError(f"sharp did not produce output at {out_path}")
    return Image(path=out_path)


@mcp.tool()
def image_metadata(input_path: str) -> Dict[str, Any]:
    """
    Read basic image metadata (format, width, height, etc.) using sharp.
    Returns a JSON-serializable dict of common fields.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"input_path not found: {input_path}")
    result = _run_node("metadata", {
        "input": os.path.abspath(input_path),
    })
    md = result.get("metadata") or {}
    if not isinstance(md, dict) or not md:
        raise RuntimeError("Failed to read metadata or empty metadata returned")
    return md


if __name__ == "__main__":
    # Self-test: create a small image, read its metadata, then resize/convert it.
    # This must run offline and complete quickly.
    base_dir = os.getcwd()
    # Ensure sharp is available; provide a helpful message if not.
    node_base = _find_node_base_dir()
    if not os.path.isdir(os.path.join(node_base, "node_modules", "sharp")):
        raise SystemExit("sharp not installed; run: npm install sharp")

    solid_path = os.path.abspath("selftest_solid.png")
    out_resized_path = os.path.abspath("selftest_resized.jpg")

    img1 = create_solid_image(8, 6, 255, 0, 0, 0.5, solid_path, format="png")
    if not os.path.exists(img1.path) or os.path.getsize(img1.path) <= 0:
        raise SystemExit("create_solid_image did not produce a valid file")

    md = image_metadata(img1.path)
    if not md:
        raise SystemExit("image_metadata returned empty result")

    img2 = resize_image(img1.path, out_resized_path, width=4, height=4, format="jpeg", auto_orient=False, quality=80)
    if not os.path.exists(img2.path) or os.path.getsize(img2.path) <= 0:
        raise SystemExit("resize_image did not produce a valid file")

    print("OK: sharp wrapper self-test completed")