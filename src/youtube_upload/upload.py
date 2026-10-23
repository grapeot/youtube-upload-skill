"""upload --execute: duplicate guard, resumable videos.insert, follow-up steps, readback.

Write calls made here, and nowhere else: videos.insert, thumbnails.set, captions.insert,
playlistItems.insert.
"""

from __future__ import annotations

import mimetypes
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import ledger as L
from .api import error_details, is_retryable
from .ledger import Ledger, file_sha256, public_view
from .meta import UploadMeta
from .plan import INSERT_PART, LONG_UPLOAD_S, build_plan, caption_step, quota_estimate
from .policy import check_insert_body
from .readback import list_captions, playlist_contains, readback

CHUNK_SIZE = 8 * 1024 * 1024
MAX_RETRIES = 8
STEP_RETRIES = 3


class UploadRefused(RuntimeError):
    """The run must not proceed (duplicate, nothing to resume, channel limits...)."""

    def __init__(self, message: str, **extra: Any):
        super().__init__(message)
        self.extra = extra


@dataclass
class Options:
    visibility: str
    resume: bool = False
    new_upload: bool = False
    chunk_size: int = CHUNK_SIZE
    max_retries: int = MAX_RETRIES
    sleep: Callable[[float], None] = time.sleep
    progress: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr, flush=True)


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def studio_url(video_id: str) -> str:
    return f"https://studio.youtube.com/video/{video_id}/edit"


def _with_retries(fn: Callable[[int], Any], opts: Options, attempts: int) -> Any:
    """Call fn(attempt) and retry transient failures with backoff."""
    for attempt in range(attempts + 1):
        try:
            return fn(attempt)
        except Exception as e:  # noqa: BLE001 - classified below
            if attempt >= attempts or not is_retryable(e):
                raise
            opts.sleep(min(2 ** (attempt + 1), 64))
    raise AssertionError("unreachable")


def decide_mode(prior: dict[str, Any] | None, opts: Options) -> str:
    """'new', 'resume_session' or 'follow_ups'. Raises UploadRefused for duplicates."""
    if opts.resume and opts.new_upload:
        raise UploadRefused("--resume and --new-upload are mutually exclusive")
    if prior is not None and prior.get("state") not in (L.UPLOADING, L.UPLOADED):
        prior = None  # e.g. marked "deleted" by hand after the owner removed the video in Studio
    if prior is None:
        if opts.resume:
            raise UploadRefused("nothing to resume: this file has no recorded upload")
        return "new"
    if opts.new_upload:
        return "new"
    if prior.get("visibility") != opts.visibility and opts.resume:
        raise UploadRefused(
            f"the recorded upload used visibility {prior.get('visibility')!r}; resume with the same --visibility",
            ledger=public_view(prior),
        )
    if prior.get("state") == L.UPLOADED:
        if opts.resume:
            return "follow_ups"
        raise UploadRefused(
            f"this file was already uploaded as {prior.get('video_id')}; use --resume to finish remaining steps, "
            "or --new-upload to upload it again",
            video_id=prior.get("video_id"), ledger=public_view(prior),
        )
    if opts.resume:
        return "resume_session"
    raise UploadRefused(
        "an upload of this file was interrupted; use --resume to continue it, or --new-upload to start over",
        ledger=public_view(prior),
    )


def channel_preflight(service: Any) -> dict[str, Any]:
    resp = service.channels().list(part="id,snippet,status", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        raise UploadRefused("the authorized Google account has no YouTube channel; create one first")
    ch = items[0]
    return {
        "id": ch.get("id"),
        "title": ch.get("snippet", {}).get("title"),
        "long_uploads_status": ch.get("status", {}).get("longUploadsStatus"),
    }


class _SessionRecorder:
    """httplib2-compatible wrapper that reports the Location of a resumable-session initiation."""

    def __init__(self, inner: Any, on_session: Callable[[str], None]):
        self._inner = inner
        self._on_session = on_session

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        resp, content = self._inner.request(uri, method=method, body=body, headers=headers, **kwargs)
        if method == "POST" and getattr(resp, "status", None) == 200 and "location" in resp:
            self._on_session(resp["location"])
        return resp, content

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _insert(service: Any, meta: UploadMeta, body: dict[str, Any], entry: dict[str, Any], book: Ledger,
            opts: Options, resume_uri: str | None) -> dict[str, Any]:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    check_insert_body(body)  # last check before anything is sent
    mimetype = mimetypes.guess_type(str(meta.video))[0] or "application/octet-stream"
    media = MediaFileUpload(str(meta.video), mimetype=mimetype, chunksize=opts.chunk_size, resumable=True)
    request = service.videos().insert(
        part=INSERT_PART, body=body, notifySubscribers=meta.notify_subscribers, media_body=media,
    )
    if resume_uri:
        # Re-attach to the recorded session: the next call asks the server how many bytes it holds.
        request.resumable_uri = resume_uri
        request._in_error_state = True  # noqa: SLF001 - documented resume path of the client library

    def remember(uri: str | None = None) -> None:
        uri = uri or request.resumable_uri
        if uri and entry.get("session_uri") != uri:
            book.update(entry, session_uri=uri)

    # The library starts the session and sends the first chunk inside one next_chunk() call; record
    # the session URL from the initiation response itself, so a crash during the first chunk can resume.
    recorder = _SessionRecorder(request.http, remember)
    response = None
    retries = 0
    size = max(media.size() or 1, 1)
    while response is None:
        try:
            status, response = request.next_chunk(http=recorder)
            retries = 0
            remember()
            if status is not None:
                opts.progress(f"uploaded {status.resumable_progress}/{size} bytes ({100 * status.resumable_progress // size}%)")
        except Exception as e:  # noqa: BLE001 - classified below
            remember()
            if resume_uri and isinstance(e, HttpError) and getattr(e.resp, "status", None) in (404, 410):
                book.update(entry, state=L.EXPIRED, session_uri=None)
                raise UploadRefused(
                    "the recorded upload session has expired; start again with --new-upload",
                    error_detail=error_details(e),
                ) from None
            if retries >= opts.max_retries or not is_retryable(e):
                raise
            retries += 1
            delay = min(2 ** retries, 64)
            opts.progress(f"retry {retries}/{opts.max_retries} in {delay}s after {error_details(e)}")
            opts.sleep(delay)
    return response


def _thumbnail(service: Any, meta: UploadMeta, video_id: str) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    mimetype = "image/png" if meta.cover.suffix.lower() == ".png" else "image/jpeg"
    resp = service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(meta.cover), mimetype=mimetype)).execute()
    return {"sizes": sorted((resp.get("items") or [{}])[0].keys())}


def _caption(service: Any, caption, video_id: str, retrying: bool) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    if retrying:  # a previous attempt may have landed; do not add a second track
        for t in list_captions(service, video_id):
            if t.get("track_kind") not in (None, "standard"):
                continue  # YouTube's automatic (ASR) track is not the owner's caption
            if (t["language"] or "").lower() == caption.language.lower() and (t["name"] or "") == caption.name:
                return {"id": t["id"], "already_present": True}
    body = {"snippet": {"videoId": video_id, "language": caption.language, "name": caption.name, "isDraft": False}}
    media = MediaFileUpload(str(caption.path), mimetype="application/octet-stream")
    resp = service.captions().insert(part="snippet", body=body, media_body=media).execute()
    return {"id": resp.get("id")}


def _playlist(service: Any, playlist_id: str, video_id: str) -> dict[str, Any]:
    if playlist_contains(service, playlist_id, video_id):
        return {"already_present": True}
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    resp = service.playlistItems().insert(part="snippet", body=body).execute()
    return {"id": resp.get("id")}


def run_follow_ups(service: Any, meta: UploadMeta, video_id: str, entry: dict[str, Any], book: Ledger,
                   opts: Options) -> list[dict[str, Any]]:
    tasks: list[tuple[str, Callable[[bool], dict[str, Any]]]] = []
    if meta.cover:
        tasks.append(("thumbnail", lambda retrying: _thumbnail(service, meta, video_id)))
    for c in meta.subtitles:
        tasks.append((caption_step(c.language, c.name), lambda retrying, c=c: _caption(service, c, video_id, retrying)))
    if meta.playlist_id:
        tasks.append(("playlist", lambda retrying: _playlist(service, meta.playlist_id, video_id)))
    results = []
    for name, fn in tasks:
        previous = entry.get("steps", {}).get(name)
        if previous == "done":
            results.append({"step": name, "ok": True, "skipped": "done in an earlier run"})
            continue
        book.set_step(entry, name, "started")
        try:
            detail = _with_retries(lambda attempt: fn(previous is not None or attempt > 0), opts, STEP_RETRIES)
            book.set_step(entry, name, "done")
            results.append({"step": name, "ok": True, **detail})
        except Exception as e:  # noqa: BLE001 - reported per step, the rest continue
            book.set_step(entry, name, "failed")
            results.append({"step": name, "ok": False, "error": error_details(e)})
    return results


HUMAN_STEPS = [
    "Open the video in YouTube Studio and watch it, including captions and chapters.",
    "Check the thumbnail, the audience setting and the altered or synthetic content disclosure.",
    "Publish or schedule it in Studio when it is ready. This tool never does that.",
]


def execute(meta: UploadMeta, service_factory: Callable[[], Any], book: Ledger, opts: Options,
            duration_s: float | None) -> dict[str, Any]:
    plan = build_plan(meta, opts.visibility, duration_s)
    sha = file_sha256(meta.video)
    prior = book.latest(sha)
    mode = decide_mode(prior, opts)

    service = service_factory()
    channel = channel_preflight(service)
    if mode != "follow_ups" and duration_s is not None and duration_s > LONG_UPLOAD_S \
            and channel["long_uploads_status"] != "allowed":
        raise UploadRefused(
            f"video is longer than 15 minutes and the channel's long uploads status is "
            f"{channel['long_uploads_status']!r}; verify the channel with a phone number in YouTube Studio",
            channel=channel,
        )

    if mode == "follow_ups":
        entry = prior
    elif mode == "resume_session":
        entry = prior
    else:
        entry = book.start(sha, meta.video, meta.video.stat().st_size, meta.title, opts.visibility)

    result: dict[str, Any] = {"ok": False, "mode": mode, "channel": channel, "sha256": sha}
    if mode != "follow_ups":
        resume_uri = entry.get("session_uri") if mode == "resume_session" else None
        try:
            response = _insert(service, meta, plan["request"]["body"], entry, book, opts, resume_uri)
        except UploadRefused:
            raise
        except Exception as e:  # noqa: BLE001 - surfaced with the ledger state
            result.update({
                "error": "videos.insert failed",
                "error_detail": error_details(e),
                "ledger": public_view(entry),
                "next": "rerun with --execute --resume to continue this upload" if entry.get("session_uri")
                        else "rerun with --execute --new-upload",
            })
            return result
        video_id = response.get("id") if isinstance(response, dict) else None
        if not video_id:
            result.update({"error": "videos.insert completed without a video ID", "response": response,
                           "ledger": public_view(entry), "next": "check YouTube Studio before retrying"})
            return result
        book.update(entry, state=L.UPLOADED, video_id=video_id, session_uri=None)

    video_id = entry["video_id"]
    steps = run_follow_ups(service, meta, video_id, entry, book, opts)
    try:
        rb = readback(service, video_id, meta, opts.visibility)
    except Exception as e:  # noqa: BLE001
        rb = {"ok": False, "error": "readback failed", "error_detail": error_details(e)}
    result.update({
        "ok": all(s["ok"] for s in steps) and rb.get("ok", False),
        "video_id": video_id,
        "url": watch_url(video_id),
        "studio_url": studio_url(video_id),
        "visibility": entry.get("visibility"),
        "steps": steps,
        "readback": rb,
        "quota": quota_estimate(meta, insert=mode != "follow_ups"),
        "ledger": public_view(entry),
        "human_steps": HUMAN_STEPS,
    })
    return result
