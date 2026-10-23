"""Static guarantees about the source tree (see AGENTS.md invariants 2 and 3)."""

import ast
import io
import re
import tokenize
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "youtube_upload"
ALLOWLIST = {"policy.py"}  # the only module that may name the forbidden visibility and field

FORBIDDEN_CALLS = [
    r"\.delete\(",                  # never delete anything
    r"videos\(\)\s*\.\s*update",    # never edit existing videos
    r"videos\(\)\s*\.\s*rate",
    r"channels\(\)\s*\.\s*update",  # never change channel settings
    r"channelBanners\(\)",
    r"channelSections\(\)",
    r"watermarks\(\)",
    r"playlists\(\)\s*\.\s*(insert|update)",  # never create or edit playlists
    r"playlistItems\(\)\s*\.\s*update",
    r"captions\(\)\s*\.\s*update",
    r"subscriptions\(\)",
    r"commentThreads\(\)",
]
ALLOWED_WRITES = {"videos().insert", "thumbnails().set", "captions().insert", "playlistItems().insert"}


def scan_visibility(text: str) -> list[str]:
    """Problems if `text` names the public visibility as a string literal or mentions the publish-time field."""
    found = []
    if "publishat" in text.lower():
        found.append("mentions the scheduled-publish field")
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(tok.string)
        except (ValueError, SyntaxError):  # f-strings and similar: check the raw text
            value = tok.string
        if isinstance(value, (str, bytes)) and re.fullmatch(r"\W*public\W*", str(value), re.IGNORECASE):
            found.append(f"string literal {tok.string} at line {tok.start[0]}")
    return found


def source_files():
    files = sorted(SRC.rglob("*.py"))
    assert files, SRC
    return files


def test_scanner_detects_violations():
    assert scan_visibility('x = "public"\n')
    assert scan_visibility("x = 'Public'\n")
    assert scan_visibility('body["status"]["publishAt"] = t\n')
    assert scan_visibility('x = "publishat"\n')
    assert not scan_visibility('x = "publish it in Studio"\ny = "private"\n# public is not allowed\n')


def test_public_and_publish_time_only_in_policy():
    offenders = {}
    for f in source_files():
        if f.name in ALLOWLIST:
            continue
        problems = scan_visibility(f.read_text(encoding="utf-8"))
        if problems:
            offenders[f.name] = problems
    assert offenders == {}


def test_policy_still_names_them():
    text = (SRC / "policy.py").read_text(encoding="utf-8")
    assert scan_visibility(text), "policy.py must be the place that refuses them"


def test_no_forbidden_api_calls():
    offenders = []
    for f in source_files():
        text = f.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_CALLS:
            for m in re.finditer(pattern, text):
                offenders.append(f"{f.name}: {m.group(0)}")
    assert offenders == []


def test_write_calls_are_the_documented_four_and_only_in_upload():
    write = re.compile(r"(\w+)\(\)\s*\.\s*(insert|set|update|delete)\(")
    seen = {}
    for f in source_files():
        for m in write.finditer(f.read_text(encoding="utf-8")):
            seen.setdefault(f"{m.group(1)}().{m.group(2)}", set()).add(f.name)
    assert set(seen) == ALLOWED_WRITES
    assert all(files == {"upload.py"} for files in seen.values()), seen
