"""Visibility policy. This is the only module allowed to name visibility values.

The tool uploads private by default, unlisted only when a run asks for it, and never
public or scheduled: the channel owner publishes or schedules in YouTube Studio.
tests/test_source_scan.py fails if the literal for the public visibility or the
scheduled-publish field appears anywhere else under src/.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_VISIBILITY = "private"
ALLOWED_VISIBILITIES = ("private", "unlisted")

# Named here only so they can be refused.
_FORBIDDEN_VISIBILITY = "public"
_FORBIDDEN_STATUS_FIELDS = ("publishAt",)

# Metadata files cannot carry a visibility or a schedule: visibility is a per-run flag,
# publishing and scheduling happen in YouTube Studio.
REFUSED_META_KEYS = (
    "visibility", "privacy", "privacy_status", "privacyStatus", "status",
    "schedule", "publish_at", "publishAt", "publish_time", "scheduled_at",
)


class PolicyError(ValueError):
    """A request would widen visibility or schedule publication."""


def check_visibility(value: Any) -> str:
    if value not in ALLOWED_VISIBILITIES:
        if value == _FORBIDDEN_VISIBILITY:
            raise PolicyError(
                "this tool never makes a video visible to everyone; upload private or unlisted "
                "and publish it in YouTube Studio"
            )
        raise PolicyError(f"visibility must be one of {list(ALLOWED_VISIBILITIES)}, got {value!r}")
    return value


def check_status_body(status: Any) -> None:
    """Refuse a videos.insert status part that is not private/unlisted or carries a publish time."""
    if not isinstance(status, dict):
        raise PolicyError("status part must be an object")
    for key in _FORBIDDEN_STATUS_FIELDS:
        if key in status:
            raise PolicyError("this tool never schedules publication; schedule it in YouTube Studio")
    check_visibility(status.get("privacyStatus"))


def check_insert_body(body: Any) -> None:
    if not isinstance(body, dict) or "status" not in body:
        raise PolicyError("videos.insert body must carry an explicit status part")
    check_status_body(body["status"])


_PRIVACY_IN_BODY = re.compile(rb'"privacyStatus"\s*:\s*"([^"]*)"')


def check_request_body(body: Any) -> None:
    """Transport-level backstop: refuse any metadata body that widens visibility or schedules."""
    if body is None or hasattr(body, "read"):
        return  # media stream chunks carry no metadata
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if b'"' + _FORBIDDEN_STATUS_FIELDS[0].encode() + b'"' in raw:
        raise PolicyError("refusing to send a request that schedules publication")
    for m in _PRIVACY_IN_BODY.finditer(raw):
        check_visibility(m.group(1).decode("utf-8", "replace"))
