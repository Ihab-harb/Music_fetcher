import pytest

import anghami


@pytest.mark.parametrize("url,expected_id", [
    ("https://play.anghami.com/playlist/6471050", "6471050"),
    ("http://play.anghami.com/playlist/6471050", "6471050"),
    ("https://play.anghami.com/playlist/6471050?refer=share", "6471050"),
    ("  https://play.anghami.com/playlist/6471050  ", "6471050"),
])
def test_validate_playlist_url_valid(url, expected_id):
    assert anghami.validate_playlist_url(url) == expected_id


@pytest.mark.parametrize("url", [
    "",
    "not a url",
    "https://play.anghami.com/song/123456",          # a song, not a playlist
    "https://play.anghami.com/artist/123456",
    "https://open.spotify.com/playlist/abc",          # wrong host
    "https://evil.com/play.anghami.com/playlist/1",
    "https://play.anghami.com/playlist/",             # no id
    "https://play.anghami.com/playlist/abc",          # non-numeric id
])
def test_validate_playlist_url_invalid(url):
    with pytest.raises(anghami.InvalidUrl):
        anghami.validate_playlist_url(url)
