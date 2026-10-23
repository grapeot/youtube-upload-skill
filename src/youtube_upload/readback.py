"""Read a video back and compare it with the metadata. Read-only."""

from __future__ import annotations

from typing import Any

from .describe import render_description
from .meta import UploadMeta

VIDEO_PARTS = "snippet,status,contentDetails,processingDetails"


def fetch_video(service: Any, video_id: str) -> dict[str, Any] | None:
    resp = service.videos().list(part=VIDEO_PARTS, id=video_id).execute()
    items = resp.get("items") or []
    return items[0] if items else None


def list_captions(service: Any, video_id: str) -> list[dict[str, Any]]:
    resp = service.captions().list(part="snippet", videoId=video_id).execute()
    out = []
    for item in resp.get("items") or []:
        sn = item.get("snippet", {})
        out.append({
            "id": item.get("id"), "language": sn.get("language"), "name": sn.get("name", ""),
            "track_kind": sn.get("trackKind"), "status": sn.get("status"),
        })
    return out


def playlist_contains(service: Any, playlist_id: str, video_id: str) -> bool:
    resp = service.playlistItems().list(part="id", playlistId=playlist_id, videoId=video_id, maxResults=5).execute()
    return bool(resp.get("items"))


def summarize(video: dict[str, Any]) -> dict[str, Any]:
    sn, st = video.get("snippet", {}), video.get("status", {})
    cd, pd = video.get("contentDetails", {}), video.get("processingDetails", {})
    return {
        "title": sn.get("title"),
        "description": sn.get("description"),
        "tags": sn.get("tags", []),
        "category": sn.get("categoryId"),
        "language": sn.get("defaultLanguage"),
        "audio_language": sn.get("defaultAudioLanguage"),
        "visibility": st.get("privacyStatus"),
        "upload_status": st.get("uploadStatus"),
        "failure_reason": st.get("failureReason"),
        "rejection_reason": st.get("rejectionReason"),
        "made_for_kids": st.get("selfDeclaredMadeForKids"),
        "made_for_kids_effective": st.get("madeForKids"),
        "contains_synthetic_media": st.get("containsSyntheticMedia"),
        "license": st.get("license"),
        "embeddable": st.get("embeddable"),
        "duration": cd.get("duration"),
        "processing_status": pd.get("processingStatus"),
    }


def compare(fields: dict[str, Any], meta: UploadMeta, visibility: str | None) -> dict[str, list]:
    """mismatches: present and different. unverified: not returned by the API. not_applied: best-effort fields."""
    mismatches: list[dict[str, Any]] = []
    unverified: list[str] = []
    not_applied: list[dict[str, Any]] = []

    def check(name: str, expected: Any, actual: Any) -> None:
        if actual is None:
            unverified.append(name)
        elif actual != expected:
            mismatches.append({"field": name, "expected": expected, "actual": actual})

    check("title", meta.title, fields["title"])
    check("description", render_description(meta.description, meta.chapters), fields["description"])
    if sorted(fields["tags"] or []) != sorted(meta.tags):
        if not fields["tags"] and fields.get("processing_status") == "processing":
            # Observed on a real upload: tags come back empty until processing finishes.
            unverified.append("tags")
        else:
            mismatches.append({"field": "tags", "expected": meta.tags, "actual": fields["tags"]})
    if meta.category:
        check("category", meta.category, fields["category"])
    if meta.language:
        check("language", meta.language, fields["language"])
    if visibility is not None:
        check("visibility", visibility, fields["visibility"])
    check("made_for_kids", meta.made_for_kids, fields["made_for_kids"])
    # videos.list does not return containsSyntheticMedia in practice; it lands in `unverified` and the
    # human checks the disclosure in Studio (see HUMAN_STEPS).
    check("contains_synthetic_media", meta.contains_synthetic_media, fields["contains_synthetic_media"])
    check("license", meta.license, fields["license"])
    check("embeddable", meta.embeddable, fields["embeddable"])
    if meta.audio_language and fields["audio_language"] != meta.audio_language:
        not_applied.append({"field": "audio_language", "expected": meta.audio_language, "actual": fields["audio_language"],
                            "fix": "set the video language in YouTube Studio > Details > Language and captions"})
    return {"mismatches": mismatches, "unverified": unverified, "not_applied": not_applied}


def readback(service: Any, video_id: str, meta: UploadMeta | None = None, visibility: str | None = None) -> dict[str, Any]:
    video = fetch_video(service, video_id)
    if video is None:
        return {"ok": False, "video_id": video_id, "error": "video not found on the authorized channel"}
    fields = summarize(video)
    out: dict[str, Any] = {"ok": True, "video_id": video_id, "fields": fields}
    if fields["upload_status"] in ("failed", "rejected", "deleted"):
        out["ok"] = False
        out["error"] = f"upload status is {fields['upload_status']}"
    if meta is None:
        return out
    result = compare(fields, meta, visibility)
    if meta.subtitles:
        tracks = list_captions(service, video_id)
        out["captions"] = tracks
        have = {((t["language"] or "").lower(), t["name"] or "") for t in tracks
                if t.get("track_kind") in (None, "standard")}  # ignore YouTube's automatic (ASR) track
        for c in meta.subtitles:
            if (c.language.lower(), c.name) not in have:
                result["mismatches"].append({"field": "captions", "expected": {"language": c.language, "name": c.name}, "actual": None})
    if meta.playlist_id:
        contained = playlist_contains(service, meta.playlist_id, video_id)
        out["playlist"] = {"id": meta.playlist_id, "contains_video": contained}
        if not contained:
            result["mismatches"].append({"field": "playlist", "expected": meta.playlist_id, "actual": None})
    out.update(result)
    if result["mismatches"]:
        out["ok"] = False
    return out
