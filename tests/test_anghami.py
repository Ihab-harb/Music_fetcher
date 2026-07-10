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


# Finding 1 tests: lowercase "numtracks" key (real Anghami pages emit this)
def test_parse_playlist_page_lowercase_numtracks():
    """Real Anghami pages use lowercase 'numtracks', not 'numTracks'.
    This test ensures truncation detection works when declared_total < len(tracks)."""
    html = ('<html><head><script type="application/ld+json">'
            '{"@type": "MusicPlaylist", "name": "Test", '
            '"numtracks": 5, '
            '"track": ['
            '  {"name": "Song 1", "byArtist": {"name": "Artist 1"}},'
            '  {"name": "Song 2", "byArtist": {"name": "Artist 2"}}'
            ']}'
            "</script></head><body></body></html>")
    result = anghami.parse_playlist_page(html)
    assert result["declared_total"] == 5, f"Expected declared_total=5, got {result['declared_total']}"
    assert len(result["tracks"]) == 2


def test_parse_playlist_page_real_fixture_declares_50():
    """The frozen fixture's MusicPlaylist block declares 'numtracks': 50.
    Verify that parsed declared_total captures this."""
    result = anghami.parse_playlist_page(_read("playlist_page.html"))
    assert result["declared_total"] == 50, f"Expected declared_total=50, got {result['declared_total']}"
    assert len(result["tracks"]) > 0


# Finding 2 tests: single track as dict instead of list
def test_parse_playlist_page_single_track_as_dict():
    """Per schema.org, a single-cardinality value may be a bare object instead of a 1-element array.
    Ensure we normalize and parse it as 1 track instead of crashing with AttributeError."""
    html = ('<html><head><script type="application/ld+json">'
            '{"@type": "MusicPlaylist", "name": "SingleTrackTest", '
            '"track": {"name": "Only Song", "byArtist": {"name": "Solo Artist"}}'
            "}"
            "</script></head><body></body></html>")
    result = anghami.parse_playlist_page(html)
    assert len(result["tracks"]) == 1
    assert result["tracks"][0]["title"] == "Only Song"
    assert result["tracks"][0]["artist"] == "Solo Artist"


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise anghami.requests.HTTPError(f"HTTP {self.status_code}")


def test_fetch_happy_path(monkeypatch):
    html = _read("playlist_page.html")
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse(200, html)

    monkeypatch.setattr(anghami.requests, "get", fake_get)
    result = anghami.fetch_anghami_playlist(" https://play.anghami.com/playlist/6471050 ")
    assert result["url"] == "https://play.anghami.com/playlist/6471050"
    assert len(result["tracks"]) > 0
    assert captured["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert captured["timeout"] == 15


def test_fetch_invalid_url_no_network(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("network must not be touched for an invalid URL")

    monkeypatch.setattr(anghami.requests, "get", explode)
    with pytest.raises(anghami.InvalidUrl):
        anghami.fetch_anghami_playlist("https://example.com/playlist/1")


def test_fetch_404_maps_to_not_found(monkeypatch):
    monkeypatch.setattr(anghami.requests, "get", lambda *a, **k: _FakeResponse(404, ""))
    with pytest.raises(anghami.PlaylistNotFound):
        anghami.fetch_anghami_playlist("https://play.anghami.com/playlist/1")


def test_fetch_network_error_propagates(monkeypatch):
    def boom(*a, **k):
        raise anghami.requests.ConnectionError("dns fail")

    monkeypatch.setattr(anghami.requests, "get", boom)
    with pytest.raises(anghami.requests.RequestException):
        anghami.fetch_anghami_playlist("https://play.anghami.com/playlist/1")
