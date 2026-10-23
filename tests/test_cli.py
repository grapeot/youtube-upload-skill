"""CLI contract: exactly one JSON object on stdout for every command; no secrets in any output."""

import json
import subprocess
import sys

import pytest
from conftest import mock_service, resp
from test_auth_paths import FAKE_TOKEN
from test_upload import CHANNEL, SESSION, follow_ups_ok, readback_ok, upload_ok

from youtube_upload import cli
from youtube_upload.meta import load_meta
from youtube_upload.paths import write_private

SECRETS = ("ya29.", "1//", "FAKE-SECRET", "FAKE-REFRESH", SESSION)


def run_subprocess(*args, home):
    p = subprocess.run([sys.executable, "-m", "youtube_upload", "--home", str(home.root), *args],
                       capture_output=True, text=True, timeout=60)
    return p.returncode, json.loads(p.stdout), p


def run_inproc(capsys, *args):
    code = cli.run(list(args))
    out = capsys.readouterr().out
    assert not any(s in out for s in SECRETS), out
    return code, json.loads(out)


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network client built during a dry run")
    monkeypatch.setattr("youtube_upload.api.build_service", boom)
    monkeypatch.setattr("youtube_upload.cli._service", boom)


@pytest.fixture
def fake_service(monkeypatch):
    holder = {}

    def install(responses):
        service, http = mock_service(responses)
        monkeypatch.setattr("youtube_upload.cli._service", lambda home: service)
        holder["http"] = http
        return http
    return install


@pytest.mark.parametrize("args,code", [
    ([], 2), (["upload"], 2), (["upload", "--meta", "x.json", "--visibility", "public"], 2), (["bogus"], 2),
])
def test_usage_errors_are_json(home, args, code):
    rc, out, p = run_subprocess(*args, home=home)
    assert rc == code and out["ok"] is False and out["error"].startswith("usage")


def test_version(home):
    rc, out, _ = run_subprocess("--version", home=home)
    assert rc == 0 and out["version"]


def test_status_without_token_says_run_auth(home):
    rc, out, _ = run_subprocess("status", home=home)
    assert rc == 1 and out["command"] == "status" and out["needs_auth"] is True and out["token_present"] is False


def test_home_inside_git_repo_is_refused(tmp_path, capsys):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    code, out = run_inproc(capsys, "--home", str(tmp_path / "repo" / "cfg"), "status")
    assert code == 1 and "inside a git repository" in out["error"]


def test_invalid_metadata_lists_problems(home, write_meta, capsys):
    p = write_meta({"video": "video.mp4", "title": "t"})
    code, out = run_inproc(capsys, "--home", str(home.root), "upload", "--meta", str(p))
    assert code == 1 and out["error"] == "invalid metadata"
    assert any("made_for_kids" in x for x in out["problems"])


def test_dry_run_makes_no_network_call(home, write_meta, good_meta, capsys, no_network):
    p = write_meta(good_meta)
    code, out = run_inproc(capsys, "--home", str(home.root), "upload", "--meta", str(p))
    assert code == 0 and out["dry_run"] is True and out["visibility"] == "private"
    assert out["request"]["body"]["status"]["privacyStatus"] == "private"
    assert out["previous_upload"] is None and "--execute" in out["next"]
    assert not home.ledger.exists()


def test_dry_run_unlisted(home, write_meta, good_meta, capsys, no_network):
    p = write_meta(good_meta)
    code, out = run_inproc(capsys, "--home", str(home.root), "upload", "--meta", str(p), "--visibility", "unlisted")
    assert code == 0 and out["request"]["body"]["status"]["privacyStatus"] == "unlisted"


def test_status_with_token_prints_no_secret(home, capsys, fake_service):
    write_private(home.token, json.dumps(FAKE_TOKEN))
    fake_service([CHANNEL])
    code, out = run_inproc(capsys, "--home", str(home.root), "status")
    assert code == 0 and out["channel"]["id"] == "UC_TEST_CHANNEL" and out["token_valid"] is True


def test_execute_end_to_end_prints_one_object_without_secrets(home, write_meta, good_meta, capsys, fake_service):
    p = write_meta(good_meta)
    meta = load_meta(p)
    fake_service([CHANNEL, *upload_ok(), *follow_ups_ok(), *readback_ok(meta)])
    code, out = run_inproc(capsys, "--home", str(home.root), "upload", "--meta", str(p), "--execute")
    assert code == 0 and out["ok"] is True and out["dry_run"] is False and out["video_id"] == "VID123"
    assert out["human_steps"]
    # the duplicate guard now refuses the same file
    fake_service([CHANNEL])
    code, out = run_inproc(capsys, "--home", str(home.root), "upload", "--meta", str(p), "--execute")
    assert code == 1 and "already uploaded" in out["error"] and out["video_id"] == "VID123"


def test_failed_execute_hides_session_url(home, write_meta, good_meta, capsys, fake_service):
    p = write_meta(good_meta)
    fake_service([CHANNEL, resp(200, location=SESSION), resp(400, {"error": {"code": 400, "message": "Bad Request",
                                                                          "errors": [{"reason": "invalidTitle"}]}})])
    code, out = run_inproc(capsys, "--home", str(home.root), "upload", "--meta", str(p), "--execute")
    assert code == 1 and out["error_detail"]["reason"] == "invalidTitle" and out["ledger"]["has_session"] is True


def test_readback_and_categories(home, write_meta, good_meta, capsys, fake_service):
    p = write_meta(good_meta)
    meta = load_meta(p)
    fake_service(readback_ok(meta))
    code, out = run_inproc(capsys, "--home", str(home.root), "readback", "--video-id", "VID123", "--meta", str(p),
                           "--visibility", "private")
    assert code == 0 and out["mismatches"] == []
    http = fake_service([resp(200, {"items": [
        {"id": "28", "snippet": {"title": "Science & Technology", "assignable": True}},
        {"id": "18", "snippet": {"title": "Short Movies", "assignable": False}},
    ]})])
    code, out = run_inproc(capsys, "--home", str(home.root), "categories", "--region", "GB")
    assert code == 0 and out["categories"] == [{"id": "28", "title": "Science & Technology"}]
    assert "regionCode=GB" in http.request_sequence[0][0]


def test_unexpected_error_is_still_one_json_object(home, write_meta, good_meta, capsys, monkeypatch):
    p = write_meta(good_meta)
    monkeypatch.setitem(cli.COMMANDS, "upload", lambda args, home: (_ for _ in ()).throw(RuntimeError("boom")))
    code, out = run_inproc(capsys, "--home", str(home.root), "upload", "--meta", str(p))
    assert code == 1 and out["error"] == "upload failed" and out["error_detail"]["message"] == "boom"


def test_setup_without_a_step_is_a_usage_error(home, capsys):
    code, out = run_inproc(capsys, "--home", str(home.root), "setup")
    assert code == 2 and out["error"].startswith("usage")


def test_status_keeps_fields_when_the_channel_check_fails(home, capsys, fake_service):
    write_private(home.token, json.dumps(FAKE_TOKEN))
    fake_service([resp(403, {"error": {"code": 403, "message": "YouTube Data API v3 has not been used",
                                        "errors": [{"reason": "accessNotConfigured"}]}})])
    code, out = run_inproc(capsys, "--home", str(home.root), "status")
    assert code == 1 and out["token_valid"] is True and out["token_present"] is True
    assert out["error_detail"]["reason"] == "accessNotConfigured"
