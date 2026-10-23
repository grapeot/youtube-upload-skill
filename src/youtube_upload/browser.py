"""Visible Chrome session for the guided setup: launch, own-browser check, CDP connection.

Chrome runs with a dedicated persistent profile and a debugging port bound to 127.0.0.1.
The tool attaches with Playwright's connect_over_cdp and only disconnects when done; it never
closes the browser. Nothing here reads, prints or exports cookies or storage, and nothing
types into a Google sign-in page: the human signs in.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import ConfigError, Home, enclosing_git_repo

DEFAULT_PORT = 9351
CONSOLE_URL = "https://console.cloud.google.com/"
SINGLETON_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
BROWSER_NAME = "yt-upload-setup-chrome"


@dataclass
class Session:
    port: int
    profile_dir: Path

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def session_for(home: Home, port: int | None = None, profile_dir: str | None = None) -> Session:
    port = port or int(os.environ.get("YT_UPLOAD_CDP_PORT", DEFAULT_PORT))
    prof = Path(profile_dir or os.environ.get("YT_UPLOAD_PROFILE_DIR") or str(home.root / "browser-profile")).expanduser()
    repo = enclosing_git_repo(prof)
    if repo is not None:  # the profile holds a signed-in Google session
        raise ConfigError(f"browser profile {prof} is inside a git repository ({repo}); put it outside any repository")
    return Session(port=port, profile_dir=prof)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def profile_in_use(profile: Path) -> bool:
    return any(os.path.lexists(profile / n) for n in SINGLETON_FILES)


def find_chrome() -> str | None:
    explicit = os.environ.get("YT_UPLOAD_CHROME")
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def launch(session: Session, url: str = CONSOLE_URL) -> dict[str, Any]:
    if port_in_use(session.port):
        return {"ok": False, "error": f"port {session.port} is already in use; run `yt-upload setup status`, "
                "or pick another port with --port / YT_UPLOAD_CDP_PORT"}
    if profile_in_use(session.profile_dir):
        return {"ok": False, "error": "the setup profile is locked by a running Chrome", "profile_dir": str(session.profile_dir)}
    chrome = find_chrome()
    if not chrome:
        return {"ok": False, "error": "Chrome not found; set YT_UPLOAD_CHROME to its executable"}
    created = not session.profile_dir.exists()
    session.profile_dir.mkdir(parents=True, exist_ok=True)
    if created or session.profile_dir.stat().st_uid == os.getuid():
        os.chmod(session.profile_dir, 0o700)  # only the profile itself; never its parent
    log_path = session.profile_dir / "yt-upload-browser.log"
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={session.port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={session.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1400,1000",
                url,
            ],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,  # the browser outlives the shell that started it
        )
    os.chmod(log_path, 0o600)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return {"ok": False, "error": "Chrome exited early", "log": str(log_path)}
        try:
            with urllib.request.urlopen(f"{session.endpoint}/json/version", timeout=2) as r:
                json.loads(r.read())
            return {"ok": True, "pid": proc.pid, "cdp_endpoint": session.endpoint,
                    "profile_dir": str(session.profile_dir), "url": url}
        except Exception:  # noqa: BLE001 - wait for the port
            time.sleep(0.3)
    return {"ok": False, "error": "the debugging endpoint did not come up within 20 s", "log": str(log_path)}


def browser_matches(cmdlines: list[str], port: int, profile_dir: Path) -> bool:
    """Pure: is there a process started with both this debugging port and this profile?"""
    want_port = f"--remote-debugging-port={port}"
    pats = [re.compile(re.escape(f"--user-data-dir={d}") + r"(\s|$)") for d in {profile_dir, profile_dir.resolve()}]
    return any(want_port in line.split() and any(pt.search(line) for pt in pats) for line in cmdlines)


def listening_pids(port: int) -> list[int] | None:
    """PIDs listening on 127.0.0.1:<port>, or None when lsof is unavailable."""
    exe = shutil.which("lsof")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [int(x) for x in out.split() if x.isdigit()]


def _cmdlines(pids: list[int] | None) -> list[str]:
    if pids is not None:
        lines = []
        for pid in pids:
            r = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
            lines += r.stdout.splitlines()
        return lines
    flags = ["-axww"] if sys.platform == "darwin" else ["-eww"]
    return subprocess.run(["ps", *flags, "-o", "args="], capture_output=True, text=True, timeout=10).stdout.splitlines()


def verify_own_browser(session: Session) -> dict[str, Any]:
    """Refuse to drive a Chrome that this tool did not start with this profile.

    The process that owns the listening port (via lsof when available) must carry both the
    debugging-port flag and this profile directory on its command line.
    """
    if not port_in_use(session.port):
        return {"ok": False, "error": "no setup browser is running; run `yt-upload setup browser`"}
    try:
        lines = _cmdlines(listening_pids(session.port) or None)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"cannot list processes to verify the browser: {type(e).__name__}"}
    if browser_matches(lines, session.port, session.profile_dir):
        return {"ok": True}
    return {"ok": False, "error": f"the browser on port {session.port} was not started with the setup profile; "
            "refusing to drive it"}


def connect(p, session: Session):
    """Attach to the running Chrome. Callers only let Playwright exit; the browser stays open."""
    browser = p.chromium.connect_over_cdp(session.endpoint, timeout=10000)
    if not browser.contexts:
        raise RuntimeError("browser has no default context")
    return browser, browser.contexts[0]


def page_targets(session: Session) -> list[dict[str, Any]]:
    """Open tabs of the setup Chrome (DevTools HTTP endpoint)."""
    with urllib.request.urlopen(f"{session.endpoint}/json/list", timeout=5) as r:
        return [t for t in json.loads(r.read()) if t.get("type") == "page"]


def ensure_window(session: Session, url: str = CONSOLE_URL) -> bool:
    """On macOS, closing the last window leaves Chrome running with no tabs, and CDP attach then
    fails. Open a fresh window in that case. Returns True when a window had to be reopened."""
    if page_targets(session):
        return False
    open_in_session(session, url)
    time.sleep(1.0)
    return True


def new_tab_url(endpoint: str, url: str) -> str:
    return f"{endpoint}/json/new?{urllib.parse.quote(url, safe='')}"


def open_in_session(session: Session, url: str) -> None:
    """Open `url` in a new tab of the setup Chrome (DevTools HTTP endpoint, PUT /json/new)."""
    req = urllib.request.Request(new_tab_url(session.endpoint, url), method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read()


class _SessionBrowser(webbrowser.BaseBrowser):
    """A `webbrowser` controller so the OAuth flow opens its consent URL in the setup Chrome."""

    def __init__(self, session: Session):
        super().__init__(BROWSER_NAME)
        self.session = session

    def open(self, url, new=0, autoraise=True):  # noqa: ARG002 - webbrowser signature
        try:
            open_in_session(self.session, url)
            return True
        except OSError:
            return False


def register_session_browser(session: Session) -> str:
    webbrowser.register(BROWSER_NAME, None, _SessionBrowser(session), preferred=False)
    return BROWSER_NAME
