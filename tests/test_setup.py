"""Guided setup: client file handling, console guard, browser helpers (all offline)."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from youtube_upload import browser as B
from youtube_upload import clientfile, console
from youtube_upload import setup as S
from youtube_upload.paths import mode


def client(path: Path, kind: str, project: str = "my-test-project") -> Path:
    path.write_text(json.dumps({kind: {"client_id": "123-abc.apps.example", "client_secret": "FAKE-SECRET",
                                       "project_id": project}}), encoding="utf-8")
    return path


def test_describe_kinds_without_secrets(tmp_path):
    d = clientfile.describe(client(tmp_path / "a.json", "installed"))
    assert d == {"kind": "installed", "project_id": "my-test-project", "client_id_prefix": "123-..."}
    assert clientfile.describe(client(tmp_path / "b.json", "web"))["kind"] == "web"
    (tmp_path / "c.json").write_text("not json")
    assert clientfile.describe(tmp_path / "c.json")["kind"] is None
    assert "FAKE-SECRET" not in json.dumps(d)


def test_find_downloads_newest_first(tmp_path):
    import os
    old = client(tmp_path / "client_secret_1.json", "web")
    new = client(tmp_path / "client_secret_2.json", "installed")
    os.utime(old, (1_000_000, 1_000_000))
    (tmp_path / "other.json").write_text("{}")
    found = clientfile.find_downloads(tmp_path)
    assert [Path(f["path"]).name for f in found] == [new.name, old.name]
    assert clientfile.find_downloads(tmp_path / "missing") == []


def test_install_refuses_web_client(home, tmp_path):
    with pytest.raises(clientfile.InstallError, match="not a Desktop app"):
        clientfile.install(home, client(tmp_path / "w.json", "web"))
    assert not home.client_secret.exists()


def test_install_backs_up_the_replaced_file(home, tmp_path):
    home.root.mkdir(parents=True)
    client(home.client_secret, "web")
    src = client(tmp_path / "d.json", "installed")
    res = clientfile.install(home, src, now=datetime(2026, 1, 2, 3, 4, 5))
    assert res["backup"].endswith("client_secret.json.bak-20260102-030405") and res["kind"] == "installed"
    assert clientfile.describe(Path(res["backup"]))["kind"] == "web"
    assert clientfile.describe(home.client_secret)["kind"] == "installed"
    assert mode(home.client_secret) == 0o600 and mode(Path(res["backup"])) == 0o600 and mode(home.root) == 0o700
    assert src.exists()  # copied, not moved
    again = clientfile.install(home, src, now=datetime(2026, 1, 2, 3, 4, 5))
    assert again["unchanged"] is True and again["backup"] is None


def test_install_backup_names_do_not_collide(home, tmp_path):
    home.root.mkdir(parents=True)
    t = datetime(2026, 1, 2, 3, 4, 5)
    for i in range(3):
        client(home.client_secret, "web", project=f"p{i}x0")
        clientfile.install(home, client(tmp_path / f"d{i}.json", "installed", project=f"q{i}x0"), now=t)
    assert len(list(home.root.glob("client_secret.json.bak-*"))) == 3


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    d = tmp_path / "Downloads"
    d.mkdir()
    monkeypatch.setattr(clientfile, "find_downloads", lambda directory=None, _d=d: _find(_d))
    return d


def _find(d):
    out = []
    for p in sorted(d.glob(clientfile.DOWNLOAD_GLOB)):
        out.append({"path": str(p), "modified": "", **clientfile.describe(p)})
    return out


def test_install_client_from_downloads_filters_project(home, downloads):
    client(downloads / "client_secret_a.json", "installed", project="other-project")
    out = S.cmd_install_client(home, None, "wanted-project")
    assert out["ok"] is False and "for project wanted-project" in out["error"]
    client(downloads / "client_secret_b.json", "installed", project="wanted-project")
    out = S.cmd_install_client(home, None, "wanted-project")
    assert out["ok"] is True and out["project_id"] == "wanted-project"
    assert S.suggested_project(home) == "other-project"  # first download with a project ID


def test_step_urls_and_plan():
    assert console.step_url("api", "my-test-project") == \
        "https://console.cloud.google.com/apis/library/youtube.googleapis.com?project=my-test-project"
    assert [s["step"] for s in console.plan("my-test-project")] == list(console.ORDER)
    with pytest.raises(ValueError):
        console.step_url("billing", "my-test-project")
    with pytest.raises(ValueError):
        console.step_url("api", "Bad Project!")


@pytest.mark.parametrize("url,ok", [
    ("https://console.cloud.google.com/auth/branding?project=x", True),
    ("https://accounts.google.com/signin", False),
    ("https://myaccount.google.com/", False),
    ("https://payments.google.com/", False),
    ("https://example.com/", False),
])
def test_check_page(url, ok):
    if ok:
        console.check_page(url)
    else:
        with pytest.raises(console.GuardError):
            console.check_page(url)


@pytest.mark.parametrize("text", ["Delete", "Delete project", "Remove", "Disable API", "Reset secret", "Billing",
                                  "Back to testing", "Make internal", "Shut down", "Add or remove scopes", "Upgrade"])
def test_destructive_controls_are_refused(text):
    with pytest.raises(console.GuardError):
        console.check_control(text)


@pytest.mark.parametrize("text", ["Enable", "Save", "Publish app", "Confirm", "Create client", "Desktop app",
                                  "Download JSON", "Get started", "Next", "Create", "Continue"])
def test_setup_controls_are_allowed(text):
    console.check_control(text)


def test_project_from_url():
    assert console.project_from_url("https://console.cloud.google.com/x?project=abc-123&y=1") == "abc-123"
    assert console.project_from_url("https://console.cloud.google.com/x") is None


def test_browser_matches(tmp_path):
    prof = tmp_path / "profile"
    line = f"/Chrome --remote-debugging-port=9351 --user-data-dir={prof} --no-first-run"
    assert B.browser_matches([line], 9351, prof)
    assert not B.browser_matches([line], 9352, prof)
    assert not B.browser_matches([line.replace(str(prof), str(prof) + "-other")], 9351, prof)


def test_new_tab_url_encodes():
    assert B.new_tab_url("http://127.0.0.1:9351", "https://a.example/x?y=1&z=2") == \
        "http://127.0.0.1:9351/json/new?https%3A%2F%2Fa.example%2Fx%3Fy%3D1%26z%3D2"


def test_ensure_window_reopens_when_all_windows_were_closed(monkeypatch, home):
    session = B.session_for(home)
    opened = []
    monkeypatch.setattr(B, "page_targets", lambda s: [])
    monkeypatch.setattr(B, "open_in_session", lambda s, url: opened.append(url))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    assert B.ensure_window(session) is True and opened == [B.CONSOLE_URL]
    monkeypatch.setattr(B, "page_targets", lambda s: [{"type": "page"}])
    assert B.ensure_window(session) is False


def test_driving_without_a_browser_suggests_relaunch(home, monkeypatch):
    monkeypatch.setattr(B, "port_in_use", lambda port: False)
    out = S.cmd_snapshot(B.session_for(home))
    assert out["ok"] is False and "relaunch" in out["hint"]


def test_session_browser_controller_opens_in_setup_chrome(monkeypatch, home):
    session = B.session_for(home)
    seen = []
    monkeypatch.setattr(B, "open_in_session", lambda s, url: seen.append(url))
    import webbrowser
    name = B.register_session_browser(session)
    assert webbrowser.get(name).open("https://accounts.example/consent") is True
    assert seen == ["https://accounts.example/consent"]
