"""Render chapters into the description in the form YouTube parses as chapters."""

from __future__ import annotations

import re

from .meta import CHAPTER_MIN_S, DESCRIPTION_MAX_BYTES, Chapter

# A line that starts with a timestamp: YouTube would read it as a chapter marker.
_TIMESTAMP_LINE = re.compile(r"^\s*[\(\[]?\d{1,2}:\d{2}(?::\d{2})?\b", re.MULTILINE)


def format_timestamp(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def chapter_lines(chapters: list[Chapter]) -> list[str]:
    return [f"{format_timestamp(c.start)} {c.title}" for c in chapters]


def render_description(description: str, chapters: list[Chapter]) -> str:
    """Description text, then a blank line, then one `MM:SS Title` line per chapter."""
    body = description.rstrip()
    if not chapters:
        return body
    block = "\n".join(chapter_lines(chapters))
    return f"{body}\n\n{block}" if body else block


def description_problems(description: str, chapters: list[Chapter], duration_s: float | None) -> list[str]:
    problems: list[str] = []
    if chapters and _TIMESTAMP_LINE.search(description):
        problems.append(
            "description: already contains a line starting with a timestamp; YouTube would mix it with "
            "the chapters. Put chapters only in `chapters`"
        )
    rendered = render_description(description, chapters)
    size = len(rendered.encode("utf-8"))
    if size > DESCRIPTION_MAX_BYTES:
        problems.append(f"description: {size} UTF-8 bytes with chapters > {DESCRIPTION_MAX_BYTES}")
    if chapters and duration_s is not None:
        last = chapters[-1].start
        if last >= duration_s:
            problems.append(f"chapters: last chapter starts at {last} s, after the video ends ({duration_s:.1f} s)")
        elif duration_s - last < CHAPTER_MIN_S:
            problems.append(f"chapters: last chapter is {duration_s - last:.1f} s long; YouTube needs at least {CHAPTER_MIN_S} s")
    return problems
