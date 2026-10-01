import io
import os
import time
from typing import Dict, Optional, Literal, Union, List

import qrcode
from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("python-qrcode")


def _ensure_out_path(out_path: Optional[str], default_basename: str) -> str:
    if out_path is None or not str(out_path).strip():
        os.makedirs("outputs", exist_ok=True)
        out_path = os.path.join("outputs", default_basename)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    return os.path.abspath(out_path)


def _parse_color(c: Union[str, List[int], None]) -> Union[str, tuple, None]:
    if c is None:
        return None
    if isinstance(c, list) and len(c) == 3 and all(isinstance(v, int) for v in c):
        return tuple(c)  # type: ignore[return-value]
    # For PIL, named colors or hex strings are fine.
    if isinstance(c, str):
        return c
    return c


def _ec_level(level: Literal["L", "M", "Q", "H"]) -> int:
    level = level.upper()  # type: ignore[assignment]
    mapping = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }
    return mapping[level]


@mcp.tool()
def generate_qr_png(
    data: str,
    out_path: Optional[str] = None,
    version: Optional[int] = None,
    error_correction: Literal["L", "M", "Q", "H"] = "M",
    box_size: int = 10,
    border: int = 4,
    fill_color: Union[str, List[int]] = "black",
    back_color: Union[str, List[int]] = "white",
) -> Image:
    """
    Generate a QR code PNG image.

    - data: text to encode
    - out_path: file path to save (defaults to ./outputs/qr_<timestamp>.png)
    - version: 1..40 (size). If None, fit automatically.
    - error_correction: L (7%), M (15%, default), Q (25%), H (30%)
    - box_size: pixels per QR module
    - border: modules thick border (min 4)
    - fill_color, back_color: hex string/name or [R,G,B]
    """
    abs_out = _ensure_out_path(out_path, f"qr_{int(time.time()*1000)}.png")
    qr = qrcode.QRCode(
        version=version,
        error_correction=_ec_level(error_correction),
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=(version is None))
    img = qr.make_image(
        fill_color=_parse_color(fill_color),
        back_color=_parse_color(back_color),
    )
    img.save(abs_out)
    return Image(path=abs_out)


@mcp.tool()
def generate_qr_svg(
    data: str,
    out_path: Optional[str] = None,
    factory: Literal["path", "basic", "fragment", "path-fill", "fill"] = "path",
    attrib: Optional[Dict[str, str]] = None,
    version: Optional[int] = None,
    error_correction: Literal["L", "M", "Q", "H"] = "M",
    box_size: int = 10,
    border: int = 4,
) -> Image:
    """
    Generate a QR code SVG image.

    - data: text to encode
    - out_path: file path to save (defaults to ./outputs/qr_<timestamp>.svg)
    - factory: 'path' (default), 'basic', 'fragment', 'path-fill', or 'fill'
    - attrib: optional SVG root attributes dict (e.g., {'class': 'my-css'})
    - version, error_correction, box_size, border: QR sizing options
    """
    abs_out = _ensure_out_path(out_path, f"qr_{int(time.time()*1000)}.svg")

    # Import factories lazily
    import qrcode.image.svg as svgmod

    factory_map = {
        "path": getattr(svgmod, "SvgPathImage", None),
        "basic": getattr(svgmod, "SvgImage", None),
        "fragment": getattr(svgmod, "SvgFragmentImage", None),
        "path-fill": getattr(svgmod, "SvgPathFillImage", None),
        "fill": getattr(svgmod, "SvgFillImage", None),
    }
    factory_cls = factory_map.get(factory)
    if factory_cls is None:
        raise ValueError(f"Unsupported or unavailable SVG factory: {factory}")

    qr = qrcode.QRCode(
        version=version,
        error_correction=_ec_level(error_correction),
        box_size=box_size,
        border=border,
        image_factory=factory_cls,
    )
    qr.add_data(data)
    qr.make(fit=(version is None))

    if attrib is not None:
        img = qr.make_image(attrib=attrib)
    else:
        img = qr.make_image()
    img.save(abs_out)
    return Image(path=abs_out)


@mcp.tool()
def qr_ascii(
    data: str,
    border: int = 4,
    invert: bool = False,
) -> str:
    """
    Generate ASCII art for a QR code and return it as text.
    - data: text to encode
    - border: modules thick border (min 0)
    - invert: swap foreground/background
    """
    qr = qrcode.QRCode(border=border)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.StringIO()
    # print_ascii signature supports 'out' and 'invert' in python-qrcode
    qr.print_ascii(out=buf, invert=invert)
    buf.seek(0)
    return buf.read()


if __name__ == "__main__":
    # Self-test: simple offline usage confirmed by README examples
    img = generate_qr_png("Self-test QR", box_size=2, border=2)
    p = getattr(img, "path", None)
    if not p or not os.path.exists(p) or os.path.getsize(p) <= 0:
        raise SystemExit("PNG generation self-test failed")

    ascii_art = qr_ascii("test")
    if not isinstance(ascii_art, str) or not ascii_art.strip():
        raise SystemExit("ASCII generation self-test failed")

    print("Self-test OK:", p, f"(ASCII length {len(ascii_art)})")