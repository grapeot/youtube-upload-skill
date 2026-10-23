import pytest

from youtube_upload.meta import MetaError, load_meta, parse_meta, parse_timestamp, tags_length


def problems(write_meta, data) -> str:
    with pytest.raises(MetaError) as ei:
        load_meta(write_meta(data))
    return " | ".join(ei.value.problems)


def test_good_meta_resolves_relative_paths(write_meta, good_meta, media):
    meta = load_meta(write_meta(good_meta))
    assert meta.video == (media / "video.mp4").resolve()
    assert meta.cover == (media / "cover.jpg").resolve()
    assert meta.subtitles[0].language == "en" and meta.subtitles[0].name == "English"
    assert [c.start for c in meta.chapters] == [0, 90, 300]
    assert meta.license == "youtube" and meta.embeddable is True and meta.notify_subscribers is True


def test_minimal_meta(write_meta):
    meta = load_meta(write_meta({"video": "video.mp4", "title": "t", "made_for_kids": False,
                                 "contains_synthetic_media": True}))
    assert meta.chapters == [] and meta.subtitles == [] and meta.cover is None and meta.playlist_id is None
    assert meta.contains_synthetic_media is True


@pytest.mark.parametrize("key", ["made_for_kids", "contains_synthetic_media"])
def test_disclosures_are_required_without_default(write_meta, good_meta, key):
    del good_meta[key]
    assert f"{key}: required" in problems(write_meta, good_meta)


@pytest.mark.parametrize("key", ["made_for_kids", "contains_synthetic_media"])
def test_disclosures_must_be_booleans(write_meta, good_meta, key):
    good_meta[key] = "no"
    assert f"{key}: must be true or false" in problems(write_meta, good_meta)


@pytest.mark.parametrize("key,value", [
    ("visibility", "unlisted"), ("privacyStatus", "private"), ("schedule", "2030-01-01T00:00Z"),
    ("publish_at", "2030-01-01T00:00Z"), ("status", {}),
])
def test_visibility_and_schedule_keys_are_refused(write_meta, good_meta, key, value):
    good_meta[key] = value
    text = problems(write_meta, good_meta)
    assert "visibility and scheduling cannot be set in metadata" in text
    assert "unknown keys" not in text


def test_collects_all_problems(write_meta):
    text = problems(write_meta, {"video": "missing.mp4", "title": "x" * 101, "extra": 1, "license": "cc"})
    for needle in ("unknown keys: ['extra']", "video: file not found", "title: 101 characters",
                   "made_for_kids: required", "contains_synthetic_media: required", "license:"):
        assert needle in text, needle


def test_title_limits(write_meta, good_meta):
    good_meta["title"] = "x" * 100
    assert load_meta(write_meta(good_meta)).title == "x" * 100
    for bad, needle in [("a<b", "< or >"), ("line\nbreak", "line breaks"), ("  ", "title: required")]:
        good_meta["title"] = bad
        assert needle in problems(write_meta, good_meta)


def test_title_is_stripped(write_meta, good_meta):
    good_meta["title"] = "  padded  "
    assert load_meta(write_meta(good_meta)).title == "padded"


def test_description_rejects_angle_brackets(write_meta, good_meta):
    good_meta["description"] = "see <here>"
    assert "description: must not contain < or >" in problems(write_meta, good_meta)


@pytest.mark.parametrize("chapters,needle", [
    ([{"start": 0, "title": "a"}, {"start": 20, "title": "b"}], "at least 3"),
    ([{"start": 5, "title": "a"}, {"start": 20, "title": "b"}, {"start": 40, "title": "c"}], "start at 0"),
    ([{"start": 0, "title": "a"}, {"start": 20, "title": "b"}, {"start": 20, "title": "c"}], "strictly increasing"),
    ([{"start": 0, "title": "a"}, {"start": 9, "title": "b"}, {"start": 40, "title": "c"}], "at least 10 s"),
    ([{"start": "1:99", "title": "a"}], "bad timestamp"),
    ([{"start": 1.5, "title": "a"}], "whole seconds"),
    ([{"start": 0}], "exactly start and title"),
    ([{"start": 0, "title": " "}], "empty title"),
    ([{"start": 0, "title": "a<b"}], "line breaks, < or >"),
    ("00:00 a", "must be a list"),
])
def test_chapter_rules(write_meta, good_meta, chapters, needle):
    good_meta["chapters"] = chapters
    assert needle in problems(write_meta, good_meta)


def test_chapter_exactly_ten_seconds_is_fine(write_meta, good_meta):
    good_meta["chapters"] = [{"start": 0, "title": "a"}, {"start": 10, "title": "b"}, {"start": 20, "title": "c"}]
    assert len(load_meta(write_meta(good_meta)).chapters) == 3


def test_tags_length_rule():
    assert tags_length(["Foo-Baz"]) == 7
    assert tags_length(["Foo Baz"]) == 9
    assert tags_length(["a", "b", "c"]) == 5
    assert tags_length([]) == 0


def test_tags_total_limit_at_boundary(write_meta, good_meta):
    good_meta["tags"] = ["x" * 250, "y" * 249]  # 250 + 1 comma + 249 = 500
    assert load_meta(write_meta(good_meta)).tags[0] == "x" * 250
    good_meta["tags"] = ["x" * 250, "y" * 250]
    assert "total length 501 > 500" in problems(write_meta, good_meta)


@pytest.mark.parametrize("tags,needle", [
    (["a,b"], "comma"), ([""], "empty tag"), (["Dup", "dup"], "duplicates"), ("a,b", "list of strings"),
    (["a>b"], "contains <, >"),
])
def test_tag_rules(write_meta, good_meta, tags, needle):
    good_meta["tags"] = tags
    assert needle in problems(write_meta, good_meta)


@pytest.mark.parametrize("value,ok", [("28", True), (28, True), ("Science", False), (True, False)])
def test_category(write_meta, good_meta, value, ok):
    good_meta["category"] = value
    if ok:
        assert load_meta(write_meta(good_meta)).category == "28"
    else:
        assert "category:" in problems(write_meta, good_meta)


@pytest.mark.parametrize("value,ok", [("en", True), ("zh-Hans", True), ("pt-BR", True), ("english", False), ("e", False)])
def test_language_codes(write_meta, good_meta, value, ok):
    good_meta["audio_language"] = value
    if ok:
        assert load_meta(write_meta(good_meta)).audio_language == value
    else:
        assert "audio_language:" in problems(write_meta, good_meta)


def test_cover_must_be_jpg_or_png(write_meta, good_meta, media):
    (media / "cover.gif").write_bytes(b"GIF89a")
    good_meta["cover"] = "cover.gif"
    assert "unsupported extension '.gif'" in problems(write_meta, good_meta)


def test_empty_file_is_refused(write_meta, good_meta, media):
    (media / "empty.mp4").write_bytes(b"")
    good_meta["video"] = "empty.mp4"
    assert "file is empty" in problems(write_meta, good_meta)


@pytest.mark.parametrize("subs,needle", [
    ({"path": "subs.en.srt", "language": "en"}, "must be a list"),
    ([{"path": "subs.en.srt"}], "language: required"),
    ([{"path": "subs.en.srt", "language": "en", "default_on": True}], "optional name"),
    ([{"path": "subs.en.srt", "language": "en"}, {"path": "subs.en.srt", "language": "EN"}], "duplicate language"),
    ([{"path": "subs.ass", "language": "en"}], "unsupported extension"),
])
def test_subtitle_rules(write_meta, good_meta, subs, needle):
    good_meta["subtitles"] = subs
    assert needle in problems(write_meta, good_meta)


def test_vtt_captions_accepted(write_meta, good_meta, media):
    (media / "subs.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHi\n", encoding="utf-8")
    good_meta["subtitles"] = [{"path": "subs.vtt", "language": "zh-Hans"}]
    assert load_meta(write_meta(good_meta)).subtitles[0].name == ""


@pytest.mark.parametrize("value,needle", [
    ("https://www.youtube.com/playlist?list=PLabcdefghijk", "not the URL"),
    ("short", "existing playlist ID"),
])
def test_playlist_id_shape(write_meta, good_meta, value, needle):
    good_meta["playlist_id"] = value
    assert needle in problems(write_meta, good_meta)


def test_check_files_false_skips_existence(tmp_path, good_meta):
    meta = parse_meta(good_meta, base_dir=tmp_path / "nowhere", check_files=False)
    assert meta.title == "Test title"


def test_invalid_json_and_missing_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(MetaError, match="invalid JSON"):
        load_meta(bad)
    with pytest.raises(MetaError, match="not found"):
        load_meta(tmp_path / "absent.json")


@pytest.mark.parametrize("value,seconds", [(0, 0), (90, 90), ("01:30", 90), ("1:02:03", 3723), (5.0, 5)])
def test_parse_timestamp(value, seconds):
    assert parse_timestamp(value) == seconds


@pytest.mark.parametrize("value", [-1, "abc", True, None, "1:60", "1:60:00", float("nan")])
def test_parse_timestamp_rejects(value):
    with pytest.raises(ValueError):
        parse_timestamp(value)
