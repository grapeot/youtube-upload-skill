import json
import stat

import pytest

from youtube_upload import auth
from youtube_upload.auth import SCOPE, AuthError, credential_status, load_credentials, run_flow
from youtube_upload.paths import ConfigError, Home, mode, resolve_home, write_private

FAKE_TOKEN = {"token": "ya29.FAKE-ACCESS", "refresh_token": "1//FAKE-REFRESH", "client_id": "cid.apps.example",
              "client_secret": "FAKE-SECRET", "token_uri": "https://oauth2.example.invalid/token", "scopes": [SCOPE],
              "expiry": "2000-01-01T00:00:00Z"}


def desktop_client(home: Home):
    write_private(home.client_secret, json.dumps({"installed": {"client_id": "cid", "client_secret": "s",
                                                                "auth_uri": "https://a.example", "token_uri": "https://t.example"}}))


def test_write_private_modes(tmp_path):
    p = tmp_path / "cfg" / "token.json"
    write_private(p, "{}")
    assert mode(p) == 0o600 and mode(p.parent) == 0o700


def test_home_inside_git_repo_is_refused(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(ConfigError, match="inside a git repository"):
        resolve_home(str(tmp_path / "repo" / "secrets"))


def test_home_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_UPLOAD_HOME", str(tmp_path / "x"))
    assert resolve_home().root == tmp_path / "x"
    assert resolve_home(str(tmp_path / "y")).root == tmp_path / "y"


def test_status_without_files(home):
    s = credential_status(home)
    assert s["client_secret_present"] is False and s["token_present"] is False and s["token_has_scope"] is False


def test_load_credentials_needs_token(home):
    with pytest.raises(AuthError, match="run `yt-upload auth`"):
        load_credentials(home)


def test_load_credentials_rejects_missing_scope(home):
    write_private(home.token, json.dumps({**FAKE_TOKEN, "scopes": ["https://www.googleapis.com/auth/youtube.readonly"]}))
    with pytest.raises(AuthError, match="lacks the youtube.force-ssl scope"):
        load_credentials(home)


def test_refresh_rewrites_token_privately(home, monkeypatch):
    write_private(home.token, json.dumps(FAKE_TOKEN))
    home.token.chmod(0o644)

    def fake_refresh(self, request):
        self.token = "ya29.REFRESHED"
        self.expiry = None

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", fake_refresh)
    creds = load_credentials(home)
    assert creds.token == "ya29.REFRESHED"
    assert stat.S_IMODE(home.token.stat().st_mode) == 0o600
    assert json.loads(home.token.read_text())["token"] == "ya29.REFRESHED"


def test_refresh_failure_says_run_auth_without_secrets(home, monkeypatch):
    from google.auth.exceptions import RefreshError

    write_private(home.token, json.dumps(FAKE_TOKEN))

    def fail(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", fail)
    with pytest.raises(AuthError) as ei:
        load_credentials(home)
    assert "invalid_grant" in str(ei.value) and "yt-upload auth" in str(ei.value)
    assert "FAKE" not in str(ei.value)


def test_run_flow_needs_desktop_client(home):
    with pytest.raises(AuthError, match="client file not found"):
        run_flow(home)
    write_private(home.client_secret, json.dumps({"web": {"client_id": "x"}}))
    with pytest.raises(AuthError, match="not a Desktop app"):
        run_flow(home)


def test_run_flow_stores_token_and_keeps_stdout_clean(home, monkeypatch, capsys):
    desktop_client(home)

    class FakeCreds:
        def has_scopes(self, scopes):
            return scopes == [SCOPE]

        def to_json(self):
            return json.dumps(FAKE_TOKEN)

    class FakeFlow:
        def run_local_server(self, **kwargs):
            assert kwargs["port"] == 0 and kwargs["open_browser"] is False
            print("Please visit this URL to authorize this application: https://accounts.example/auth")
            return FakeCreds()

    monkeypatch.setattr("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
                        classmethod(lambda cls, path, scopes: FakeFlow()))
    run_flow(home, open_browser=False)
    captured = capsys.readouterr()
    assert captured.out == "" and "authorize" in captured.err
    assert stat.S_IMODE(home.token.stat().st_mode) == 0o600
    assert auth.token_scopes(home) == [SCOPE]
