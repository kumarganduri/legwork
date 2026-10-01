import os
import io
from typing import Optional, Dict, Any, List, Tuple

from mcp.server.fastmcp import FastMCP, Image as MCPImage
from PIL import Image as PILImage, ImageDraw, ImageFont, ImageOps

mcp = FastMCP("pillow-tools")


def _abs(path: str) -> str:
    return os.path.abspath(path)


def _ensure_outputs() -> str:
    out_dir = os.path.abspath("./outputs")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _resample_filter(name: str):
    name = (name or "").strip().lower()
    mapping = {
        "nearest": PILImage.NEAREST,
        "box": PILImage.BOX,
        "bilinear": PILImage.BILINEAR,
        "hamming": PILImage.HAMMING,
        "bicubic": PILImage.BICUBIC,
        "lanczos": PILImage.LANCZOS,
    }
    return mapping.get(name, PILImage.LANCZOS)


def _format_extension(fmt: str) -> str:
    fmt_upper = (fmt or "").strip().upper()
    mapping = {
        "JPEG": "jpg",
        "JPG": "jpg",
        "PNG": "png",
        "WEBP": "webp",
        "GIF": "gif",
        "TIFF": "tiff",
        "BMP": "bmp",
        "PPM": "ppm",
        "PGM": "pgm",
        "PBM": "pbm",
        "TGA": "tga",
        "ICO": "ico",
        "ICNS": "icns",
        "PDF": "pdf",
        "EPS": "eps",
        # Some formats may require plugins; saves will fail if unsupported.
        "AVIF": "avif",
        "HEIF": "heif",
    }
    return "." + mapping.get(fmt_upper, fmt_upper.lower() if fmt_upper else "png")


def _derive_output_name(in_path: str, suffix: str, ext: Optional[str] = None) -> str:
    out_dir = _ensure_outputs()
    base = os.path.splitext(os.path.basename(in_path))[0]
    if not ext:
        ext = ".png"
    return os.path.join(out_dir, f"{base}_{suffix}{ext}")


@mcp.tool()
def image_info(path: str) -> Dict[str, Any]:
    """
    Inspect an image and return basic information.
    """
    ap = _abs(path)
    with PILImage.open(ap) as im:
        info = {
            "path": ap,
            "format": im.format,
            "mode": im.mode,
            "width": im.width,
            "height": im.height,
            "is_animated": bool(getattr(im, "is_animated", False)),
            "n_frames": int(getattr(im, "n_frames", 1)),
            "info_keys": sorted(list(im.info.keys())) if hasattr(im, "info") else [],
        }
    return info


@mcp.tool()
def resize_image(
    path: str,
    width: int,
    height: int,
    keep_aspect: bool = True,
    resample: str = "lanczos",
) -> MCPImage:
    """
    Resize an image to the target size. If keep_aspect is True, the image fits within the box.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers")
    ap = _abs(path)
    flt = _resample_filter(resample)
    with PILImage.open(ap) as im:
        if keep_aspect:
            work = im.copy()
            work.thumbnail((width, height), flt)
            resized = work
        else:
            resized = im.resize((width, height), flt)

        out_path = _derive_output_name(ap, f"resized_{resized.width}x{resized.height}", ".png")
        resized.save(out_path, format="PNG")
    return MCPImage(path=os.path.abspath(out_path))


@mcp.tool()
def rotate_image(path: str, angle: float, expand: bool = True, resample: str = "bicubic") -> MCPImage:
    """
    Rotate an image by angle degrees. Use expand=True to grow the canvas to fit.
    """
    ap = _abs(path)
    flt = _resample_filter(resample)
    with PILImage.open(ap) as im:
        rotated = im.rotate(angle, resample=flt, expand=expand)
        out_path = _derive_output_name(ap, f"rotated_{int(angle)}", ".png")
        rotated.save(out_path, format="PNG")
    return MCPImage(path=os.path.abspath(out_path))


@mcp.tool()
def convert_format(path: str, format: str, quality: Optional[int] = None, optimize: bool = True) -> MCPImage:
    """
    Convert an image to another format (e.g., PNG, JPEG, WEBP). Quality applies to lossy formats if supported.
    """
    if not format:
        raise ValueError("format is required (e.g., 'PNG', 'JPEG', 'WEBP')")
    ap = _abs(path)
    fmt_upper = format.strip().upper()
    ext = _format_extension(fmt_upper)
    with PILImage.open(ap) as im:
        save_img = im
        # Handle alpha for formats that don't support transparency well
        if fmt_upper in ("JPEG", "JPG") and im.mode in ("RGBA", "LA", "P"):
            save_img = im.convert("RGB")

        out_path = _derive_output_name(ap, f"converted", ext)
        kwargs = {"optimize": optimize}
        if quality is not None:
            kwargs["quality"] = int(quality)
        try:
            save_img.save(out_path, format=fmt_upper, **kwargs)
        except Exception:
            # Retry with minimal kwargs in case options are unsupported
            save_img.save(out_path, format=fmt_upper)
    return MCPImage(path=os.path.abspath(out_path))


@mcp.tool()
def overlay_text(
    path: str,
    text: str,
    x: int = 10,
    y: int = 10,
    color: str = "white",
    size: int = 20,
) -> MCPImage:
    """
    Draw simple text onto an image at (x, y) using the default bitmap font.
    """
    if not text:
        raise ValueError("text cannot be empty")
    ap = _abs(path)
    with PILImage.open(ap) as im:
        # Ensure drawable mode
        if im.mode not in ("RGB", "RGBA"):
            base = im.convert("RGBA")
        else:
            base = im.copy()

        draw = ImageDraw.Draw(base)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        draw.text((x, y), text, fill=color, font=font)
        out_path = _derive_output_name(ap, "text", ".png")
        base.save(out_path, format="PNG")
    return MCPImage(path=os.path.abspath(out_path))


@mcp.tool()
def grayscale(path: str) -> MCPImage:
    """
    Convert an image to grayscale.
    """
    ap = _abs(path)
    with PILImage.open(ap) as im:
        gray = im.convert("L")
        out_path = _derive_output_name(ap, "grayscale", ".png")
        gray.save(out_path, format="PNG")
    return MCPImage(path=os.path.abspath(out_path))


@mcp.tool()
def crop(path: str, left: int, top: int, right: int, bottom: int) -> MCPImage:
    """
    Crop an image to the given box (left, top, right, bottom).
    """
    ap = _abs(path)
    with PILImage.open(ap) as im:
        if left < 0 or top < 0 or right <= left or bottom <= top:
            raise ValueError("Invalid crop box")
        # Clamp to image bounds
        box = (max(0, left), max(0, top), min(im.width, right), min(im.height, bottom))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("Crop box outside image bounds")
        cropped = im.crop(box)
        out_path = _derive_output_name(ap, f"crop_{box[0]}_{box[1]}_{box[2]}_{box[3]}", ".png")
        cropped.save(out_path, format="PNG")
    return MCPImage(path=os.path.abspath(out_path))


if __name__ == "__main__":
    # Self-test: create a tiny local image, query info, and perform a simple operation.
    _ensure_outputs()
    test_img_path = os.path.abspath("./outputs/selftest_input.png")
    if not os.path.exists(test_img_path):
        img = PILImage.new("RGB", (16, 16), color="blue")
        img.save(test_img_path, format="PNG")

    info = image_info(test_img_path)
    if not info:
        raise SystemExit("image_info returned empty result")

    rotated = rotate_image(test_img_path, 45)
    # Verify an output path exists if available
    out_path = getattr(rotated, "path", None)
    if out_path and not os.path.exists(out_path):
        raise SystemExit("rotate_image did not create an output file")

    print("Self-test completed.")