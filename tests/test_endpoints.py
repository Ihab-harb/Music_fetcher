import json

import pytest
from fastapi.testclient import TestClient

import main


# TrustedHostMiddleware rejects the default "testserver" host — must use 127.0.0.1.
@pytest.fixture()
def client():
    return TestClient(main.app, base_url="http://127.0.0.1")


class _FakeSpotify:
    pass


@pytest.fixture()
def spotify_and_cache(monkeypatch):
    """Authenticated fake Spotify + in-memory cache + canned search results."""
    monkeypatch.setattr(main, "get_spotify", lambda: _FakeSpotify())
    monkeypatch.setattr(main, "load_cache", lambda: {})
    monkeypatch.setattr(main, "save_cache", lambda cache: None)

    def fake_search(sp, artist, title):
        if title == "findme":
            return {"uri": "spotify:track:1", "id": "1", "name": "Found",
                    "artist": "Artist", "album": "Album"}
        return None

    monkeypatch.setattr(main, "search_spotify_track", fake_search)


def _sse_events(response_text):
    return [json.loads(line[len("data: "):])
            for line in response_text.splitlines() if line.startswith("data: ")]


def test_library_search_stream_shape(client, spotify_and_cache, monkeypatch):
    songs = [
        {"path": "x", "filename": "a.mp3", "folder": "x", "artist": "A", "title": "findme", "album": ""},
        {"path": "y", "filename": "b.mp3", "folder": "y", "artist": "B", "title": "nope", "album": ""},
    ]
    monkeypatch.setattr(main, "scan_music_folders", lambda: songs)
    resp = client.get("/api/search-stream")
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert events[0]["song"]["title"] == "findme"
    assert events[0]["match"]["uri"] == "spotify:track:1"
    assert events[1]["match"] is None
    assert events[-1] == {"done": True, "total": 2}


def test_library_search_stream_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(main, "get_spotify", lambda: None)
    resp = client.get("/api/search-stream")
    assert resp.status_code == 401
