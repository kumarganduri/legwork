import os
import json
from typing import Optional, Tuple

from mcp.server.fastmcp import FastMCP, Image as MCPImage

mcp = FastMCP("rembg-mcp")


def _ensure_model_home():
    # Keep models inside the current project so it works in sandboxed environments
    default_home = os.path.abspath("./models")
    if not os.environ.get("REMBG_HOME"):
        os.environ["REMBG_HOME"] = default_home
    os.makedirs(os.environ["REMBG_HOME"], exist_ok=True)


def _parse_bgcolor(color: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    if not color:
        return None
    s = color.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 3:  # RGB short
        r = int(s[0] * 2, 16)
        g = int(s[1] * 2, 16)
        b = int(s[2] * 2, 16)
        a = 255
        return (r, g, b, a)
    if len(s) == 4:  # RGBA short
        r = int(s[0] * 2, 16)
        g = int(s[1] * 2, 16)
        b = int(s[2] * 2, 16)
        a = int(s[3] * 2, 16)
        return (r, g, b, a)
    if len(s) == 6:  # RRGGBB
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        a = 255
        return (r, g, b, a)
    if len(s) == 8:  # RRGGBBAA
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        a = int(s[6:8], 16)
        return (r, g, b, a)
    # Try JSON array like "[255,255,255,255]"
    try:
        vals = json.loads(color)
        if isinstance(vals, list) and len(vals) in (3, 4):
            r, g, b = vals[0], vals[1], vals[2]
            a = vals[3] if len(vals) == 4 else 255
            return (int(r), int(g), int(b), int(a))
    except Exception:
        pass
    raise ValueError("Invalid bgcolor format. Use hex (#RRGGBB or #RRGGBBAA) or a JSON array [r,g,b(,a)].")


@mcp.tool()
def remove_background(
    input_path: str,
    output_path: Optional[str] = None,
    model: str = "u2net",
    alpha_matting: bool = False,
    decontaminate: bool = False,
    only_mask: bool = False,
    post_process_mask: bool = False,
    bgcolor: Optional[str] = None,
) -> MCPImage:
    """
    Remove the background from an image file and save a PNG with transparency.

    - input_path: Path to the input image file.
    - output_path: Optional output path. If omitted, writes to ./outputs/<stem>.out.png.
    - model: Model name (e.g., 'u2net', 'u2netp', 'isnet-general-use', 'birefnet-general', 'bria-rmbg', etc.). Default 'u2net' for fast, small offline use.
    - alpha_matting: Enable alpha matting refinement (slower).
    - decontaminate: Enable edge color decontamination.
    - only_mask: Return only the mask (grayscale).
    - post_process_mask: Post-process mask to binary.
    - bgcolor: Background replacement color. Hex like '#FFFFFFFF' or '#FFFFFF', or JSON array '[r,g,b,a]'.
    """
    _ensure_model_home()

    from pathlib import Path
    from PIL import Image as PILImage
    from rembg import new_session, remove

    in_path = Path(input_path)
    if not in_path.exists() or not in_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    outputs_dir = Path("./outputs")
    outputs_dir.mkdir(parents=True, exist_ok=True)

    if output_path:
        out_path = Path(output_path)
        if out_path.is_dir():
            out_path = out_path / (in_path.stem + ".out.png")
        # Ensure parent exists and is under current folder
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_path = outputs_dir / (in_path.stem + ".out.png")

    bg_tuple = _parse_bgcolor(bgcolor) if bgcolor else None

    # Open input via PIL to get a PIL.Image back from rembg.remove
    with PILImage.open(in_path) as im:
        # Convert to RGBA to ensure alpha handling
        if im.mode != "RGBA":
            im = im.convert("RGBA")

        session = new_session(model)
        result = remove(
            im,
            session=session,
            alpha_matting=alpha_matting,
            decontaminate=decontaminate,
            only_mask=only_mask,
            post_process_mask=post_process_mask,
            bgcolor=bg_tuple,
        )
        # result is a PIL.Image when input was a PIL.Image
        result.save(out_path, format="PNG")

    return MCPImage(path=str(out_path.resolve()))


if __name__ == "__main__":
    # Offline self-test: use cached model at ./models and process a tiny local image.
    _ensure_model_home()

    from PIL import Image as PILImage

    test_in = "._rembg_selftest_input.png"
    test_out = "._rembg_selftest_output.png"

    # Create a small test image
    img = PILImage.new("RGBA", (8, 8), (255, 0, 0, 255))
    img.save(test_in)

    # Run background removal using the small, pre-downloaded 'u2net' model
    produced = remove_background(
        input_path=test_in,
        output_path=test_out,
        model="u2net",
        decontaminate=True,
    )

    # Verify output exists and is non-empty
    out_path = produced.path if isinstance(produced, MCPImage) else test_out
    if not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
        raise RuntimeError("Self-test failed: output file missing or empty.")

    print("Self-test passed. Output at:", out_path)
    mcp.run()