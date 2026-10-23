"""Static guarantees about the source tree (see AGENTS.md invariants 2 and 3)."""

import ast
import re
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


def _string_values(tree: ast.AST):
    """Every string the code can produce literally: constants (implicit concatenation is already
    merged by the parser), the literal parts of f-strings joined together, and both sides of a
    `"a" + "b"` concatenation of constants."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            yield node.value if isinstance(node.value, str) else node.value.decode("latin-1"), node.lineno
        elif isinstance(node, ast.JoinedStr):
            yield "".join(v.value for v in node.values if isinstance(v, ast.Constant)), node.lineno
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) \
                and all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in (node.left, node.right)):
            yield node.left.value + node.right.value, node.lineno


def scan_visibility(text: str) -> list[str]:
    """Problems if `text` names the public visibility as a string or mentions the publish-time field."""
    found = []
    if "publishat" in text.lower():
        found.append("mentions the scheduled-publish field")
    for value, line in _string_values(ast.parse(text)):
        if re.fullmatch(r"\W*public\W*", value, re.IGNORECASE):
            found.append(f"string {value!r} at line {line}")
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
    assert scan_visibility('x = f"public"\n')
    assert scan_visibility('x = "pub" "lic"\n')
    assert scan_visibility('x = "pub" + "lic"\n')
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
