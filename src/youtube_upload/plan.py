"""Dry-run plan: the exact request bodies, the steps, a quota estimate and warnings. No network."""

from __future__ import annotations

from typing import Any

from .api import DAILY_SHARED_UNITS, DAILY_UPLOADS, QUOTA
from .describe import description_problems, render_description
from .meta import MetaError, UploadMeta
from .policy import check_insert_body, check_visibility

LONG_UPLOAD_S = 15 * 60
INSERT_PART = "snippet,status"


def caption_step(language: str, name: str) -> str:
    return f"caption:{language}:{name}" if name else f"caption:{language}"


def insert_body(meta: UploadMeta, visibility: str) -> dict[str, Any]:
    snippet: dict[str, Any] = {"title": meta.title, "description": render_description(meta.description, meta.chapters)}
    if meta.tags:
        snippet["tags"] = list(meta.tags)
    if meta.category:
        snippet["categoryId"] = meta.category
    if meta.language:
        snippet["defaultLanguage"] = meta.language
    if meta.audio_language:
        snippet["defaultAudioLanguage"] = meta.audio_language
    status = {
        "privacyStatus": check_visibility(visibility),
        "selfDeclaredMadeForKids": meta.made_for_kids,
        "containsSyntheticMedia": meta.contains_synthetic_media,
        "license": meta.license,
        "embeddable": meta.embeddable,
    }
    body = {"snippet": snippet, "status": status}
    check_insert_body(body)
    return body


def follow_up_steps(meta: UploadMeta) -> list[str]:
    steps: list[str] = []
    if meta.cover:
        steps.append("thumbnail")
    steps += [caption_step(c.language, c.name) for c in meta.subtitles]
    if meta.playlist_id:
        steps.append("playlist")
    return steps


def quota_estimate(meta: UploadMeta, insert: bool = True) -> dict[str, Any]:
    shared = QUOTA["channels.list"] + QUOTA["videos.list"]  # preflight + readback
    if meta.cover:
        shared += QUOTA["thumbnails.set"]
    if meta.subtitles:
        shared += QUOTA["captions.insert"] * len(meta.subtitles) + QUOTA["captions.list"]
    if meta.playlist_id:
        shared += QUOTA["playlistItems.list"] * 2 + QUOTA["playlistItems.insert"]
    return {
        "uploads_bucket_calls": 1 if insert else 0,
        "uploads_bucket_daily_limit": DAILY_UPLOADS,
        "shared_units": shared,
        "shared_daily_limit": DAILY_SHARED_UNITS,
    }


def build_plan(meta: UploadMeta, visibility: str, duration_s: float | None) -> dict[str, Any]:
    problems = description_problems(meta.description, meta.chapters, duration_s)
    if problems:
        raise MetaError(problems)
    body = insert_body(meta, visibility)
    warnings: list[str] = []
    if duration_s is None:
        warnings.append("ffprobe unavailable or failed: the last chapter's length and the 15-minute limit were not checked")
    elif duration_s > LONG_UPLOAD_S:
        warnings.append("video is longer than 15 minutes: the channel must be phone-verified (checked before upload)")
    if not meta.category:
        warnings.append("no category: YouTube assigns its default; list IDs with `yt-upload categories`")
    if meta.audio_language:
        warnings.append("audio_language is not a documented writable field of videos.insert; the readback reports whether it was applied")
    if meta.cover:
        warnings.append("custom thumbnails need a verified channel; a refusal is reported as a failed step")
    if visibility != "private":
        warnings.append(f"visibility is {visibility} for this run (requested explicitly)")
    return {
        "request": {
            "method": "videos.insert",
            "part": INSERT_PART,
            "notifySubscribers": meta.notify_subscribers,
            "body": body,
        },
        "visibility": visibility,
        "disclosures": {
            "made_for_kids": meta.made_for_kids,
            "contains_synthetic_media": meta.contains_synthetic_media,
        },
        "duration_s": duration_s,
        "steps": ["videos.insert", *follow_up_steps(meta), "readback"],
        "quota": quota_estimate(meta),
        "warnings": warnings,
    }
