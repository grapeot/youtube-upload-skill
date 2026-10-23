"""Optional duration probe with ffprobe. Absent ffprobe is not an error."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def video_duration(path: Path) -> float | None:
    exe = shutil.which("ffprobe")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except (subprocess.SubprocessError, OSError, KeyError, ValueError, TypeError):
        return None
