import pytest
from pathlib import Path

import anghami

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


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


def test_parse_playlist_page_real_fixture():
    result = anghami.parse_playlist_page(_read("playlist_page.html"))
    assert result["name"]                       # non-empty playlist name
    assert len(result["tracks"]) > 0
    # every track has a title; artists are present on this chart playlist
    for t in result["tracks"]:
        assert t["title"]
        assert isinstance(t["artist"], str)
    # the embedded list is complete: declared count matches parsed count
    assert result["declared_total"] == len(result["tracks"])


def test_parse_playlist_page_deleted():
    with pytest.raises(anghami.PlaylistNotFound):
        anghami.parse_playlist_page(_read("deleted_page.html"))


def test_parse_playlist_page_garbage():
    with pytest.raises(anghami.ParseError):
        anghami.parse_playlist_page("<html><body>hello</body></html>")


def test_parse_playlist_page_musicplaylist_without_tracks():
    html = ('<html><head><script type="application/ld+json">'
            '{"@type": "MusicPlaylist", "name": "Empty", "track": []}'
            "</script></head><body></body></html>")
    with pytest.raises(anghami.ParseError):
        anghami.parse_playlist_page(html)
