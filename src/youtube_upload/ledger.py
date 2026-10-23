"""Upload ledger: sha256 duplicate guard and resumable-upload state.

Stored as JSON next to the token (0600). Entries hold the resumable session URL, which is
never included in any command output: `public_view` strips it.

Every change is a locked read-modify-write (flock on a sidecar lock file): the file is
reloaded under the lock and only the changed entry is replaced, so concurrent runs cannot
erase each other's records.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import ensure_private_dir

UPLOADING = "uploading"   # session started, video not complete
UPLOADED = "uploaded"     # videos.insert returned a video ID
EXPIRED = "expired"       # the upload session expired before completion

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
        self.entries: list[dict[str, Any]] = self._read()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"upload ledger {self.path} is not valid JSON ({e}); fix or move it aside") from None
        entries = list(data.get("uploads", [])) if isinstance(data, dict) else []
        for e in entries:
            e.setdefault("entry_id", e.get("sha256", "") + ":" + str(e.get("created_at", "")))
        return entries

    @contextmanager
    def _locked(self) -> Iterator[None]:
        ensure_private_dir(self.path.parent)
        lock = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, entries: list[dict[str, Any]]) -> None:
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "uploads": entries}, fh, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def _commit(self, entry: dict[str, Any]) -> None:
        """Replace (or append) this entry in the on-disk ledger under the lock."""
        with self._locked():
            disk = self._read()
            for i, e in enumerate(disk):
                if e.get("entry_id") == entry["entry_id"]:
                    disk[i] = entry
                    break
            else:
                disk.append(entry)
            self._write(disk)
            self.entries = disk

    def save(self) -> None:  # kept for callers that edited entries in place
        with self._locked():
            self._write(self.entries)

    def latest(self, sha256: str) -> dict[str, Any] | None:
        for entry in reversed(self._read()):
            if entry.get("sha256") == sha256:
                return entry
        return None

    def start(self, sha256: str, video: Path, size: int, title: str, visibility: str) -> dict[str, Any]:
        entry = {
            "entry_id": uuid.uuid4().hex, "sha256": sha256, "video": str(video), "size": size, "title": title,
            "visibility": visibility, "state": UPLOADING, "session_uri": None, "video_id": None, "steps": {},
            "created_at": _now(), "updated_at": _now(),
        }
        self._commit(entry)
        return entry

    def update(self, entry: dict[str, Any], **changes: Any) -> None:
        entry.update(changes)
        entry["updated_at"] = _now()
        self._commit(entry)

    def set_step(self, entry: dict[str, Any], step: str, status: str) -> None:
        entry.setdefault("steps", {})[step] = status
        entry["updated_at"] = _now()
        self._commit(entry)


def public_view(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    view = {k: v for k, v in entry.items() if k not in _PRIVATE_FIELDS}
    view["has_session"] = bool(entry.get("session_uri"))
    return view
