import os
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP

# MCP server instance
mcp = FastMCP("markdownify")


def _build_options_kwargs(
    strip: Optional[List[str]] = None,
    convert: Optional[List[str]] = None,
    autolinks: Optional[bool] = None,
    default_title: Optional[bool] = None,
    heading_style: Optional[str] = None,
    bullets: Optional[Union[str, List[str]]] = None,
    strong_em_symbol: Optional[str] = None,
    sub_symbol: Optional[str] = None,
    sup_symbol: Optional[str] = None,
    newline_style: Optional[str] = None,
    code_language: Optional[str] = None,
    escape_asterisks: Optional[bool] = None,
    escape_underscores: Optional[bool] = None,
    escape_misc: Optional[bool] = None,
    keep_inline_images_in: Optional[List[str]] = None,
    table_infer_header: Optional[bool] = None,
    wrap: Optional[bool] = None,
    wrap_width: Optional[Union[int, None]] = None,
    strip_document: Optional[str] = None,
    strip_pre: Optional[str] = None,
    bs4_options: Optional[Union[str, List[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build kwargs dict for markdownify based on provided (non-None) options.
    """
    opts: Dict[str, Any] = {}
    if strip is not None:
        opts["strip"] = strip
    if convert is not None:
        opts["convert"] = convert
    if autolinks is not None:
        opts["autolinks"] = autolinks
    if default_title is not None:
        opts["default_title"] = default_title
    if heading_style is not None:
        opts["heading_style"] = heading_style
    if bullets is not None:
        opts["bullets"] = bullets
    if strong_em_symbol is not None:
        opts["strong_em_symbol"] = strong_em_symbol
    if sub_symbol is not None:
        opts["sub_symbol"] = sub_symbol
    if sup_symbol is not None:
        opts["sup_symbol"] = sup_symbol
    if newline_style is not None:
        opts["newline_style"] = newline_style
    if code_language is not None:
        opts["code_language"] = code_language
    if escape_asterisks is not None:
        opts["escape_asterisks"] = escape_asterisks
    if escape_underscores is not None:
        opts["escape_underscores"] = escape_underscores
    if escape_misc is not None:
        opts["escape_misc"] = escape_misc
    if keep_inline_images_in is not None:
        opts["keep_inline_images_in"] = keep_inline_images_in
    if table_infer_header is not None:
        opts["table_infer_header"] = table_infer_header
    if wrap is not None:
        opts["wrap"] = wrap
    if wrap_width is not None:
        opts["wrap_width"] = wrap_width
    if strip_document is not None:
        opts["strip_document"] = strip_document
    if strip_pre is not None:
        opts["strip_pre"] = strip_pre
    if bs4_options is not None:
        opts["bs4_options"] = bs4_options
    return opts


@mcp.tool()
def html_to_markdown(
    html: str,
    strip: Optional[List[str]] = None,
    convert: Optional[List[str]] = None,
    autolinks: Optional[bool] = None,
    default_title: Optional[bool] = None,
    heading_style: Optional[str] = None,
    bullets: Optional[Union[str, List[str]]] = None,
    strong_em_symbol: Optional[str] = None,
    sub_symbol: Optional[str] = None,
    sup_symbol: Optional[str] = None,
    newline_style: Optional[str] = None,
    code_language: Optional[str] = None,
    escape_asterisks: Optional[bool] = None,
    escape_underscores: Optional[bool] = None,
    escape_misc: Optional[bool] = None,
    keep_inline_images_in: Optional[List[str]] = None,
    table_infer_header: Optional[bool] = None,
    wrap: Optional[bool] = None,
    wrap_width: Optional[Union[int, None]] = None,
    strip_document: Optional[str] = None,
    strip_pre: Optional[str] = None,
    bs4_options: Optional[Union[str, List[str], Dict[str, Any]]] = None,
) -> str:
    """
    Convert an HTML string to Markdown using python-markdownify.
    All options mirror the library's documented options.
    """
    from markdownify import markdownify as md  # Imported here to ensure package is present

    opts = _build_options_kwargs(
        strip=strip,
        convert=convert,
        autolinks=autolinks,
        default_title=default_title,
        heading_style=heading_style,
        bullets=bullets,
        strong_em_symbol=strong_em_symbol,
        sub_symbol=sub_symbol,
        sup_symbol=sup_symbol,
        newline_style=newline_style,
        code_language=code_language,
        escape_asterisks=escape_asterisks,
        escape_underscores=escape_underscores,
        escape_misc=escape_misc,
        keep_inline_images_in=keep_inline_images_in,
        table_infer_header=table_infer_header,
        wrap=wrap,
        wrap_width=wrap_width,
        strip_document=strip_document,
        strip_pre=strip_pre,
        bs4_options=bs4_options,
    )
    return md(html, **opts)


@mcp.tool()
def file_to_markdown(
    file_path: str,
    encoding: str = "utf-8",
    strip: Optional[List[str]] = None,
    convert: Optional[List[str]] = None,
    autolinks: Optional[bool] = None,
    default_title: Optional[bool] = None,
    heading_style: Optional[str] = None,
    bullets: Optional[Union[str, List[str]]] = None,
    strong_em_symbol: Optional[str] = None,
    sub_symbol: Optional[str] = None,
    sup_symbol: Optional[str] = None,
    newline_style: Optional[str] = None,
    code_language: Optional[str] = None,
    escape_asterisks: Optional[bool] = None,
    escape_underscores: Optional[bool] = None,
    escape_misc: Optional[bool] = None,
    keep_inline_images_in: Optional[List[str]] = None,
    table_infer_header: Optional[bool] = None,
    wrap: Optional[bool] = None,
    wrap_width: Optional[Union[int, None]] = None,
    strip_document: Optional[str] = None,
    strip_pre: Optional[str] = None,
    bs4_options: Optional[Union[str, List[str], Dict[str, Any]]] = None,
) -> str:
    """
    Convert an HTML file to Markdown and return the Markdown text.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding=encoding) as f:
        html = f.read()

    return html_to_markdown(
        html=html,
        strip=strip,
        convert=convert,
        autolinks=autolinks,
        default_title=default_title,
        heading_style=heading_style,
        bullets=bullets,
        strong_em_symbol=strong_em_symbol,
        sub_symbol=sub_symbol,
        sup_symbol=sup_symbol,
        newline_style=newline_style,
        code_language=code_language,
        escape_asterisks=escape_asterisks,
        escape_underscores=escape_underscores,
        escape_misc=escape_misc,
        keep_inline_images_in=keep_inline_images_in,
        table_infer_header=table_infer_header,
        wrap=wrap,
        wrap_width=wrap_width,
        strip_document=strip_document,
        strip_pre=strip_pre,
        bs4_options=bs4_options,
    )


def _self_test() -> None:
    """
    Offline self-test: convert a tiny HTML snippet and ensure we get non-empty Markdown back.
    """
    from markdownify import markdownify as md  # Verify import works

    sample_html = "<b>Yay</b> <a href='http://github.com'>GitHub</a>"
    out_direct = md(sample_html)
    out_tool = html_to_markdown(sample_html)

    if not out_direct or not out_tool:
        raise RuntimeError("markdownify self-test did not produce output")
    # Basic sanity: outputs should be strings
    if not isinstance(out_direct, str) or not isinstance(out_tool, str):
        raise RuntimeError("markdownify self-test outputs are not strings")


if __name__ == "__main__":
    _self_test()
    print("SELF-TEST PASSED")