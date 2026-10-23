"""YouTube Data API client construction, quota table and error details."""

from __future__ import annotations

import json
from typing import Any

# Units from the shared 10,000/day pool (determine_quota_cost). videos.insert is separate:
# its own bucket of 100 calls/day at 1 per call.
QUOTA = {
    "channels.list": 1,
    "videos.list": 1,
    "videoCategories.list": 1,
    "thumbnails.set": 50,
    "captions.insert": 400,
    "captions.list": 50,
    "playlistItems.insert": 50,
    "playlistItems.list": 1,
}
DAILY_SHARED_UNITS = 10_000
DAILY_UPLOADS = 100

RETRYABLE_STATUS = {500, 502, 503, 504}


def build_service(credentials: Any = None, http: Any = None):
    """Build the youtube v3 client from the library's bundled discovery document (no network)."""
    from googleapiclient.discovery import build

    if http is not None:
        return build("youtube", "v3", http=http, cache_discovery=False, static_discovery=True)
    return build("youtube", "v3", credentials=credentials, cache_discovery=False, static_discovery=True)


def error_details(exc: BaseException) -> dict[str, Any]:
    """Google's status, reason and message, untruncated except for pathological sizes."""
    from googleapiclient.errors import HttpError

    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        out: dict[str, Any] = {"type": "HttpError", "status": int(status) if status is not None else None}
        raw = exc.content.decode("utf-8", "replace") if isinstance(exc.content, bytes) else str(exc.content)
        try:
            err = json.loads(raw).get("error", {})
        except (json.JSONDecodeError, AttributeError):
            err = {}
        if isinstance(err, dict) and err:
            out["message"] = err.get("message")
            reasons = [e.get("reason") for e in err.get("errors", []) if isinstance(e, dict) and e.get("reason")]
            if reasons:
                out["reason"] = reasons[0]
                out["reasons"] = reasons
        elif isinstance(err, str):
            out["message"] = err
        else:
            out["message"] = raw[:2000]
        return out
    return {"type": type(exc).__name__, "message": str(exc)[:2000]}


def is_retryable(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError

    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None) in RETRYABLE_STATUS
    try:
        import httplib2

        if isinstance(exc, httplib2.HttpLib2Error):
            return True
    except ImportError:  # pragma: no cover
        pass
    # Network-level failures (resets, timeouts, TLS, DNS) are OSError subclasses; local file errors are not retried.
    return isinstance(exc, OSError) and not isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError))
