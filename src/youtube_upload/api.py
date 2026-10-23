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


# The only write endpoints the tool may call (method, path). Everything else except GET is refused
# at the transport, so a code path that reaches another endpoint fails before anything is sent.
ALLOWED_WRITES = {
    ("POST", "/upload/youtube/v3/videos"),
    ("PUT", "/upload/youtube/v3/videos"),          # resumable session chunks and status queries
    ("POST", "/upload/youtube/v3/thumbnails/set"),
    ("POST", "/upload/youtube/v3/captions"),
    ("POST", "/youtube/v3/playlistItems"),
}


class TransportPolicyError(RuntimeError):
    pass


class GuardedHttp:
    """httplib2-compatible wrapper enforcing ALLOWED_WRITES and the visibility policy on every request."""

    def __init__(self, inner: Any):
        self._inner = inner

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        from urllib.parse import urlsplit

        from .policy import check_request_body

        m = (method or "GET").upper()
        if m != "GET":
            path = urlsplit(uri).path
            if (m, path) not in ALLOWED_WRITES:
                raise TransportPolicyError(f"refusing {m} {path}: not one of the tool's write endpoints")
            check_request_body(body)
        return self._inner.request(uri, method=method, body=body, headers=headers, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def build_service(credentials: Any = None, http: Any = None):
    """Build the youtube v3 client from the library's bundled discovery document (no network).

    All traffic goes through GuardedHttp, including the mocked transport used in tests.
    """
    from googleapiclient.discovery import build

    if http is None:
        import google_auth_httplib2
        from googleapiclient.http import build_http

        http = google_auth_httplib2.AuthorizedHttp(credentials, http=build_http())
    return build("youtube", "v3", http=GuardedHttp(http), cache_discovery=False, static_discovery=True)


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
