"""yt-upload command-line entry. Every subcommand prints exactly one JSON object to stdout."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from . import __version__
from .policy import ALLOWED_VISIBILITIES, DEFAULT_VISIBILITY, PolicyError


def _emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # usage errors are JSON too
        _emit({"ok": False, "error": f"usage: {message}"})
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="yt-upload", description="Upload a local video to your own YouTube channel (private by default).")
    ap.add_argument("--version", action="store_true", help="print the version")
    ap.add_argument("--home", help="config directory (default $YT_UPLOAD_HOME or ~/.youtube-upload)")
    sub = ap.add_subparsers(dest="cmd", parser_class=_Parser)

    a = sub.add_parser("auth", help="authorize in the browser and store a token")
    a.add_argument("--timeout", type=int, default=300, help="seconds to wait for the browser consent (default 300)")
    a.add_argument("--no-browser", action="store_true", help="print the consent URL to stderr instead of opening a browser")

    sub.add_parser("status", help="token, channel and long-upload status (read-only)")

    u = sub.add_parser("upload", help="upload a video; dry-run unless --execute")
    u.add_argument("--meta", required=True, help="metadata JSON file")
    u.add_argument("--execute", action="store_true", help="really upload (needs the owner's approval for this video)")
    u.add_argument("--visibility", choices=ALLOWED_VISIBILITIES, default=DEFAULT_VISIBILITY,
                   help="private (default) or unlisted, for this run only")
    u.add_argument("--resume", action="store_true", help="continue an interrupted upload or unfinished follow-up steps")
    u.add_argument("--new-upload", action="store_true", help="upload this file again even though it was uploaded before")

    r = sub.add_parser("readback", help="read a video back; compare with metadata when --meta is given (read-only)")
    r.add_argument("--video-id", required=True)
    r.add_argument("--meta")
    r.add_argument("--visibility", choices=ALLOWED_VISIBILITIES, help="expected visibility")

    c = sub.add_parser("categories", help="list assignable video categories (read-only)")
    c.add_argument("--region", default="US", help="ISO 3166-1 region code (default US)")

    s = sub.add_parser("setup", help="guided one-time Google Cloud setup in a visible Chrome")
    s.add_argument("--port", type=int, help="debugging port (default $YT_UPLOAD_CDP_PORT or 9351)")
    s.add_argument("--profile-dir", help="Chrome profile (default <home>/browser-profile)")
    ss = s.add_subparsers(dest="setup_cmd", parser_class=_Parser)
    b = ss.add_parser("browser", help="launch the visible setup Chrome")
    b.add_argument("--project", help="open this project's dashboard")
    ss.add_parser("status", help="local files, downloaded clients, browser and sign-in state (read-only)")
    pl = ss.add_parser("plan", help="the setup steps and their console URLs")
    pl.add_argument("--project")
    g = ss.add_parser("goto", help="open a setup step page and read it back")
    g.add_argument("step", choices=["project", "api", "branding", "audience", "clients"])
    g.add_argument("--project", required=True, help="Google Cloud project ID")
    ss.add_parser("snapshot", help="read the current setup page (read-only)")
    k = ss.add_parser("click", help="click one visible control by its label (guarded)")
    k.add_argument("--text", required=True)
    k.add_argument("--role", choices=["button", "link", "menuitem", "option", "radio", "tab", "checkbox", "combobox"])
    f = ss.add_parser("fill", help="type into one visible field by its label (guarded)")
    f.add_argument("--label", required=True)
    f.add_argument("--value", required=True)
    i = ss.add_parser("install-client", help="install a Desktop client JSON as <home>/client_secret.json")
    i.add_argument("--file", help="path to the JSON (default: newest Desktop client in ~/Downloads)")
    i.add_argument("--project", help="only accept a client from this project")
    sa = ss.add_parser("auth", help="OAuth consent in the setup Chrome; stores the token")
    sa.add_argument("--timeout", type=int, default=300)
    return ap


def _service(home) -> Any:
    from .api import build_service
    from .auth import load_credentials

    return build_service(credentials=load_credentials(home))


def cmd_auth(args, home) -> dict[str, Any]:
    from .api import build_service, error_details
    from .auth import run_flow
    from .upload import UploadRefused, channel_preflight

    creds = run_flow(home, timeout=args.timeout, open_browser=not args.no_browser)
    out: dict[str, Any] = {"ok": True, "token_path": str(home.token)}
    try:
        out["channel"] = channel_preflight(build_service(credentials=creds))
    except UploadRefused as e:  # authorized, but the account has no channel
        out.update(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001 - e.g. the API is not enabled in the Cloud project
        out.update(ok=False, error="authorized, but the channel check failed", error_detail=error_details(e))
    return out


def cmd_status(args, home) -> dict[str, Any]:
    from .auth import AuthError, credential_status
    from .ledger import Ledger, public_view
    from .upload import channel_preflight

    out: dict[str, Any] = {"ok": False, **credential_status(home)}
    book = Ledger(home.ledger)
    out["uploads_recorded"] = len(book.entries)
    out["interrupted_uploads"] = [public_view(e) for e in book.entries if e.get("state") == "uploading"]
    try:
        service = _service(home)
    except AuthError as e:
        out.update({"token_valid": False, "needs_auth": True, "error": str(e)})
        return out
    out["token_valid"] = True
    try:
        out["channel"] = channel_preflight(service)
    except Exception as e:  # noqa: BLE001 - keep the fields collected so far
        from .api import error_details
        from .upload import UploadRefused

        out["error"] = str(e) if isinstance(e, UploadRefused) else "channel check failed"
        if not isinstance(e, UploadRefused):
            out["error_detail"] = error_details(e)
        return out
    out["ok"] = True
    return out


def cmd_upload(args, home) -> dict[str, Any]:
    from .ledger import Ledger, file_sha256, public_view
    from .meta import load_meta
    from .plan import build_plan
    from .probe import video_duration
    from .upload import Options, execute

    meta = load_meta(args.meta)
    duration = video_duration(meta.video)
    book = Ledger(home.ledger)
    if not args.execute:
        plan = build_plan(meta, args.visibility, duration)
        sha = file_sha256(meta.video)
        prior = book.latest(sha)
        return {
            "ok": True, "dry_run": True, "meta": meta.to_json(), "sha256": sha,
            "previous_upload": public_view(prior), **plan,
            "next": "after the owner approves this upload, rerun with --execute",
        }
    opts = Options(visibility=args.visibility, resume=args.resume, new_upload=args.new_upload)
    out = execute(meta, lambda: _service(home), book, opts, duration)
    return {"dry_run": False, **out}


def cmd_readback(args, home) -> dict[str, Any]:
    from .meta import load_meta
    from .readback import readback

    meta = load_meta(args.meta) if args.meta else None
    return readback(_service(home), args.video_id, meta, args.visibility)


def cmd_categories(args, home) -> dict[str, Any]:
    resp = _service(home).videoCategories().list(part="snippet", regionCode=args.region).execute()
    cats = [
        {"id": i.get("id"), "title": i.get("snippet", {}).get("title")}
        for i in resp.get("items") or [] if i.get("snippet", {}).get("assignable")
    ]
    return {"ok": True, "region": args.region, "categories": cats}


def cmd_setup(args, home) -> dict[str, Any]:
    from . import console
    from . import setup as S
    from .browser import session_for

    session = session_for(home, args.port, args.profile_dir)
    sc = args.setup_cmd
    if sc is None:
        return {"ok": False, "error": "usage: setup needs a step (browser, status, plan, goto, snapshot, click, fill, install-client, auth)"}
    if sc == "browser":
        return S.cmd_browser(home, session, args.project)
    if sc == "status":
        return S.cmd_status(home, session)
    if sc == "plan":
        project = args.project or S.suggested_project(home)
        if not project:
            return {"ok": False, "error": "no project ID: pass --project or download a client JSON first"}
        return {"ok": True, "project": project, "steps": console.plan(project),
                "then": ["yt-upload setup install-client", "yt-upload setup auth", "yt-upload status"]}
    if sc == "goto":
        return S.cmd_goto(session, args.step, args.project)
    if sc == "snapshot":
        return S.cmd_snapshot(session)
    if sc == "click":
        return S.cmd_click(session, args.text, args.role)
    if sc == "fill":
        return S.cmd_fill(session, args.label, args.value)
    if sc == "install-client":
        return S.cmd_install_client(home, args.file, args.project)
    return S.cmd_auth(home, session, args.timeout)


COMMANDS: dict[str, Callable[..., dict[str, Any]]] = {
    "auth": cmd_auth, "status": cmd_status, "upload": cmd_upload,
    "readback": cmd_readback, "categories": cmd_categories, "setup": cmd_setup,
}


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        _emit({"ok": True, "version": __version__})
        return 0
    if args.cmd is None:
        _emit({"ok": False, "error": f"usage: a command is required ({', '.join(COMMANDS)})"})
        return 2
    if args.cmd == "setup" and args.setup_cmd is None:
        _emit({"command": "setup", "ok": False, "error": "usage: setup needs a step (browser, status, plan, goto, "
               "snapshot, click, fill, install-client, auth)"})
        return 2
    from .api import error_details
    from .auth import AuthError
    from .clientfile import InstallError
    from .console import GuardError
    from .meta import MetaError
    from .paths import ConfigError, resolve_home
    from .upload import UploadRefused

    out: dict[str, Any]
    try:
        home = resolve_home(args.home)
        out = COMMANDS[args.cmd](args, home)
    except MetaError as e:
        out = {"ok": False, "error": "invalid metadata", "problems": e.problems}
    except AuthError as e:
        out = {"ok": False, "error": str(e), "needs_auth": e.needs_auth}
    except UploadRefused as e:
        out = {"ok": False, "error": str(e), **e.extra}
    except (PolicyError, ConfigError, GuardError, InstallError, ValueError) as e:
        out = {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - one JSON object, whatever happens
        out = {"ok": False, "error": f"{args.cmd} failed", "error_detail": error_details(e)}
    out = {"command": args.cmd, **out}
    _emit(out)
    return 0 if out.get("ok") else 1


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))
