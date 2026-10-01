import os
import json
from typing import Any, Dict, List, Optional, Tuple, Union

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("rapidocr_mcp")

_engine = None  # Lazy-initialized RapidOCR engine


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR  # Import here to keep self-test lightweight
        _engine = RapidOCR()
    return _engine


def _extract_lines_from_result(result: Any) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """
    Attempt to normalize RapidOCR result into a list of lines with text/score/box, plus elapsed if present.
    This handles multiple potential return structures across versions.
    """
    elapsed = None
    core = result

    # Some versions may return (core, elapsed)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], (int, float)):
        core, elapsed = result

    lines: List[Dict[str, Any]] = []

    # Case 1: Rich result object with expected attributes
    attr_sets = [
        ("boxes", "texts", "scores"),
        ("dt_boxes", "rec_texts", "rec_scores"),
        ("boxes", "texts", None),
        ("dt_boxes", "rec_texts", None),
    ]
    for a_box, a_text, a_score in attr_sets:
        if hasattr(core, a_box) and hasattr(core, a_text):
            boxes = getattr(core, a_box)
            texts = getattr(core, a_text)
            scores = getattr(core, a_score) if a_score and hasattr(core, a_score) else None
            for idx, txt in enumerate(list(texts) if texts is not None else []):
                line: Dict[str, Any] = {"text": txt}
                if boxes is not None and idx < len(boxes):
                    line["box"] = boxes[idx]
                if scores is not None and idx < len(scores):
                    line["score"] = scores[idx]
                lines.append(line)
            if lines:
                return lines, elapsed

    # Case 2: Object provides to_list() method
    if hasattr(core, "to_list") and callable(getattr(core, "to_list")):
        try:
            list_core = core.to_list()
            # Heuristic: each item may be [box, text, score] or similar
            for item in list_core:
                line: Dict[str, Any] = {}
                if isinstance(item, dict):
                    # Trust dictionary as-is but normalize keys if present
                    text = item.get("text") or item.get("label") or item.get("content")
                    if text is not None:
                        line["text"] = text
                    if "score" in item:
                        line["score"] = item["score"]
                    if "box" in item or "points" in item:
                        line["box"] = item.get("box") or item.get("points")
                    if not line and item:
                        # Fallback to raw item
                        line["raw"] = item
                elif isinstance(item, (list, tuple)):
                    # Common layout: [points, text, score] or [text, score]
                    if len(item) == 3 and isinstance(item[1], str):
                        line["box"] = item[0]
                        line["text"] = item[1]
                        line["score"] = item[2]
                    elif len(item) == 2 and isinstance(item[0], str):
                        line["text"] = item[0]
                        line["score"] = item[1]
                    else:
                        line["raw"] = item
                else:
                    line["raw"] = item
                lines.append(line)
            if lines:
                return lines, elapsed
        except Exception:
            pass

    # Case 3: Iterable of per-line entries (list of [box, (text, score)] or similar)
    if isinstance(core, (list, tuple)):
        for item in core:
            line: Dict[str, Any] = {}
            if isinstance(item, dict):
                text = item.get("text") or item.get("label") or item.get("content")
                if text is not None:
                    line["text"] = text
                if "score" in item:
                    line["score"] = item["score"]
                if "box" in item or "points" in item:
                    line["box"] = item.get("box") or item.get("points")
                if not line and item:
                    line["raw"] = item
            elif isinstance(item, (list, tuple)):
                # Try common patterns:
                # - [points, (text, score)]
                # - [points, text, score]
                # - (text, score)
                if len(item) == 2 and isinstance(item[1], (list, tuple)) and len(item[1]) == 2:
                    line["box"] = item[0]
                    line["text"] = item[1][0]
                    line["score"] = item[1][1]
                elif len(item) == 3 and isinstance(item[1], str):
                    line["box"] = item[0]
                    line["text"] = item[1]
                    line["score"] = item[2]
                elif len(item) == 2 and isinstance(item[0], str):
                    line["text"] = item[0]
                    line["score"] = item[1]
                else:
                    line["raw"] = item
            else:
                line["raw"] = item
            lines.append(line)
        if lines:
            return lines, elapsed

    # Case 4: As a last resort, use string form
    text_str = ""
    try:
        text_str = str(core)
    except Exception:
        text_str = ""
    if text_str:
        lines.append({"raw": text_str})

    return lines, elapsed


@mcp.tool()
def ocr_image(image_path: str, visualize: bool = False) -> Dict[str, Any]:
    """
    Run OCR on a local image using RapidOCR (ONNX Runtime).
    - image_path: Path to a local image file (read-only).
    - visualize: If True, saves a visualization image with detected boxes and recognized text.
    Returns the recognized text, each line with its confidence and box, and optional elapsed time and visualization path.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    engine = _get_engine()
    result = engine(image_path)

    # RapidOCROutput carries parallel txts/scores/boxes; returning those
    # (not the object's repr, which embeds the whole image array) keeps the
    # reply small and readable.
    txts = list(getattr(result, "txts", None) or [])
    scores = list(getattr(result, "scores", None) or [])
    boxes = getattr(result, "boxes", None)
    lines = [
        {
            "text": text,
            "score": round(float(scores[i]), 3) if i < len(scores) else None,
            "box": boxes[i].tolist() if boxes is not None and i < len(boxes) else None,
        }
        for i, text in enumerate(txts)
    ]
    elapsed = getattr(result, "elapse", None)

    vis_path = None
    if visualize:
        out_path = os.path.abspath("rapidocr_vis.png")
        # If result has a built-in visualization method, use it
        if hasattr(result, "vis") and callable(getattr(result, "vis")):
            result.vis(out_path)
            vis_path = out_path
        else:
            # No built-in visualizer exposed; skip visualization gracefully
            vis_path = None

    out: Dict[str, Any] = {"text": "\n".join(txts), "lines": lines}
    if elapsed is not None:
        out["elapsed"] = float(elapsed)
    if vis_path:
        out["visualization_path"] = vis_path
    return out


def _self_test() -> None:
    """
    Offline self-test: ensure the package is importable and RapidOCR class is present.
    Does not run any network or heavy model inference.
    """
    import importlib
    mod = importlib.import_module("rapidocr")
    RapidOCR = getattr(mod, "RapidOCR", None)
    name = getattr(RapidOCR, "__name__", "") if RapidOCR else ""
    if not name:
        raise RuntimeError("RapidOCR class not found in rapidocr package")
    # Ensure we return something non-empty to stdout
    print(f"Found class: {name}")


if __name__ == "__main__":
    _self_test()
    # Do not start the MCP server here; standalone run is just a self-test.