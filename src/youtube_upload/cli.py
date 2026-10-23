"""yt-upload command-line entry. Every subcommand prints exactly one JSON object to stdout."""

from __future__ import annotations

import json
import sys

from . import __version__


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        print(json.dumps({"ok": True, "version": __version__}))
        sys.exit(0)
    print(json.dumps({"ok": False, "error": "not implemented yet"}))
    sys.exit(1)
