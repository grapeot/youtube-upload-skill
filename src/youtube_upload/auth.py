"""OAuth for an installed (Desktop) app with a loopback redirect. Tokens are never printed."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from typing import Any

from .paths import Home, harden, write_private

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPES = [SCOPE]


class AuthError(RuntimeError):
    """No usable credentials. `needs_auth` tells the caller to run `yt-upload auth`."""

    def __init__(self, message: str, needs_auth: bool = True):
        super().__init__(message)
        self.needs_auth = needs_auth


def _client_secret_kind(home: Home) -> str | None:
    """'installed' for a Desktop client, 'web' for a web client, None if unreadable."""
    try:
        data = json.loads(home.client_secret.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for kind in ("installed", "web"):
        if isinstance(data, dict) and kind in data:
            return kind
    return None


def token_scopes(home: Home) -> list[str]:
    try:
        raw = json.loads(home.token.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    scopes = raw.get("scopes") if isinstance(raw, dict) else None
    if isinstance(scopes, str):
        return scopes.split()
    if isinstance(scopes, list):
        return [str(s) for s in scopes]
    return []


def run_flow(home: Home, timeout: int | None = None, open_browser: bool = True, browser: str | None = None):
    """Interactive consent in the browser; stores the token (0600). Returns Credentials.

    `browser` names a registered `webbrowser` controller (the guided setup registers one that
    opens the consent page in its own visible Chrome).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    harden(home)
    if not home.client_secret.is_file():
        raise AuthError(
            f"OAuth client file not found: {home.client_secret}. Create a Desktop app OAuth client in "
            "Google Cloud console and save its JSON there.",
        )
    kind = _client_secret_kind(home)
    if kind != "installed":
        raise AuthError(
            "client_secret.json is not a Desktop app OAuth client "
            f"(found {kind or 'an unreadable file'}); create one with application type 'Desktop app'",
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(home.client_secret), SCOPES)
    kwargs: dict[str, Any] = {"port": 0, "open_browser": open_browser}
    if timeout is not None:
        kwargs["timeout_seconds"] = timeout
    if browser is not None:
        kwargs["browser"] = browser
    # The flow prints the consent URL; keep stdout for the single JSON result.
    with redirect_stdout(sys.stderr):
        creds = flow.run_local_server(**kwargs)
    if not creds.has_scopes(SCOPES):
        raise AuthError("Google did not grant the youtube.force-ssl scope")
    write_private(home.token, creds.to_json())
    return creds


def load_credentials(home: Home, refresh: bool = True):
    """Load stored credentials, refreshing (and re-saving) them when expired."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    harden(home)
    if not home.token.is_file():
        raise AuthError(f"no token at {home.token}; run `yt-upload auth`")
    if SCOPE not in token_scopes(home):
        raise AuthError("stored token lacks the youtube.force-ssl scope; run `yt-upload auth`")
    try:
        creds = Credentials.from_authorized_user_file(str(home.token), SCOPES)
    except (ValueError, KeyError) as e:
        raise AuthError(f"stored token is unreadable ({type(e).__name__}); run `yt-upload auth`") from None
    if creds.valid or not refresh:
        return creds
    if not creds.refresh_token:
        raise AuthError("stored token has no refresh token; run `yt-upload auth`")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        # e.g. invalid_grant: revoked, password changed, unused for six months, or a Testing-mode
        # consent screen (7-day expiry). The message from Google carries no secret.
        raise AuthError(f"token refresh failed: {_short(e)}; run `yt-upload auth`") from None
    write_private(home.token, creds.to_json())
    return creds


def _short(e: Exception) -> str:
    text = str(e)
    return text if len(text) <= 300 else text[:300] + "..."


def credential_status(home: Home) -> dict[str, Any]:
    """File-level status without network access and without revealing any secret."""
    return {
        "home": str(home.root),
        "client_secret_present": home.client_secret.is_file(),
        "client_secret_kind": _client_secret_kind(home) if home.client_secret.is_file() else None,
        "token_present": home.token.is_file(),
        "token_has_scope": SCOPE in token_scopes(home),
    }
