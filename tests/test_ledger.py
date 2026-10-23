import hashlib

import pytest

from youtube_upload.ledger import Ledger, file_sha256, public_view
from youtube_upload.paths import mode


def test_sha256_streams(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc" * 1000)
    assert file_sha256(p, chunk=7) == hashlib.sha256(b"abc" * 1000).hexdigest()


def test_round_trip_and_permissions(home, tmp_path):
    book = Ledger(home.ledger)
    e = book.start("h1", tmp_path / "v.mp4", 10, "t", "private")
    book.update(e, session_uri="https://upload.example.invalid/s")
    book.set_step(e, "thumbnail", "done")
    again = Ledger(home.ledger)
    assert again.latest("h1")["steps"] == {"thumbnail": "done"}
    assert again.latest("h2") is None
    assert mode(home.ledger) == 0o600 and mode(home.root) == 0o700


def test_latest_is_the_most_recent_entry(home, tmp_path):
    book = Ledger(home.ledger)
    book.start("h", tmp_path / "v.mp4", 1, "first", "private")
    book.start("h", tmp_path / "v.mp4", 1, "second", "private")
    assert book.latest("h")["title"] == "second"


def test_public_view_strips_session(home, tmp_path):
    book = Ledger(home.ledger)
    e = book.start("h", tmp_path / "v.mp4", 1, "t", "private")
    book.update(e, session_uri="https://upload.example.invalid/s")
    view = public_view(e)
    assert "session_uri" not in view and view["has_session"] is True
    assert public_view(None) is None


def test_corrupt_ledger_is_reported(home):
    home.root.mkdir(parents=True)
    home.ledger.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        Ledger(home.ledger)
