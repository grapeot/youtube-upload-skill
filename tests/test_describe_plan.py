import pytest

from youtube_upload.describe import description_problems, format_timestamp, render_description
from youtube_upload.meta import Chapter, MetaError, load_meta
from youtube_upload.plan import build_plan, insert_body
from youtube_upload.policy import PolicyError

CH = [Chapter(0, "Intro"), Chapter(90, "Middle"), Chapter(300, "End")]


def test_render_description_snapshot():
    assert render_description("Hello.\n\n", CH) == "Hello.\n\n00:00 Intro\n01:30 Middle\n05:00 End"


def test_render_without_description_or_chapters():
    assert render_description("", CH) == "00:00 Intro\n01:30 Middle\n05:00 End"
    assert render_description("Just text.  ", []) == "Just text."


def test_hour_formatting():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(3599) == "59:59"
    assert format_timestamp(3723) == "1:02:03"
    out = render_description("", [Chapter(0, "a"), Chapter(1800, "b"), Chapter(3723, "c")])
    assert out.splitlines() == ["00:00 a", "30:00 b", "1:02:03 c"]


def test_existing_timestamps_in_description_are_refused():
    assert "already contains a line starting with a timestamp" in description_problems("0:00 Start\nmore", CH, None)[0]
    assert description_problems("At 0:00 nothing happens", CH, None) == []
    assert description_problems("0:00 Start", [], None) == []


def test_description_byte_limit_counts_chapters():
    text = "é" * 2490  # 4980 bytes; the 38-byte chapter block pushes it over 5000
    assert "UTF-8 bytes with chapters > 5000" in description_problems(text, CH, None)[0]
    assert description_problems(text, [], None) == []


def test_chapter_end_checked_against_duration():
    assert "after the video ends" in description_problems("", CH, 299.0)[0]
    assert "needs at least 10 s" in description_problems("", CH, 305.0)[0]
    assert description_problems("", CH, 310.0) == []


def test_insert_body_private_by_default(write_meta, good_meta):
    meta = load_meta(write_meta(good_meta))
    body = insert_body(meta, "private")
    assert body["status"] == {
        "privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False,
        "license": "youtube", "embeddable": True,
    }
    assert body["snippet"]["categoryId"] == "28" and body["snippet"]["defaultLanguage"] == "en"
    assert body["snippet"]["description"].endswith("00:00 Intro\n01:30 Middle\n05:00 End")


def test_insert_body_unlisted_when_requested(write_meta, good_meta):
    meta = load_meta(write_meta(good_meta))
    assert insert_body(meta, "unlisted")["status"]["privacyStatus"] == "unlisted"


@pytest.mark.parametrize("value", ["public", "PRIVATE", "", None])
def test_insert_body_refuses_other_visibilities(write_meta, good_meta, value):
    meta = load_meta(write_meta(good_meta))
    with pytest.raises(PolicyError):
        insert_body(meta, value)


def test_plan_steps_and_quota(write_meta, good_meta):
    meta = load_meta(write_meta(good_meta))
    plan = build_plan(meta, "private", 400.0)
    assert plan["steps"] == ["videos.insert", "thumbnail", "caption:en:English", "playlist", "readback"]
    # preflight 1 + videos.list 1 + thumbnail 50 + caption 400 + captions.list 50 + playlist 1+50+1
    assert plan["quota"]["shared_units"] == 554
    assert plan["quota"]["uploads_bucket_calls"] == 1
    assert plan["visibility"] == "private"
    assert plan["disclosures"] == {"made_for_kids": False, "contains_synthetic_media": False}
    assert not any("visibility is" in w for w in plan["warnings"])


def test_plan_warnings(write_meta, good_meta):
    good_meta.pop("category")
    good_meta["audio_language"] = "en"
    meta = load_meta(write_meta(good_meta))
    w = " | ".join(build_plan(meta, "unlisted", None)["warnings"])
    for needle in ("ffprobe unavailable", "no category", "audio_language is not a documented writable field",
                   "verified channel", "visibility is unlisted"):
        assert needle in w
    assert "longer than 15 minutes" in " ".join(build_plan(meta, "private", 16 * 60.0)["warnings"])


def test_plan_raises_description_problems(write_meta, good_meta):
    meta = load_meta(write_meta(good_meta))
    with pytest.raises(MetaError, match="after the video ends"):
        build_plan(meta, "private", 100.0)
