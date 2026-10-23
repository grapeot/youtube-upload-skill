"""OAuth client JSON files: detect the kind, find downloaded ones, install with a backup.

Only metadata is ever reported (kind, project ID, file name, modification time). The client
secret itself is never read into output.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import Home, ensure_private_dir

DOWNLOAD_GLOB = "client_secret_*.json"


def describe(path: Path) -> dict[str, Any]:
    """{'kind': 'installed'|'web'|None, 'project_id', 'client_id_suffix'} without secrets."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"kind": None, "project_id": None}
    if not isinstance(data, dict):
        return {"kind": None, "project_id": None}
    for kind in ("installed", "web"):
        block = data.get(kind)
        if isinstance(block, dict):
            client_id = str(block.get("client_id", ""))
            return {
                "kind": kind,
                "project_id": block.get("project_id"),
                # the public half of the client ID is enough to tell clients apart
                "client_id_prefix": client_id.split("-", 1)[0] + "-..." if client_id else None,
            }
    return {"kind": None, "project_id": None}


def find_downloads(directory: Path | None = None) -> list[dict[str, Any]]:
    """Downloaded OAuth client files, newest first."""
    d = directory or Path.home() / "Downloads"
    if not d.is_dir():
        return []
    out = []
    for p in d.glob(DOWNLOAD_GLOB):
        if p.is_file():
            out.append({"path": str(p), "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
                        **describe(p)})
    return sorted(out, key=lambda x: x["modified"], reverse=True)


class InstallError(ValueError):
    pass


def install(home: Home, source: Path, now: datetime | None = None) -> dict[str, Any]:
    """Copy a Desktop client JSON to <home>/client_secret.json (0600). An existing file is kept as a backup."""
    info = describe(source)
    if info["kind"] != "installed":
        raise InstallError(
            f"{source.name} is not a Desktop app OAuth client (top-level key {info['kind']!r}); "
            "create a client with application type 'Desktop app'"
        )
    ensure_private_dir(home.root)
    backup = None
    if home.client_secret.exists():
        if home.client_secret.read_bytes() == source.read_bytes():
            home.client_secret.chmod(0o600)
            return {"installed": str(home.client_secret), "backup": None, "unchanged": True, **info}
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = home.root / f"client_secret.json.bak-{stamp}"
        n = 1
        while backup.exists():
            backup = home.root / f"client_secret.json.bak-{stamp}-{n}"
            n += 1
        os.replace(home.client_secret, backup)
        backup.chmod(0o600)
    tmp = home.client_secret.with_name("client_secret.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(source.read_bytes())
    os.chmod(tmp, 0o600)
    os.replace(tmp, home.client_secret)
    after = describe(home.client_secret)
    if after["kind"] != "installed":  # pragma: no cover - defensive readback
        raise InstallError("installed file does not read back as a Desktop client")
    return {"installed": str(home.client_secret), "backup": str(backup) if backup else None, "unchanged": False, **after}
