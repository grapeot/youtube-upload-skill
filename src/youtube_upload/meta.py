"""Upload metadata: read, validate, normalize. Format reference: skills/youtube-upload/SKILL.md."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .policy import REFUSED_META_KEYS

TITLE_MAX = 100                      # characters
DESCRIPTION_MAX_BYTES = 5000         # UTF-8 bytes, after chapters are rendered
TAGS_TOTAL_MAX = 500                 # YouTube's counting rule, see tags_length()
CHAPTERS_MIN = 3
CHAPTER_MIN_S = 10
CHAPTER_TITLE_MAX = 100
CAPTION_NAME_MAX = 150
VIDEO_MAX_BYTES = 256 * 1024**3
THUMBNAIL_MAX_BYTES = 50 * 1024**2
CAPTION_MAX_BYTES = 100 * 1024**2
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".mts", ".ts"}
THUMBNAIL_EXTS = {".jpg", ".jpeg", ".png"}
CAPTION_EXTS = {".srt", ".vtt"}
LICENSES = ("youtube", "creativeCommon")

KNOWN_KEYS = {
    "video", "title", "description", "chapters", "tags", "category", "language", "audio_language",
    "cover", "subtitles", "playlist_id", "made_for_kids", "contains_synthetic_media",
    "license", "embeddable", "notify_subscribers",
}

_ANGLE = re.compile(r"[<>]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_EXCEPT_NEWLINE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_LANG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_PLAYLIST = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
_CATEGORY = re.compile(r"^\d{1,4}$")
_TS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


class MetaError(ValueError):
    """Invalid metadata. `problems` lists every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Chapter:
    start: int
    title: str


@dataclass
class Caption:
    path: Path
    language: str
    name: str = ""


@dataclass
class UploadMeta:
    video: Path
    title: str
    made_for_kids: bool
    contains_synthetic_media: bool
    description: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    category: str | None = None
    language: str | None = None
    audio_language: str | None = None
    cover: Path | None = None
    subtitles: list[Caption] = field(default_factory=list)
    playlist_id: str | None = None
    license: str = "youtube"
    embeddable: bool = True
    notify_subscribers: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "video": str(self.video),
            "title": self.title,
            "description": self.description,
            "chapters": [{"start": c.start, "title": c.title} for c in self.chapters],
            "tags": self.tags,
            "category": self.category,
            "language": self.language,
            "audio_language": self.audio_language,
            "cover": str(self.cover) if self.cover else None,
            "subtitles": [{"path": str(c.path), "language": c.language, "name": c.name} for c in self.subtitles],
            "playlist_id": self.playlist_id,
            "made_for_kids": self.made_for_kids,
            "contains_synthetic_media": self.contains_synthetic_media,
            "license": self.license,
            "embeddable": self.embeddable,
            "notify_subscribers": self.notify_subscribers,
        }


def parse_timestamp(value: Any) -> int:
    """Whole seconds (int) or an MM:SS / H:MM:SS string -> seconds."""
    if isinstance(value, bool):
        raise ValueError(f"bad timestamp: {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"negative timestamp: {value!r}")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or value != int(value):
            raise ValueError(f"timestamps are whole seconds, got {value!r}")
        return int(value)
    if isinstance(value, str):
        m = _TS.match(value.strip())
        if m:
            h, mnt, sec = m.groups()
            if int(sec) >= 60 or (h is not None and int(mnt) >= 60):
                raise ValueError(f"bad timestamp: {value!r}")
            return int(h or 0) * 3600 + int(mnt) * 60 + int(sec)
    raise ValueError(f"bad timestamp: {value!r}")


def tags_length(tags: list[str]) -> int:
    """YouTube's count: commas between tags count, and a tag containing a space counts two quote marks."""
    total = sum(len(t) + (2 if " " in t else 0) for t in tags)
    return total + max(len(tags) - 1, 0)


def _resolve(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else base / p


def parse_meta(data: Any, base_dir: Path, check_files: bool = True) -> UploadMeta:
    """Validate and normalize a metadata dict. Collects every problem, then raises MetaError."""
    if not isinstance(data, dict):
        raise MetaError(["metadata must be a JSON object"])
    problems: list[str] = []

    refused = sorted(k for k in data if k in REFUSED_META_KEYS)
    if refused:
        problems.append(
            f"{refused}: visibility and scheduling cannot be set in metadata. Uploads are private; pass "
            "--visibility unlisted on the command line for one run; publish or schedule in YouTube Studio"
        )
    unknown = sorted(set(data) - KNOWN_KEYS - set(REFUSED_META_KEYS))
    if unknown:
        problems.append(f"unknown keys: {unknown}")

    def need_file(key: str, value: Any, exts: set[str], max_bytes: int) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{key}: must be a non-empty path string")
            return None
        p = _resolve(base_dir, value.strip())
        if p.suffix.lower() not in exts:
            problems.append(f"{key}: unsupported extension {p.suffix!r} (allowed: {sorted(exts)})")
        if check_files:
            if not p.is_file():
                problems.append(f"{key}: file not found: {p}")
                return p
            size = p.stat().st_size
            if size == 0:
                problems.append(f"{key}: file is empty: {p}")
            elif size > max_bytes:
                problems.append(f"{key}: {size} bytes > {max_bytes}")
            return p.resolve()
        return p

    def need_bool(key: str, default: bool | None) -> bool:
        if key not in data:
            if default is None:
                problems.append(f"{key}: required (true or false); there is no default")
                return False
            return default
        value = data[key]
        if not isinstance(value, bool):
            problems.append(f"{key}: must be true or false")
            return bool(default)
        return value

    def lang(key: str, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _LANG.match(value.strip()):
            problems.append(f"{key}: must be a BCP-47 language code such as en, zh-Hans, pt-BR")
            return None
        return value.strip()

    video = need_file("video", data.get("video"), VIDEO_EXTS, VIDEO_MAX_BYTES)

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("title: required")
        title = ""
    else:
        title = title.strip()
        if _CONTROL.search(title):
            problems.append("title: must not contain line breaks or control characters")
        if _ANGLE.search(title):
            problems.append("title: must not contain < or >")
        if len(title) > TITLE_MAX:
            problems.append(f"title: {len(title)} characters > {TITLE_MAX}")

    description = data.get("description", "")
    if not isinstance(description, str):
        problems.append("description: must be a string")
        description = ""
    else:
        if _ANGLE.search(description):
            problems.append("description: must not contain < or >")
        if _CONTROL_EXCEPT_NEWLINE.search(description):
            problems.append("description: must not contain control characters other than line breaks")

    chapters: list[Chapter] = []
    raw_ch = data.get("chapters", [])
    if not isinstance(raw_ch, list):
        problems.append("chapters: must be a list of {start, title}")
    else:
        ok = True
        for i, ch in enumerate(raw_ch):
            if not isinstance(ch, dict) or set(ch) != {"start", "title"}:
                problems.append(f"chapters[{i}]: must be an object with exactly start and title")
                ok = False
                continue
            try:
                start = parse_timestamp(ch["start"])
            except ValueError as e:
                problems.append(f"chapters[{i}]: {e}")
                ok = False
                continue
            t = ch["title"]
            if not isinstance(t, str) or not t.strip():
                problems.append(f"chapters[{i}]: empty title")
                ok = False
                continue
            t = t.strip()
            if _CONTROL.search(t) or _ANGLE.search(t):
                problems.append(f"chapters[{i}]: title must not contain line breaks, < or >")
                ok = False
            elif len(t) > CHAPTER_TITLE_MAX:
                problems.append(f"chapters[{i}]: title is {len(t)} characters > {CHAPTER_TITLE_MAX}")
                ok = False
            chapters.append(Chapter(start=start, title=t))
        if ok and chapters:
            if len(chapters) < CHAPTERS_MIN:
                problems.append(f"chapters: YouTube needs at least {CHAPTERS_MIN}, got {len(chapters)}")
            if chapters[0].start != 0:
                problems.append("chapters: the first chapter must start at 0")
            if any(b.start <= a.start for a, b in zip(chapters, chapters[1:])):
                problems.append("chapters: start times must be strictly increasing")
            elif any(b.start - a.start < CHAPTER_MIN_S for a, b in zip(chapters, chapters[1:])):
                problems.append(f"chapters: every chapter must be at least {CHAPTER_MIN_S} s long")

    tags = data.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        problems.append("tags: must be a list of strings")
        tags = []
    else:
        tags = [t.strip() for t in tags]
        for t in tags:
            if not t:
                problems.append("tags: empty tag")
            elif "," in t:
                problems.append(f"tags: {t!r} contains a comma")
            elif _ANGLE.search(t) or _CONTROL.search(t):
                problems.append(f"tags: {t!r} contains <, > or a control character")
        if len({t.lower() for t in tags}) != len(tags):
            problems.append("tags: duplicates")
        if tags_length(tags) > TAGS_TOTAL_MAX:
            problems.append(f"tags: total length {tags_length(tags)} > {TAGS_TOTAL_MAX} (commas count; a tag with a space counts 2 extra)")

    category = data.get("category")
    if category is not None:
        if isinstance(category, int) and not isinstance(category, bool):
            category = str(category)
        if not isinstance(category, str) or not _CATEGORY.match(category.strip()):
            problems.append("category: must be a numeric category ID such as \"28\" (see `yt-upload categories`)")
            category = None
        else:
            category = category.strip()

    language = lang("language", data.get("language"))
    audio_language = lang("audio_language", data.get("audio_language"))

    cover = None
    if data.get("cover") is not None:
        cover = need_file("cover", data["cover"], THUMBNAIL_EXTS, THUMBNAIL_MAX_BYTES)

    subtitles: list[Caption] = []
    raw_sub = data.get("subtitles", [])
    if not isinstance(raw_sub, list):
        problems.append("subtitles: must be a list of {path, language, name}")
    else:
        seen: set[tuple[str, str]] = set()
        for i, s in enumerate(raw_sub):
            if not isinstance(s, dict) or not set(s) <= {"path", "language", "name"}:
                problems.append(f"subtitles[{i}]: must be an object with path, language and optional name")
                continue
            p = need_file(f"subtitles[{i}].path", s.get("path"), CAPTION_EXTS, CAPTION_MAX_BYTES)
            lg = s.get("language")
            if not isinstance(lg, str) or not _LANG.match(lg.strip()):
                problems.append(f"subtitles[{i}].language: required BCP-47 code such as en or zh-Hans")
                continue
            name = s.get("name", "")
            if not isinstance(name, str) or _CONTROL.search(name) or len(name) > CAPTION_NAME_MAX:
                problems.append(f"subtitles[{i}].name: must be a single-line string of at most {CAPTION_NAME_MAX} characters")
                continue
            key = (lg.strip().lower(), name.strip())
            if key in seen:
                problems.append(f"subtitles[{i}]: duplicate language and name {key}")
            seen.add(key)
            if p is not None:
                subtitles.append(Caption(path=p, language=lg.strip(), name=name.strip()))

    playlist_id = data.get("playlist_id")
    if playlist_id is not None:
        if not isinstance(playlist_id, str) or not _PLAYLIST.match(playlist_id.strip()):
            hint = " (pass the ID after list=, not the URL)" if isinstance(playlist_id, str) and "list=" in playlist_id else ""
            problems.append(f"playlist_id: must be an existing playlist ID{hint}")
            playlist_id = None
        else:
            playlist_id = playlist_id.strip()

    made_for_kids = need_bool("made_for_kids", None)
    contains_synthetic_media = need_bool("contains_synthetic_media", None)
    embeddable = need_bool("embeddable", True)
    notify_subscribers = need_bool("notify_subscribers", True)

    license_ = data.get("license", "youtube")
    if license_ not in LICENSES:
        problems.append(f"license: must be one of {list(LICENSES)}")

    if problems:
        raise MetaError(problems)
    assert video is not None
    return UploadMeta(
        video=video, title=title, description=description, chapters=chapters, tags=tags,
        category=category, language=language, audio_language=audio_language, cover=cover,
        subtitles=subtitles, playlist_id=playlist_id, made_for_kids=made_for_kids,
        contains_synthetic_media=contains_synthetic_media, license=license_,
        embeddable=embeddable, notify_subscribers=notify_subscribers,
    )


def load_meta(path: str | Path, check_files: bool = True) -> UploadMeta:
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MetaError([f"metadata file not found: {p}"]) from None
    except json.JSONDecodeError as e:
        raise MetaError([f"invalid JSON: {e}"]) from None
    return parse_meta(data, base_dir=p.resolve().parent, check_files=check_files)
