"""Upload ledger: sha256 duplicate guard and resumable-upload state.

Stored as JSON next to the token (0600). Entries hold the resumable session URL, which is
never included in any command output: `public_view` strips it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import write_private

UPLOADING = "uploading"   # session started, video not complete
UPLOADED = "uploaded"     # videos.insert returned a video ID

_PRIVATE_FIELDS = ("session_uri",)


def file_sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict[str, Any]] = []
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"upload ledger {path} is not valid JSON ({e}); fix or move it aside") from None
            self.entries = list(data.get("uploads", [])) if isinstance(data, dict) else []

    def save(self) -> None:
        write_private(self.path, json.dumps({"version": 1, "uploads": self.entries}, ensure_ascii=False, indent=1))

    def latest(self, sha256: str) -> dict[str, Any] | None:
        for entry in reversed(self.entries):
            if entry.get("sha256") == sha256:
                return entry
        return None

    def start(self, sha256: str, video: Path, size: int, title: str, visibility: str) -> dict[str, Any]:
        entry = {
            "sha256": sha256, "video": str(video), "size": size, "title": title, "visibility": visibility,
            "state": UPLOADING, "session_uri": None, "video_id": None, "steps": {},
            "created_at": _now(), "updated_at": _now(),
        }
        self.entries.append(entry)
        self.save()
        return entry

    def update(self, entry: dict[str, Any], **changes: Any) -> None:
        entry.update(changes)
        entry["updated_at"] = _now()
        self.save()

    def set_step(self, entry: dict[str, Any], step: str, status: str) -> None:
        entry.setdefault("steps", {})[step] = status
        entry["updated_at"] = _now()
        self.save()


def public_view(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    view = {k: v for k, v in entry.items() if k not in _PRIVATE_FIELDS}
    view["has_session"] = bool(entry.get("session_uri"))
    return view
