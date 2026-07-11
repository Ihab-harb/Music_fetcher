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


@pytest.fixture(autouse=True)
def clear_stream_guard():
    main._stream_active = False
    yield
    main._stream_active = False


def test_anghami_fetch_happy_path(client, monkeypatch):
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", lambda url: {
        "name": "My Mix", "url": url.strip(),
        "resolved_url": "https://play.anghami.com/playlist/1",
        "declared_total": 3,
        "tracks": [{"artist": "A", "title": "findme"},
                   {"artist": "B", "title": "nope"},
                   {"artist": "C", "title": "third"}],
    })
    resp = client.post("/api/anghami/fetch", json={"url": "https://play.anghami.com/playlist/1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"name": "My Mix", "url": "https://play.anghami.com/playlist/1",
                    "resolved_url": "https://play.anghami.com/playlist/1",
                    "total": 3, "declared_total": 3, "truncated": False,
                    "tracks": [{"artist": "A", "title": "findme"},
                               {"artist": "B", "title": "nope"},
                               {"artist": "C", "title": "third"}]}
    assert main._anghami_session["songs"][0] == {
        "filename": "", "album": "", "artist": "A", "title": "findme"}


def test_anghami_fetch_truncated_flag(client, monkeypatch):
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", lambda url: {
        "name": "Big", "url": url, "resolved_url": "u", "declared_total": 500,
        "tracks": [{"artist": "A", "title": "t"}],
    })
    resp = client.post("/api/anghami/fetch", json={"url": "https://play.anghami.com/playlist/1"})
    assert resp.json()["truncated"] is True


@pytest.mark.parametrize("exc,status,detail_start", [
    (anghami.InvalidUrl("x"), 400, "That doesn't look like an Anghami playlist link"),
    (anghami.PlaylistNotFound(), 404, "Anghami says this playlist doesn't exist"),
    (anghami.ParseError("x"), 502, "Couldn't read the playlist page"),
    (anghami.RequestError("x"), 502, "Couldn't reach Anghami"),
    (anghami.ShareLinkIsNotPlaylist("x"), 400, "That share link points to a single song"),
    (anghami.ShareLinkUnresolvable("x"), 404, "Couldn't resolve this share link"),
])
def test_anghami_fetch_errors(client, monkeypatch, exc, status, detail_start):
    def boom(url):
        raise exc
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", boom)
    resp = client.post("/api/anghami/fetch", json={"url": "https://play.anghami.com/playlist/1"})
    assert resp.status_code == status
    assert resp.json()["detail"].startswith(detail_start)
    assert main._anghami_session is None


def test_invalid_url_detail_mentions_share_links(client, monkeypatch):
    def boom(url):
        raise anghami.InvalidUrl(url)
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", boom)
    resp = client.post("/api/anghami/fetch", json={"url": "x"})
    assert resp.json()["detail"] == ("That doesn't look like an Anghami playlist link "
                                     "(expected play.anghami.com/playlist/… or an "
                                     "open.anghami.com share link)")


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


def test_create_playlist_uses_me_endpoint(client, monkeypatch):
    """Playlist creation must go through current_user_playlist_create
    (POST /me/playlists) — Spotify removed POST /users/{id}/playlists for
    Development Mode apps in Feb 2026 (bare 403)."""
    calls = {}

    class _Spotify:
        def current_user_playlist_create(self, name, public):
            calls["name"] = name
            calls["public"] = public
            return {"id": "pl1", "name": name}

        def current_user(self):  # would be needed by the removed endpoint path
            raise AssertionError("create-playlist must not resolve a user id")

    monkeypatch.setattr(main, "get_spotify", lambda: _Spotify())
    resp = client.post("/api/create-playlist", json={"name": "My List"})
    assert resp.status_code == 200
    assert resp.json() == {"id": "pl1", "name": "My List"}
    assert calls == {"name": "My List", "public": False}


# ── Single-search guard ───────────────────────────────────────────────────────

def test_stream_rejected_while_another_is_active(client, spotify_and_cache, monkeypatch):
    monkeypatch.setattr(main, "scan_music_folders", lambda: [])
    main._stream_active = True
    resp = client.get("/api/search-stream")
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]


def test_anghami_stream_rejected_while_another_is_active(client, spotify_and_cache):
    main._anghami_session = {
        "name": "M", "url": "u", "declared_total": 1,
        "songs": [{"filename": "", "album": "", "artist": "A", "title": "findme"}],
    }
    main._stream_active = True
    resp = client.get("/api/anghami/search-stream")
    assert resp.status_code == 409


def test_stream_guard_released_after_completion(client, spotify_and_cache, monkeypatch):
    songs = [{"path": "x", "filename": "a.mp3", "folder": "x", "artist": "A", "title": "findme", "album": ""}]
    monkeypatch.setattr(main, "scan_music_folders", lambda: songs)
    resp = client.get("/api/search-stream")
    assert resp.status_code == 200
    assert _sse_events(resp.text)[-1] == {"done": True, "total": 1}
    assert main._stream_active is False


def test_stream_saves_cache_on_client_disconnect(spotify_and_cache, monkeypatch):
    """Closing the browser tab cancels the SSE generator mid-run; the finally
    block must persist dirty cache entries so those Spotify searches are never
    repeated. Drives the generator directly — aclose() simulates the disconnect."""
    import asyncio

    saved = {}
    monkeypatch.setattr(main, "save_cache", lambda cache: saved.update(cache))
    songs = [
        {"filename": "a.mp3", "artist": "A", "title": "findme", "album": ""},
        {"filename": "b.mp3", "artist": "B", "title": "nope", "album": ""},
    ]

    async def run():
        resp = main.spotify_search_stream_response(_FakeSpotify(), songs, 0)
        gen = resp.body_iterator
        # i=0: cadence save fires (i % 100 == 0); i=1: entry stays dirty.
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()  # client disconnect

    asyncio.run(run())
    # The i=1 entry was never saved by the cadence — only the finally block saves it.
    assert main.make_cache_key("B", "nope") in saved
    assert main._stream_active is False
