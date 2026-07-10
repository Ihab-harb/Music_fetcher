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


import anghami


@pytest.fixture(autouse=True)
def clear_anghami_session():
    main._anghami_session = None
    yield
    main._anghami_session = None


def test_anghami_fetch_happy_path(client, monkeypatch):
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", lambda url: {
        "name": "My Mix", "url": url.strip(), "declared_total": 3,
        "tracks": [{"artist": "A", "title": "findme"},
                   {"artist": "B", "title": "nope"},
                   {"artist": "C", "title": "third"}],
    })
    resp = client.post("/api/anghami/fetch", json={"url": "https://play.anghami.com/playlist/1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"name": "My Mix", "url": "https://play.anghami.com/playlist/1",
                    "total": 3, "declared_total": 3, "truncated": False}
    assert main._anghami_session["songs"][0] == {
        "filename": "", "album": "", "artist": "A", "title": "findme"}


def test_anghami_fetch_truncated_flag(client, monkeypatch):
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", lambda url: {
        "name": "Big", "url": url, "declared_total": 500,
        "tracks": [{"artist": "A", "title": "t"}],
    })
    resp = client.post("/api/anghami/fetch", json={"url": "https://play.anghami.com/playlist/1"})
    assert resp.json()["truncated"] is True


@pytest.mark.parametrize("exc,status,detail_start", [
    (anghami.InvalidUrl("x"), 400, "That doesn't look like an Anghami playlist link"),
    (anghami.PlaylistNotFound(), 404, "Anghami says this playlist doesn't exist"),
    (anghami.ParseError("x"), 502, "Couldn't read the playlist page"),
    (anghami.requests.ConnectionError("x"), 502, "Couldn't reach Anghami"),
])
def test_anghami_fetch_errors(client, monkeypatch, exc, status, detail_start):
    def boom(url):
        raise exc
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", boom)
    resp = client.post("/api/anghami/fetch", json={"url": "https://play.anghami.com/playlist/1"})
    assert resp.status_code == status
    assert resp.json()["detail"].startswith(detail_start)
    assert main._anghami_session is None


def test_anghami_stream_without_fetch_409(client, spotify_and_cache):
    resp = client.get("/api/anghami/search-stream")
    assert resp.status_code == 409


def test_anghami_stream_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(main, "get_spotify", lambda: None)
    resp = client.get("/api/anghami/search-stream")
    assert resp.status_code == 401


def test_anghami_stream_happy_path(client, spotify_and_cache):
    main._anghami_session = {
        "name": "My Mix", "url": "u", "declared_total": 2,
        "songs": [{"filename": "", "album": "", "artist": "A", "title": "findme"},
                  {"filename": "", "album": "", "artist": "B", "title": "nope"}],
    }
    resp = client.get("/api/anghami/search-stream")
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert events[0]["match"]["uri"] == "spotify:track:1"
    assert events[1]["match"] is None
    assert events[-1] == {"done": True, "total": 2}
