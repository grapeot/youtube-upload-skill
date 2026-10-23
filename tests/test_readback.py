from conftest import mock_service, resp
from test_upload import readback_ok, video_item

from youtube_upload.meta import load_meta
from youtube_upload.readback import readback


def test_readback_matches(write_meta, good_meta):
    meta = load_meta(write_meta(good_meta))
    service, http = mock_service(readback_ok(meta))
    out = readback(service, "VID123", meta, "private")
    assert out["ok"] is True and out["mismatches"] == [] and out["unverified"] == []
    assert out["captions"][0]["language"] == "en" and out["playlist"]["contains_video"] is True
    assert "part=snippet%2Cstatus%2CcontentDetails%2CprocessingDetails" in http.request_sequence[0][0]


def test_readback_reports_mismatches_unverified_and_not_applied(write_meta, good_meta):
    good_meta["audio_language"] = "en"
    meta = load_meta(write_meta(good_meta))
    item = video_item(meta, "unlisted")
    body = __import__("json").loads(item[1])
    body["items"][0]["snippet"]["title"] = "Other"
    body["items"][0]["snippet"]["tags"] = ["tag2", "tag one"]  # order does not matter
    del body["items"][0]["status"]["containsSyntheticMedia"]
    responses = [(item[0], __import__("json").dumps(body)), resp(200, {"items": []}), resp(200, {"items": []})]
    service, _ = mock_service(responses)
    out = readback(service, "VID123", meta, "private")
    assert out["ok"] is False
    fields = {m["field"] for m in out["mismatches"]}
    assert fields == {"title", "visibility", "captions", "playlist"}
    assert out["unverified"] == ["contains_synthetic_media"]
    assert out["not_applied"][0]["field"] == "audio_language"


def test_readback_without_meta_is_a_plain_read(write_meta, good_meta):
    meta = load_meta(write_meta(good_meta))
    service, http = mock_service([video_item(meta)])
    out = readback(service, "VID123")
    assert out["ok"] is True and out["fields"]["visibility"] == "private" and "mismatches" not in out
    assert len(http.request_sequence) == 1


def test_readback_not_found_and_failed_upload(write_meta, good_meta):
    service, _ = mock_service([resp(200, {"items": []})])
    assert readback(service, "NOPE")["ok"] is False
    meta = load_meta(write_meta(good_meta))
    service, _ = mock_service([video_item(meta, uploadStatus="rejected", rejectionReason="duplicate")])
    out = readback(service, "VID123")
    assert out["ok"] is False and out["fields"]["rejection_reason"] == "duplicate"
