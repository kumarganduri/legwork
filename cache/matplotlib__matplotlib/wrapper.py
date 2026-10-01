import os
import time
from typing import List, Optional

# Ensure headless rendering for all environments before importing pyplot
import matplotlib
matplotlib.use("Agg")

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("matplotlib-tools")


def _ensure_output_dir() -> str:
    out_dir = os.path.abspath(os.path.join(".", "outputs"))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _figure_size_inches(width: int, height: int, dpi: int) -> tuple:
    # Convert pixels to inches for Matplotlib figure size
    return (max(1, width) / dpi, max(1, height) / dpi)


@mcp.tool()
def matplotlib_version() -> str:
    """
    Return the installed Matplotlib version.
    """
    return matplotlib.__version__


@mcp.tool()
def plot_line(
    y: List[float],
    x: Optional[List[float]] = None,
    title: Optional[str] = None,
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    grid: bool = True,
    width: int = 800,
    height: int = 600,
    dpi: int = 100,
    filename: Optional[str] = None,
) -> Image:
    """
    Create a line plot and return the saved PNG image.
    """
    from matplotlib import pyplot as plt  # import after setting backend

    if x is not None and len(x) != len(y):
        raise ValueError("x and y must have the same length")

    out_dir = _ensure_output_dir()
    if not filename:
        filename = f"line_{int(time.time()*1000)}.png"
    out_path = os.path.abspath(os.path.join(out_dir, filename))

    fig = plt.figure(figsize=_figure_size_inches(width, height, dpi), dpi=dpi)
    try:
        if x is None:
            plt.plot(y, marker="o")
        else:
            plt.plot(x, y, marker="o")
        if title:
            plt.title(title)
        if xlabel:
            plt.xlabel(xlabel)
        if ylabel:
            plt.ylabel(ylabel)
        if grid:
            plt.grid(True, linestyle="--", alpha=0.5)
        plt.tight_layout()
        plt.savefig(out_path, format="png")
    finally:
        plt.close(fig)

    return Image(path=out_path)


@mcp.tool()
def scatter(
    x: List[float],
    y: List[float],
    title: Optional[str] = None,
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    grid: bool = True,
    width: int = 800,
    height: int = 600,
    dpi: int = 100,
    filename: Optional[str] = None,
) -> Image:
    """
    Create a scatter plot and return the saved PNG image.
    """
    from matplotlib import pyplot as plt  # import after setting backend

    if len(x) != len(y):
        raise ValueError("x and y must have the same length")

    out_dir = _ensure_output_dir()
    if not filename:
        filename = f"scatter_{int(time.time()*1000)}.png"
    out_path = os.path.abspath(os.path.join(out_dir, filename))

    fig = plt.figure(figsize=_figure_size_inches(width, height, dpi), dpi=dpi)
    try:
        plt.scatter(x, y, alpha=0.8)
        if title:
            plt.title(title)
        if xlabel:
            plt.xlabel(xlabel)
        if ylabel:
            plt.ylabel(ylabel)
        if grid:
            plt.grid(True, linestyle="--", alpha=0.5)
        plt.tight_layout()
        plt.savefig(out_path, format="png")
    finally:
        plt.close(fig)

    return Image(path=out_path)


@mcp.tool()
def histogram(
    values: List[float],
    bins: int = 10,
    title: Optional[str] = None,
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    grid: bool = True,
    width: int = 800,
    height: int = 600,
    dpi: int = 100,
    filename: Optional[str] = None,
) -> Image:
    """
    Create a histogram and return the saved PNG image.
    """
    from matplotlib import pyplot as plt  # import after setting backend

    if bins <= 0:
        raise ValueError("bins must be a positive integer")

    out_dir = _ensure_output_dir()
    if not filename:
        filename = f"hist_{int(time.time()*1000)}.png"
    out_path = os.path.abspath(os.path.join(out_dir, filename))

    fig = plt.figure(figsize=_figure_size_inches(width, height, dpi), dpi=dpi)
    try:
        plt.hist(values, bins=bins, edgecolor="black", alpha=0.8)
        if title:
            plt.title(title)
        if xlabel:
            plt.xlabel(xlabel)
        if ylabel:
            plt.ylabel(ylabel)
        if grid:
            plt.grid(True, linestyle="--", alpha=0.5)
        plt.tight_layout()
        plt.savefig(out_path, format="png")
    finally:
        plt.close(fig)

    return Image(path=out_path)


if __name__ == "__main__":
    # Self-test: verify version is non-empty and a small plot can be saved.
    ver = matplotlib_version()
    if not ver:
        raise RuntimeError("Matplotlib version is empty")

    img = plot_line(y=[1, 3, 2], title="Self-test Line Plot", xlabel="Index", ylabel="Value")
    if not isinstance(img, Image):
        raise RuntimeError("plot_line did not return an Image")
    if not getattr(img, "path", None) or not os.path.exists(img.path):
        raise RuntimeError("Output image path missing or file not created")

    print(f"Self-test OK with Matplotlib {ver}, image at: {img.path}")