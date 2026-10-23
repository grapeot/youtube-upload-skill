"""`yt-upload setup ...`: guided one-time configuration in a visible Chrome.

The human signs in to Google and clicks the OAuth consent; the agent does the rest through
the guarded primitives in console.py, reading the page back after every action.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import browser as B
from . import clientfile, console
from .auth import credential_status
from .paths import Home


def _local_state(home: Home) -> dict[str, Any]:
    state = credential_status(home)
    if home.client_secret.is_file():
        state["client_secret"] = clientfile.describe(home.client_secret)
    state["downloaded_clients"] = clientfile.find_downloads()
    return state


def suggested_project(home: Home) -> str | None:
    """Project ID from the newest downloaded client, else from the installed client file."""
    for c in clientfile.find_downloads():
        if c.get("project_id"):
            return c["project_id"]
    if home.client_secret.is_file():
        return clientfile.describe(home.client_secret).get("project_id")
    return None


def cmd_browser(home: Home, session: B.Session, project: str | None) -> dict[str, Any]:
    url = console.step_url("project", project) if project else B.CONSOLE_URL
    if B.port_in_use(session.port):
        own = B.verify_own_browser(session)
        if not own["ok"]:
            return own
        reopened = B.ensure_window(session, url)
        return {"ok": True, "already_running": True, "reopened_window": reopened, "cdp_endpoint": session.endpoint,
                "profile_dir": str(session.profile_dir),
                "next": "run `yt-upload setup status`; the profile keeps the Google sign-in"}
    out = B.launch(session, url)
    if out.get("ok"):
        out["next"] = ("Ask the human to sign in to Google in the Chrome window that just opened "
                       "(the account that owns the YouTube channel), then run `yt-upload setup status`.")
    return out


def _driven_page(ctx, create: bool = True):
    pages = [p for p in ctx.pages if (console.CONSOLE_HOST in p.url) or ("accounts.google.com" in p.url)]
    if pages:
        return pages[-1]
    if ctx.pages and ctx.pages[-1].url in ("about:blank", "chrome://newtab/"):
        return ctx.pages[-1]
    return ctx.new_page() if create else None


def _with_page(session: B.Session, fn, create: bool = True) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    own = B.verify_own_browser(session)
    if not own["ok"]:
        if not B.port_in_use(session.port):
            own["hint"] = ("the setup browser is not running (closed or quit); run `yt-upload setup browser` "
                           "to relaunch it with the same profile, which keeps the Google sign-in")
        return own
    reopened = B.ensure_window(session)
    with sync_playwright() as p:
        _, ctx = B.connect(p, session)
        page = _driven_page(ctx, create)
        if page is None:
            return {"ok": True, "page": None}
        page.bring_to_front()
        out = fn(page)
        if reopened:
            out["reopened_window"] = True  # the human had closed the window; Chrome was still running
        return out


def cmd_status(home: Home, session: B.Session) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "local": _local_state(home), "suggested_project": suggested_project(home)}
    if not B.port_in_use(session.port):
        out["browser"] = {"running": False, "hint": "run `yt-upload setup browser`"}
        return out

    def look(page):
        snap = console.snapshot(page)
        signed_in = snap.get("host") == console.CONSOLE_HOST and not any(
            b["text"].lower() == "sign in" for b in snap.get("buttons", []))
        return {"running": True, "host": snap.get("host"), "path": snap.get("path"),
                "project": snap.get("project"), "signed_in": signed_in,
                "human_page": bool(snap.get("human_page"))}

    br = _with_page(session, look, create=False)
    out["browser"] = br
    if br.get("ok") is False:
        out["ok"] = False
    elif br.get("human_page"):
        out["next"] = "the window shows a Google sign-in or consent page: the human completes it"
    return out


def cmd_goto(session: B.Session, step: str, project: str) -> dict[str, Any]:
    url = console.step_url(step, project)

    def go(page):
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001 - the console keeps long-polling; a timeout is normal
            pass
        page.wait_for_timeout(1500)
        snap = console.snapshot(page)
        result = {"ok": True, "step": step, "goal": console.STEPS[step]["goal"], "page": snap}
        if snap.get("project") and snap["project"] != project:
            result["ok"] = False
            result["error"] = f"the console switched to project {snap['project']!r}, expected {project!r}"
        return result

    return _with_page(session, go)


def cmd_snapshot(session: B.Session) -> dict[str, Any]:
    return _with_page(session, lambda page: {"ok": True, "page": console.snapshot(page)})


def cmd_click(session: B.Session, text: str, role: str | None) -> dict[str, Any]:
    def act(page):
        res = console.click(page, text, role)
        return {"ok": True, **res, "page": console.snapshot(page)}
    return _with_page(session, act)


def cmd_fill(session: B.Session, label: str, value: str) -> dict[str, Any]:
    def act(page):
        res = console.fill(page, label, value)
        return {"ok": True, **res, "page": console.snapshot(page)}
    return _with_page(session, act)


def cmd_install_client(home: Home, file: str | None, project: str | None) -> dict[str, Any]:
    if file:
        source = Path(file).expanduser()
        if not source.is_file():
            return {"ok": False, "error": f"file not found: {source}"}
        found = clientfile.describe(source).get("project_id")
        if project and found != project:
            return {"ok": False, "error": f"{source.name} belongs to project {found!r}, not {project!r}"}
    else:
        candidates = [c for c in clientfile.find_downloads() if c["kind"] == "installed"
                      and (project is None or c.get("project_id") == project)]
        if not candidates:
            return {"ok": False, "error": "no downloaded Desktop client JSON found in ~/Downloads"
                    + (f" for project {project}" if project else ""),
                    "downloaded_clients": clientfile.find_downloads()}
        source = Path(candidates[0]["path"])
    res = clientfile.install(home, source)
    return {"ok": True, "source": str(source), **res,
            "note": "the downloaded file was copied, not moved; the previous file, if any, is kept as the backup",
            "next": ["ask the human whether to move the downloaded copy to the trash (it holds the client secret)",
                     "run `yt-upload setup auth`"]}


def cmd_auth(home: Home, session: B.Session, timeout: int) -> dict[str, Any]:
    from .api import build_service, error_details
    from .auth import run_flow
    from .upload import UploadRefused, channel_preflight

    own = B.verify_own_browser(session)
    if not own["ok"]:
        return own
    name = B.register_session_browser(session)
    creds = run_flow(home, timeout=timeout, open_browser=True, browser=name)
    out: dict[str, Any] = {"ok": True, "token_path": str(home.token)}
    try:
        out["channel"] = channel_preflight(build_service(credentials=creds))
    except UploadRefused as e:
        out.update(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        out.update(ok=False, error="authorized, but the channel check failed", error_detail=error_details(e))
    return out
