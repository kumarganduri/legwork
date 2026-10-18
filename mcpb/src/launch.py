"""Starts Legwork's hub from the Claude Desktop extension (MCPB bundle).

The host passes the user's settings as arguments: the network switch
first, then each folder they picked as its own argument (a list setting is
expanded into separate arguments), so

    launch.py true ~/Downloads ~/Documents

becomes `legwork hub --allow-read ~/Downloads --allow-read ~/Documents
--allow-net`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _expand(folder: str) -> str:
    """Claude Desktop passed the default folder through as the literal
    "${HOME}/Downloads" (2026-10-18), so the places a default may name are
    expanded here."""
    home = str(Path.home())
    for name, path in {"HOME": home, "DESKTOP": f"{home}/Desktop", "DOCUMENTS": f"{home}/Documents",
                       "DOWNLOADS": f"{home}/Downloads"}.items():
        folder = folder.replace("${" + name + "}", path)
    return folder


def hub_args(argv: list[str]) -> list[str]:
    net, *folders = argv or ["false"]
    args = ["hub"]
    for folder in map(_expand, folders):
        # A setting the host didn't fill in arrives as the literal
        # placeholder: it's no folder, and never a grant.
        if folder.strip() and "${" not in folder:
            args += ["--allow-read", folder]
    if net.strip().lower() == "true":
        args.append("--allow-net")
    return args


if __name__ == "__main__":
    from legwork.cli import main

    sys.exit(main(hub_args(sys.argv[1:])))
