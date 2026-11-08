import base64
import os
from typing import List, Dict, Optional

from mcp.server.fastmcp import FastMCP, Image  # Image not used here but available for future extensions

mcp = FastMCP("ebooklib-tools")

# Lazily import to avoid import on workers that only load this file to inspect metadata
def _load_ebooklib():
    import ebooklib
    from ebooklib import epub
    return ebooklib, epub

def _type_code_to_name(code: int) -> str:
    ebooklib, _ = _load_ebooklib()
    # Build reverse map from ebooklib.ITEM_* constants to human-friendly names
    mapping = {}
    for name in dir(ebooklib):
        if name.startswith("ITEM_"):
            val = getattr(ebooklib, name)
            try:
                mapping[int(val)] = name[5:].lower()
            except Exception:
                pass
    return mapping.get(int(code), f"unknown({code})")

def _metadata_values(book, ns: str, name: str) -> List[str]:
    vals = []
    try:
        for v, _attrs in book.get_metadata(ns, name):
            vals.append(v)
    except Exception:
        pass
    return vals

@mcp.tool()
def read_epub_metadata(epub_path: str) -> Dict:
    """
    Read basic metadata from an EPUB file.
    Returns dict with titles, languages, identifiers, authors, publishers and a count of items by type.
    """
    if not os.path.isfile(epub_path):
        raise FileNotFoundError(f"EPUB not found: {epub_path}")
    ebooklib, epub = _load_ebooklib()
    book = epub.read_epub(epub_path)

    titles = _metadata_values(book, "DC", "title")
    languages = _metadata_values(book, "DC", "language")
    identifiers = _metadata_values(book, "DC", "identifier")
    authors = _metadata_values(book, "DC", "creator")
    publishers = _metadata_values(book, "DC", "publisher")

    counts: Dict[str, int] = {}
    for item in book.get_items():
        tname = _type_code_to_name(item.get_type())
        counts[tname] = counts.get(tname, 0) + 1

    return {
        "path": os.path.abspath(epub_path),
        "titles": titles,
        "languages": languages,
        "identifiers": identifiers,
        "authors": authors,
        "publishers": publishers,
        "item_counts": counts,
    }

@mcp.tool()
def list_epub_items(epub_path: str, item_type: str = "all") -> List[Dict]:
    """
    List items in an EPUB. item_type can be one of: all, document, image, style, navigation.
    Returns a list of dicts with id, name, type.
    """
    if not os.path.isfile(epub_path):
        raise FileNotFoundError(f"EPUB not found: {epub_path}")
    ebooklib, epub = _load_ebooklib()
    book = epub.read_epub(epub_path)

    type_map = {
        "document": getattr(ebooklib, "ITEM_DOCUMENT", None),
        "image": getattr(ebooklib, "ITEM_IMAGE", None),
        "style": getattr(ebooklib, "ITEM_STYLE", None),
        "navigation": getattr(ebooklib, "ITEM_NAVIGATION", None),
        "nav": getattr(ebooklib, "ITEM_NAVIGATION", None),
        "all": None,
    }
    if item_type not in type_map:
        raise ValueError(f"Unsupported item_type '{item_type}'. Use one of: {', '.join(sorted(type_map.keys()))}")
    want = type_map[item_type]

    results = []
    for item in book.get_items():
        if want is not None and item.get_type() != want:
            continue
        # Robustly fetch id
        try:
            iid = item.get_id()  # preferred
        except Exception:
            iid = getattr(item, "id", None)
        results.append(
            {
                "id": iid,
                "name": item.get_name(),
                "type": _type_code_to_name(item.get_type()),
            }
        )
    return results

@mcp.tool()
def extract_epub_images(epub_path: str, output_dir: Optional[str] = None) -> List[str]:
    """
    Extract all images from an EPUB to output_dir (created if missing).
    Returns a list of absolute file paths to the extracted images.
    """
    import io

    if not os.path.isfile(epub_path):
        raise FileNotFoundError(f"EPUB not found: {epub_path}")
    ebooklib, epub = _load_ebooklib()
    book = epub.read_epub(epub_path)

    if output_dir is None:
        base = os.path.splitext(os.path.basename(epub_path))[0]
        output_dir = os.path.join(os.getcwd(), f"extracted_images_{base}")
    os.makedirs(output_dir, exist_ok=True)

    out_paths: List[str] = []
    for item in book.get_items():
        if item.get_type() == getattr(ebooklib, "ITEM_IMAGE", -1):
            fname = os.path.basename(item.get_name())
            out_path = os.path.abspath(os.path.join(output_dir, fname))
            with open(out_path, "wb") as f:
                f.write(item.get_content())
            out_paths.append(out_path)
    return out_paths

# A tiny 1x1 transparent GIF for optional embedding
_SAMPLE_GIF_B64 = b"R0lGODlhAQABAIAAAP///wAAACwAAAAAAQABAAACAkQBADs="

@mcp.tool()
def create_simple_epub(
    output_path: str,
    title: str,
    language: str = "en",
    author: Optional[str] = None,
    chapters: Optional[List[Dict]] = None,
    include_sample_image: bool = False,
) -> str:
    """
    Create a simple EPUB file.
    - output_path: where to write the .epub
    - title: book title
    - language: language code (default 'en')
    - author: optional single author
    - chapters: optional list of dicts with keys: title, file_name, content, lang (optional)
    - include_sample_image: if True, embeds a tiny 1x1 GIF and references it in the last chapter
    Returns absolute path to the written EPUB.
    """
    ebooklib, epub = _load_ebooklib()

    book = epub.EpubBook()
    book.set_title(title)
    book.set_language(language)
    if author:
        book.add_author(author)

    chapter_items = []
    if chapters:
        for ch in chapters:
            ch_title = ch.get("title", "Chapter")
            ch_fname = ch.get("file_name", f"{ch_title.lower().replace(' ', '_')}.xhtml")
            ch_lang = ch.get("lang", language)
            ch_content = ch.get("content", f"<h1>{ch_title}</h1>")
            c = epub.EpubHtml(title=ch_title, file_name=ch_fname, lang=ch_lang)
            c.content = ch_content
            book.add_item(c)
            chapter_items.append(c)
    else:
        c = epub.EpubHtml(title="Introduction", file_name="introduction.xhtml", lang=language)
        c.content = f"<h1>{title}</h1><p>Generated by ebooklib-tools.</p>"
        book.add_item(c)
        chapter_items.append(c)

    # Optionally embed a tiny image
    if include_sample_image:
        gif_bytes = base64.b64decode(_SAMPLE_GIF_B64)
        img = epub.EpubImage(
            uid="image_1",
            file_name="static/pixel.gif",
            media_type="image/gif",
            content=gif_bytes,
        )
        book.add_item(img)
        # Reference it in the last chapter
        chapter_items[-1].content = str(chapter_items[-1].content) + '<p><img alt="pixel" src="static/pixel.gif"/></p>'

    # Spine and default navigation
    book.spine = ["nav"] + chapter_items
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())

    # Optional simple stylesheet for better rendering
    nav_css = epub.EpubItem(
        uid="style_nav",
        file_name="style/nav.css",
        media_type="text/css",
        content="BODY { color: black; background-color: white; }",
    )
    book.add_item(nav_css)

    out_abs = os.path.abspath(output_path)
    out_dir = os.path.dirname(out_abs)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    epub.write_epub(out_abs, book)
    return out_abs

if __name__ == "__main__":
    # Offline self-test: create a tiny EPUB (with a 1x1 embedded image), read metadata, list items, and extract images.
    test_epub = "selftest_book.epub"
    created = create_simple_epub(
        output_path=test_epub,
        title="Selftest Book",
        language="en",
        author="Tester",
        chapters=[{"title": "Intro", "file_name": "intro.xhtml", "content": "<h1>Hello</h1><p>World</p>"}],
        include_sample_image=True,
    )
    if not (created and os.path.isfile(created) and os.path.getsize(created) > 0):
        raise RuntimeError("Failed to create EPUB during self-test")

    meta = read_epub_metadata(created)
    if not meta or not any(meta.get(k) for k in ("titles", "languages", "identifiers", "authors", "publishers", "item_counts")):
        raise RuntimeError("Metadata read returned empty result during self-test")

    items = list_epub_items(created)
    if not items:
        raise RuntimeError("Listing items returned empty result during self-test")

    extracted = extract_epub_images(created)
    if not extracted:
        raise RuntimeError("Expected at least one extracted image during self-test")

    print("Self-test completed successfully.")
else:
    mcp.run()