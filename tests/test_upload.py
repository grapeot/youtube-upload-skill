"""upload --execute against replayed HTTP (googleapiclient.http.HttpMockSequence)."""

import json

import pytest
from conftest import mock_service, resp

from youtube_upload import ledger as L
from youtube_upload.describe import render_description
from youtube_upload.ledger import Ledger, file_sha256
from youtube_upload.meta import load_meta
from youtube_upload.policy import PolicyError
from youtube_upload.upload import Options, UploadRefused, _insert, execute

SESSION = "https://upload.example.invalid/session-1"
CHANNEL = resp(200, {"items": [{"id": "UC_TEST_CHANNEL", "snippet": {"title": "Test channel"},
                                "status": {"longUploadsStatus": "allowed"}}]})


def video_item(meta, visibility="private", **status_overrides):
    status = {"privacyStatus": visibility, "uploadStatus": "uploaded", "selfDeclaredMadeForKids": meta.made_for_kids,
              "madeForKids": meta.made_for_kids, "containsSyntheticMedia": meta.contains_synthetic_media,
              "license": meta.license, "embeddable": meta.embeddable}
    status.update(status_overrides)
    return resp(200, {"items": [{"id": "VID123", "snippet": {
        "title": meta.title, "description": render_description(meta.description, meta.chapters),
        "tags": meta.tags, "categoryId": meta.category, "defaultLanguage": meta.language,
    }, "status": status, "contentDetails": {"duration": "PT5M10S"}, "processingDetails": {"processingStatus": "processing"}}]})


def readback_ok(meta, visibility="private"):
    return [
        video_item(meta, visibility),
        resp(200, {"items": [{"id": "CAP1", "snippet": {"language": "en", "name": "English", "trackKind": "standard"}}]}),
        resp(200, {"items": [{"id": "PLI1"}]}),
    ]


def upload_ok():
    return [
        resp(200, location=SESSION),
        resp(308, range="bytes=0-99"),
        resp(308, range="bytes=0-199"),
        resp(200, {"id": "VID123", "status": {"uploadStatus": "uploaded"}}),
    ]


def follow_ups_ok():
    return [
        resp(200, {"items": [{"default": {"url": "u"}, "high": {"url": "u"}}]}),  # thumbnails.set
        resp(200, {"id": "CAP1"}),                                              # captions.insert
        resp(200, {"items": []}),                                               # playlistItems.list (pre-check)
        resp(200, {"id": "PLI1"}),                                              # playlistItems.insert
    ]


@pytest.fixture
def meta(write_meta, good_meta):
    return load_meta(write_meta(good_meta))


@pytest.fixture
def book(home):
    return Ledger(home.ledger)


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def opts(sleeps):
    return Options(visibility="private", chunk_size=100, sleep=sleeps.append, progress=lambda m: None)


def run(meta, book, opts, responses, duration=310.0):
    service, http = mock_service(responses)
    return execute(meta, lambda: service, book, opts, duration), http


def test_full_upload(meta, book, opts, sleeps):
    out, http = run(meta, book, opts, [CHANNEL, *upload_ok(), *follow_ups_ok(), *readback_ok(meta)])
    assert out["ok"] is True, out
    assert out["video_id"] == "VID123" and out["mode"] == "new"
    assert out["url"] == "https://www.youtube.com/watch?v=VID123"
    assert out["visibility"] == "private"
    assert [s["step"] for s in out["steps"]] == ["thumbnail", "caption:en:English", "playlist"]
    assert out["readback"]["mismatches"] == [] and out["readback"]["ok"] is True
    assert sleeps == []

    seq = http.request_sequence
    init_uri, init_method, init_body, _ = seq[1]
    assert init_method == "POST" and "uploadType=resumable" in init_uri and "notifySubscribers=true" in init_uri
    body = json.loads(init_body)
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["containsSyntheticMedia"] is False and body["status"]["selfDeclaredMadeForKids"] is False
    assert body["snippet"]["description"].endswith("00:00 Intro\n01:30 Middle\n05:00 End")
    ranges = [h.get("Content-Range") for (u, m, b, h) in seq[2:5]]
    assert ranges == ["bytes 0-99/256", "bytes 100-199/256", "bytes 200-255/256"]
    assert all(u == SESSION and m == "PUT" for (u, m, b, h) in seq[2:5])
    assert "thumbnails/set" in seq[5][0] and "captions" in seq[6][0]
    assert "playlistItems" in seq[8][0] and json.loads(seq[8][2])["snippet"]["playlistId"] == meta.playlist_id

    entry = book.latest(file_sha256(meta.video))
    assert entry["state"] == L.UPLOADED and entry["video_id"] == "VID123" and entry["session_uri"] is None
    assert entry["steps"] == {"thumbnail": "done", "caption:en:English": "done", "playlist": "done"}
    assert "session_uri" not in out["ledger"]


def test_unlisted_only_when_requested(meta, book, opts):
    opts.visibility = "unlisted"
    out, http = run(meta, book, opts, [CHANNEL, *upload_ok(), *follow_ups_ok(), *readback_ok(meta, "unlisted")])
    assert out["ok"] is True and out["visibility"] == "unlisted"
    assert json.loads(http.request_sequence[1][2])["status"]["privacyStatus"] == "unlisted"


def test_5xx_mid_upload_queries_status_and_continues(meta, book, opts, sleeps):
    responses = [CHANNEL, resp(200, location=SESSION), resp(308, range="bytes=0-99"), resp(503),
                 resp(308, range="bytes=0-99"),        # status query: server holds 100 bytes
                 resp(308, range="bytes=0-199"),
                 resp(200, {"id": "VID123"}), *follow_ups_ok(), *readback_ok(meta)]
    out, http = run(meta, book, opts, responses)
    assert out["ok"] is True, out
    assert sleeps == [2]
    status_query = http.request_sequence[4]
    assert status_query[1] == "PUT" and status_query[3]["Content-Range"] == "bytes */256"
    assert http.request_sequence[5][3]["Content-Range"] == "bytes 100-199/256"


def test_retries_exhausted_keeps_session_for_resume(meta, book, opts, sleeps):
    opts.max_retries = 2
    out, _ = run(meta, book, opts, [CHANNEL, resp(200, location=SESSION), resp(503), resp(503), resp(503)])
    assert out["ok"] is False and out["error"] == "videos.insert failed"
    assert out["error_detail"]["status"] == 503
    assert "--resume" in out["next"]
    assert sleeps == [2, 4]
    entry = book.latest(file_sha256(meta.video))
    assert entry["state"] == L.UPLOADING and entry["session_uri"] == SESSION
    assert out["ledger"]["has_session"] is True and SESSION not in json.dumps(out)


def test_cross_process_resume_reattaches_to_session(meta, book, opts):
    entry = book.start(file_sha256(meta.video), meta.video, 256, meta.title, "private")
    book.update(entry, session_uri=SESSION)
    opts.resume = True
    responses = [CHANNEL, resp(308, range="bytes=0-199"), resp(200, {"id": "VID123"}), *follow_ups_ok(), *readback_ok(meta)]
    out, http = run(meta, Ledger(book.path), opts, responses)
    assert out["ok"] is True and out["mode"] == "resume_session", out
    seq = http.request_sequence
    assert all(m != "POST" or "upload" not in u for (u, m, b, h) in seq[:3])  # no new session
    assert seq[1][0] == SESSION and seq[1][3]["Content-Range"] == "bytes */256"
    assert seq[2][3]["Content-Range"] == "bytes 200-255/256"


def test_expired_session_is_reported(meta, book, opts):
    entry = book.start(file_sha256(meta.video), meta.video, 256, meta.title, "private")
    book.update(entry, session_uri=SESSION)
    opts.resume = True
    with pytest.raises(UploadRefused, match="expired"):
        run(meta, book, opts, [CHANNEL, resp(404, {"error": {"code": 404, "message": "Not Found"}})])


def test_non_retryable_insert_error_is_passed_through(meta, book, opts, sleeps):
    err = {"error": {"code": 403, "message": "The request cannot be completed because you have exceeded your quota.",
                     "errors": [{"reason": "quotaExceeded", "domain": "youtube.quota"}]}}
    out, _ = run(meta, book, opts, [CHANNEL, resp(403, err)])
    assert out["ok"] is False and sleeps == []
    assert out["error_detail"]["status"] == 403 and out["error_detail"]["reason"] == "quotaExceeded"
    assert "exceeded your quota" in out["error_detail"]["message"]
    assert "--new-upload" in out["next"]


def test_duplicate_is_refused_then_resume_and_new_upload(meta, book, opts):
    run(meta, book, opts, [CHANNEL, *upload_ok(), *follow_ups_ok(), *readback_ok(meta)])
    with pytest.raises(UploadRefused, match="already uploaded as VID123"):
        run(meta, book, opts, [CHANNEL])

    opts.resume = True
    out, http = run(meta, book, opts, [CHANNEL, *readback_ok(meta)])
    assert out["mode"] == "follow_ups" and out["ok"] is True
    assert all(s.get("skipped") for s in out["steps"])
    assert not any("upload" in u for (u, m, b, h) in http.request_sequence)

    opts.resume, opts.new_upload = False, True
    out, _ = run(meta, book, opts, [CHANNEL, *upload_ok(), *follow_ups_ok(), *readback_ok(meta)])
    assert out["mode"] == "new" and len(book.entries) == 2


def test_interrupted_upload_needs_an_explicit_choice(meta, book, opts):
    book.start(file_sha256(meta.video), meta.video, 256, meta.title, "private")
    with pytest.raises(UploadRefused, match="interrupted"):
        run(meta, book, opts, [CHANNEL])


def test_resume_with_other_visibility_is_refused(meta, book, opts):
    book.start(file_sha256(meta.video), meta.video, 256, meta.title, "private")
    opts.resume, opts.visibility = True, "unlisted"
    with pytest.raises(UploadRefused, match="same --visibility"):
        run(meta, book, opts, [])


def test_nothing_to_resume(meta, book, opts):
    opts.resume = True
    with pytest.raises(UploadRefused, match="nothing to resume"):
        run(meta, book, opts, [])


def test_failed_step_does_not_stop_others_and_resume_retries_only_it(meta, book, opts):
    forbidden = resp(403, {"error": {"code": 403, "message": "The authenticated user doesn't have permissions to upload and set custom video thumbnails.",
                                     "errors": [{"reason": "forbidden"}]}})
    out, _ = run(meta, book, opts, [CHANNEL, *upload_ok(), forbidden, *follow_ups_ok()[1:], *readback_ok(meta)])
    assert out["ok"] is False
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["thumbnail"]["ok"] is False and steps["thumbnail"]["error"]["reason"] == "forbidden"
    assert steps["caption:en:English"]["ok"] and steps["playlist"]["ok"]

    opts.resume = True
    out, http = run(meta, book, opts, [CHANNEL, follow_ups_ok()[0], *readback_ok(meta)])
    assert out["ok"] is True, out
    assert [s.get("skipped") is None for s in out["steps"]] == [True, False, False]
    assert sum("thumbnails/set" in u for (u, m, b, h) in http.request_sequence) == 1


def test_caption_retry_checks_for_an_existing_track(meta, book, opts):
    entry = book.start(file_sha256(meta.video), meta.video, 256, meta.title, "private")
    book.update(entry, state=L.UPLOADED, video_id="VID123", steps={"thumbnail": "done", "caption:en:English": "failed", "playlist": "done"})
    opts.resume = True
    existing = resp(200, {"items": [{"id": "CAP1", "snippet": {"language": "en", "name": "English"}}]})
    out, http = run(meta, book, opts, [CHANNEL, existing, *readback_ok(meta)])
    assert out["ok"] is True
    cap = [s for s in out["steps"] if s["step"].startswith("caption")][0]
    assert cap["already_present"] is True
    assert not any(m == "POST" and "captions" in u for (u, m, b, h) in http.request_sequence)


def test_transient_step_error_is_retried(meta, book, opts, sleeps):
    responses = [CHANNEL, *upload_ok(), resp(500), *follow_ups_ok(), *readback_ok(meta)]
    out, _ = run(meta, book, opts, responses)
    assert out["ok"] is True and sleeps == [2]


def test_long_video_needs_phone_verified_channel(meta, book, opts):
    channel = resp(200, {"items": [{"id": "UC_TEST_CHANNEL", "snippet": {"title": "t"}, "status": {"longUploadsStatus": "eligible"}}]})
    with pytest.raises(UploadRefused, match="longer than 15 minutes"):
        run(meta, book, opts, [channel], duration=20 * 60.0)
    assert book.entries == []


def test_no_channel(meta, book, opts):
    with pytest.raises(UploadRefused, match="no YouTube channel"):
        run(meta, book, opts, [resp(200, {"items": []})])


def test_insert_refuses_a_widened_body_before_any_request(meta, book, opts):
    service, http = mock_service([])
    entry = book.start("x", meta.video, 256, meta.title, "private")
    for status in ({"privacyStatus": "public"}, {"privacyStatus": "private", "publishAt": "2030-01-01T00:00:00Z"}):
        with pytest.raises(PolicyError):
            _insert(service, meta, {"snippet": {"title": "t"}, "status": status}, entry, book, opts, None)
    assert http.request_sequence == []


def test_401_is_answered_by_a_token_refresh():
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.http import HttpMockSequence

    from youtube_upload.api import build_service
    from youtube_upload.upload import channel_preflight

    creds = Credentials(token="old-access", refresh_token="refresh", token_uri="https://oauth2.example.invalid/token",
                        client_id="client", client_secret="secret")
    refreshed = []

    def fake_refresh(request):
        refreshed.append(True)
        creds.token = "new-access"

    creds.refresh = fake_refresh
    inner = HttpMockSequence([resp(401, {"error": {"code": 401}}), CHANNEL])
    service = build_service(http=AuthorizedHttp(creds, http=inner))
    assert channel_preflight(service)["id"] == "UC_TEST_CHANNEL"
    assert refreshed == [True]
    auth_headers = [h.get("authorization") or h.get("Authorization") for (u, m, b, h) in inner.request_sequence]
    assert auth_headers == ["Bearer old-access", "Bearer new-access"]


def test_entry_marked_deleted_does_not_block_a_new_upload(meta, book, opts):
    entry = book.start(file_sha256(meta.video), meta.video, 256, meta.title, "private")
    book.update(entry, state="deleted", video_id="VID000")
    out, _ = run(meta, book, opts, [CHANNEL, *upload_ok(), *follow_ups_ok(), *readback_ok(meta)])
    assert out["ok"] is True and out["mode"] == "new"
