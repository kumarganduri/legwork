import os
import time
from typing import List, Union, Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("plotly-tools")


@mcp.tool()
def bar_chart_html(
    x: List[Union[str, float, int]],
    y: List[Union[float, int]],
    title: Optional[str] = None,
) -> dict:
    """
    Create an interactive bar chart with plotly and save it as a self-contained HTML file.

    Args:
        x: Categorical or numeric x-axis values.
        y: Numeric y-axis values. Must be the same length as x.
        title: Optional chart title.

    Returns:
        {"html_path": absolute path of the chart (opens offline in any browser), "size_bytes": ...}

    Raises:
        ValueError: If x and y lengths differ or are empty.
    """
    if len(x) != len(y):
        raise ValueError("x and y must be the same length.")
    if len(x) == 0:
        raise ValueError("x and y must be non-empty.")

    # Import locally to avoid importing heavier optional modules at load time.
    import plotly.graph_objects as go

    fig = go.Figure(data=[go.Bar(x=x, y=y)])
    if title:
        fig.update_layout(title=title)

    # plotly.js is embedded so the file works offline; it's ~3.5 MB, so it goes
    # to a file in the tool's folder rather than into the client's context.
    path = os.path.abspath(f"chart-{int(time.time() * 1000)}.html")
    fig.write_html(path, include_plotlyjs=True)
    return {"html_path": path, "size_bytes": os.path.getsize(path)}


if __name__ == "__main__":
    # Offline self-test: verify plotly is importable and reports a version,
    # and that our tool returns a non-empty HTML string for a tiny chart.
    import plotly  # noqa: F401

    version = getattr(plotly, "__version__", "")
    if not version:
        raise RuntimeError("Plotly version string is empty or missing.")

    sample = bar_chart_html(x=["a", "b", "c"], y=[1, 3, 2], title="Test")
    if not sample.get("size_bytes"):
        raise RuntimeError("bar_chart_html did not write a chart.")

    print("Self-test passed.")