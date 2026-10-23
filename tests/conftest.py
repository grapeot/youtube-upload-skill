import json
from pathlib import Path

import pytest

from youtube_upload.api import build_service
from youtube_upload.paths import Home


@pytest.fixture
def media(tmp_path: Path) -> Path:
    """Synthetic placeholder files: meaningless bytes that pass the existence checks."""
    d = tmp_path / "media"
    d.mkdir()
    (d / "video.mp4").write_bytes(b"\x00\x01\x02\x03" * 64)  # 256 bytes
    (d / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12)
    (d / "subs.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    return d


@pytest.fixture
def good_meta() -> dict:
    return {
        "video": "video.mp4",
        "title": "Test title",
        "description": "First paragraph.\n\nSecond paragraph.",
        "chapters": [
            {"start": 0, "title": "Intro"},
            {"start": "01:30", "title": "Middle"},
            {"start": 300, "title": "End"},
        ],
        "tags": ["tag one", "tag2"],
        "category": "28",
        "language": "en",
        "cover": "cover.jpg",
        "subtitles": [{"path": "subs.en.srt", "language": "en", "name": "English"}],
        "playlist_id": "PLTESTPLAYLIST000000000000000000",
        "made_for_kids": False,
        "contains_synthetic_media": False,
    }


@pytest.fixture
def write_meta(media: Path):
    def _write(data: dict) -> Path:
        p = media / "upload.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p
    return _write


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return Home(root=tmp_path / "config")


def resp(status: int, body=None, **headers):
    """One HttpMockSequence entry."""
    h = {"status": str(status)}
    h.update({k.replace("_", "-"): v for k, v in headers.items()})
    return (h, json.dumps(body) if body is not None else "")


def mock_service(responses):
    from googleapiclient.http import HttpMockSequence

    http = HttpMockSequence(list(responses))
    return build_service(http=http), http
