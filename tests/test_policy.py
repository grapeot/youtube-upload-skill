import pytest

from youtube_upload.policy import (ALLOWED_VISIBILITIES, DEFAULT_VISIBILITY, PolicyError, check_insert_body,
                                   check_status_body, check_visibility)


def test_defaults():
    assert DEFAULT_VISIBILITY == "private"
    assert ALLOWED_VISIBILITIES == ("private", "unlisted")


@pytest.mark.parametrize("value", ["private", "unlisted"])
def test_allowed(value):
    assert check_visibility(value) == value
    check_status_body({"privacyStatus": value})


def test_public_is_refused_with_a_studio_hint():
    with pytest.raises(PolicyError, match="publish it in YouTube Studio"):
        check_visibility("public")


@pytest.mark.parametrize("status", [
    {"privacyStatus": "private", "publishAt": "2030-01-01T00:00:00Z"},
    {"privacyStatus": "public"},
    {},
    "private",
])
def test_status_bodies_refused(status):
    with pytest.raises(PolicyError):
        check_status_body(status)


def test_insert_body_needs_explicit_status():
    with pytest.raises(PolicyError, match="explicit status"):
        check_insert_body({"snippet": {"title": "t"}})
