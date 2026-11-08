import os
from typing import Optional, Literal, Dict, Any

from mcp.server.fastmcp import FastMCP
from PIL import Image
import imagehash

mcp = FastMCP("imagehash-mcp")


def _compute_hash(
    image_path: str,
    algorithm: str = "average",
    hash_size: int = 8,
    highfreq_factor: int = 4,
    binbits: int = 3,
    whash_mode: Literal["haar", "db4"] = "haar",
    remove_max_haar_ll: bool = True,
    image_scale: Optional[int] = None,
    min_segment_size: Optional[int] = None,
    segmentation_image_size: Optional[int] = None,
):
    algo = algorithm.strip().lower()
    abspath = os.path.abspath(image_path)
    with Image.open(abspath) as im:
        if algo in ("average", "ahash", "average_hash"):
            return imagehash.average_hash(im, hash_size=hash_size)
        elif algo in ("phash", "perceptual", "perceptual_hash"):
            return imagehash.phash(im, hash_size=hash_size, highfreq_factor=highfreq_factor)
        elif algo in ("dhash", "difference", "difference_hash"):
            return imagehash.dhash(im, hash_size=hash_size)
        elif algo in ("dhash_vertical", "difference_vertical", "difference_hash_vertical"):
            return imagehash.dhash_vertical(im, hash_size=hash_size)
        elif algo in ("whash", "wavelet", "wavelet_hash"):
            # image_scale can be None or an int, mode is 'haar' or 'db4'
            return imagehash.whash(
                im,
                hash_size=hash_size,
                image_scale=image_scale,
                mode=whash_mode,
                remove_max_haar_ll=remove_max_haar_ll,
            )
        elif algo in ("colorhash", "hsv", "color"):
            return imagehash.colorhash(im, binbits=binbits)
        elif algo in ("crop_resistant", "crop-resistant", "crop_resistant_hash"):
            # Only pass parameters we expose; imagehash will use sensible defaults for others
            kwargs: Dict[str, Any] = {}
            if min_segment_size is not None:
                kwargs["min_segment_size"] = min_segment_size
            if segmentation_image_size is not None:
                kwargs["segmentation_image_size"] = segmentation_image_size
            return imagehash.crop_resistant_hash(im, **kwargs)
        else:
            raise ValueError(
                "Unsupported algorithm. Use one of: average, phash, dhash, dhash_vertical, whash, colorhash, crop_resistant"
            )


@mcp.tool()
def hash_image(
    image_path: str,
    algorithm: Literal[
        "average",
        "phash",
        "dhash",
        "dhash_vertical",
        "whash",
        "colorhash",
        "crop_resistant",
    ] = "average",
    hash_size: int = 8,
    highfreq_factor: int = 4,
    binbits: int = 3,
    whash_mode: Literal["haar", "db4"] = "haar",
    remove_max_haar_ll: bool = True,
    image_scale: Optional[int] = None,
    min_segment_size: Optional[int] = None,
    segmentation_image_size: Optional[int] = None,
) -> dict:
    """
    Compute an image hash using the specified algorithm and return it as a hex string.

    - For average/phash/dhash/dhash_vertical/whash: use hash_size (default 8).
    - For phash: highfreq_factor controls frequency detail (default 4).
    - For whash: you can set whash_mode ('haar' or 'db4'), image_scale, and remove_max_haar_ll.
    - For colorhash: use binbits (default 3).
    - For crop_resistant: optionally set min_segment_size and segmentation_image_size.
    """
    hv = _compute_hash(
        image_path=image_path,
        algorithm=algorithm,
        hash_size=hash_size,
        highfreq_factor=highfreq_factor,
        binbits=binbits,
        whash_mode=whash_mode,
        remove_max_haar_ll=remove_max_haar_ll,
        image_scale=image_scale,
        min_segment_size=min_segment_size,
        segmentation_image_size=segmentation_image_size,
    )
    return {
        "algorithm": algorithm,
        "image_path": os.path.abspath(image_path),
        "hash": str(hv),
    }


@mcp.tool()
def compare_images(
    image_path_a: str,
    image_path_b: str,
    algorithm: Literal[
        "average",
        "phash",
        "dhash",
        "dhash_vertical",
        "whash",
        "colorhash",
        "crop_resistant",
    ] = "average",
    hash_size: int = 8,
    highfreq_factor: int = 4,
    binbits: int = 3,
    whash_mode: Literal["haar", "db4"] = "haar",
    remove_max_haar_ll: bool = True,
    image_scale: Optional[int] = None,
    min_segment_size: Optional[int] = None,
    segmentation_image_size: Optional[int] = None,
    threshold: Optional[float] = None,
) -> dict:
    """
    Compute two image hashes with the same algorithm and return their distance.
    The distance is the hash 'difference' (often Hamming distance). For some algorithms it may be a float.

    If threshold is provided, also return 'similar' = (distance <= threshold).
    """
    h1 = _compute_hash(
        image_path=image_path_a,
        algorithm=algorithm,
        hash_size=hash_size,
        highfreq_factor=highfreq_factor,
        binbits=binbits,
        whash_mode=whash_mode,
        remove_max_haar_ll=remove_max_haar_ll,
        image_scale=image_scale,
        min_segment_size=min_segment_size,
        segmentation_image_size=segmentation_image_size,
    )
    h2 = _compute_hash(
        image_path=image_path_b,
        algorithm=algorithm,
        hash_size=hash_size,
        highfreq_factor=highfreq_factor,
        binbits=binbits,
        whash_mode=whash_mode,
        remove_max_haar_ll=remove_max_haar_ll,
        image_scale=image_scale,
        min_segment_size=min_segment_size,
        segmentation_image_size=segmentation_image_size,
    )
    # Distance may be int-like or float-like depending on algorithm
    try:
        distance_val = h1 - h2
    except Exception as e:
        raise RuntimeError(f"Failed to compute distance between hashes: {e}")

    # Normalize to a JSON-serializable number
    try:
        distance_num = int(distance_val)
    except Exception:
        distance_num = float(distance_val)

    result = {
        "algorithm": algorithm,
        "image_a": os.path.abspath(image_path_a),
        "image_b": os.path.abspath(image_path_b),
        "hash_a": str(h1),
        "hash_b": str(h2),
        "distance": distance_num,
    }
    if threshold is not None:
        result["similar"] = distance_num <= threshold
        result["threshold"] = threshold
    return result


@mcp.tool()
def hamming_distance(hash_hex_a: str, hash_hex_b: str) -> dict:
    """
    Compute the distance between two hex-encoded hashes for single-hash types
    (average_hash, phash, dhash, whash). For colorhash or crop_resistant hashes,
    use compare_images instead.
    """
    try:
        h1 = imagehash.hex_to_hash(hash_hex_a)
        h2 = imagehash.hex_to_hash(hash_hex_b)
    except Exception as e:
        raise ValueError(
            "Could not parse one or both hashes with hex_to_hash. "
            "Only single-hash types (average/phash/dhash/whash) are supported here. "
            f"Error: {e}"
        )
    try:
        dist = h1 - h2
    except Exception as e:
        raise RuntimeError(f"Failed to compute distance: {e}")
    try:
        distance_num = int(dist)
    except Exception:
        distance_num = float(dist)
    return {"distance": distance_num}


if __name__ == "__main__":
    # Self-test: generate a tiny image, hash it, and ensure we got a non-empty hash string.
    from PIL import ImageDraw

    test_path = os.path.abspath("imagehash_mcp_selftest.png")
    # Create a simple offline image
    img = Image.new("RGB", (16, 16), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, 12, 12], fill=(0, 0, 0))
    img.save(test_path)

    out = hash_image(test_path, algorithm="average")
    if not out or not isinstance(out, dict) or not out.get("hash"):
        raise SystemExit("Self-test failed: empty hash result")
    # Also run a trivial compare to ensure distance pathway works
    cmp_out = compare_images(test_path, test_path, algorithm="average")
    if not cmp_out or "distance" not in cmp_out:
        raise SystemExit("Self-test failed: compare_images returned no distance")
    print("Self-test OK:", out["hash"], "distance", cmp_out["distance"])