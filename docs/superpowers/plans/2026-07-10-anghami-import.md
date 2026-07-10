# Anghami Playlist Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import a public Anghami playlist by URL into a new "Import from Anghami" tab, match its tracks on Spotify via the existing streamed search, review, and add to a Spotify playlist.

**Architecture:** A new isolated `anghami.py` module fetches the public playlist page and parses its embedded schema.org `MusicPlaylist` JSON-LD block (verified present; carries `name`, `numTracks`, and per-track `name`/`byArtist.name`). Two new endpoints expose fetch + an SSE search stream that reuses the existing streaming loop, factored into a shared helper. The frontend becomes two tabs whose results tables are instances of one `createResultsView` factory.

**Tech Stack:** FastAPI, spotipy, requests + BeautifulSoup4 (new), vanilla JS single-page frontend, pytest + httpx (new, dev-only).

## Global Constraints

- **Commits (updated 2026-07-10, supersedes any "Checkpoint" step below):** work happens on the `anghami-import` feature branch, and each task's implementer DOES commit its own work there (Ihab approved this branch-scoped exception; he reviews/merges the branch himself). `git add` only your task's files — never `docs/`, `.superpowers/`, or unrelated files. Never touch `main`. A "Checkpoint" step therefore means: commit your task's work on this branch.
- Platform is **Windows**; shell steps are PowerShell 5.1 (no `&&`; separate commands with `;`).
- Run Python tools via `python -m` (e.g. `python -m pytest`).
- The existing local-library flow's behavior must be **unchanged** (endpoints `/api/search-stream`, `/api/search-control`, all SSE event shapes: `{index,total,song,match}`, `stopped`, `rate_limited`, `done`, `skipped`).
- `main.py` has `TrustedHostMiddleware` allowing only `127.0.0.1`/`localhost` — every `TestClient` MUST be constructed as `TestClient(app, base_url="http://127.0.0.1")` or every request 400s.
- Error message copy (exact strings, from the spec):
  - invalid URL → `That doesn't look like an Anghami playlist link (expected play.anghami.com/playlist/…)`
  - not found → `Anghami says this playlist doesn't exist or isn't public.`
  - parse failure → `Couldn't read the playlist page — Anghami may have changed their site.`
  - network → `Couldn't reach Anghami — check your connection and try again.`
- Anghami requests need browser-like headers (`User-Agent`, `Accept`, `Accept-Language`) — verified that Anghami returns **406** without them. Deleted playlists return **HTTP 200** with "Playlist deleted" in the body.

---

### Task 1: Test scaffolding, dependencies, fixtures

**Files:**
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py` (empty)
- Create: `tests/fixtures/playlist_page.html`
- Create: `tests/fixtures/deleted_page.html`

**Interfaces:**
- Produces: fixture files used by Tasks 3–4 tests; installed `beautifulsoup4`, `pytest`, `httpx`.

- [ ] **Step 1: Add beautifulsoup4 to requirements.txt**

Append to `requirements.txt` (requests is already listed):

```
beautifulsoup4>=4.12
```

- [ ] **Step 2: Create requirements-dev.txt**

```
pytest>=8.0
httpx>=0.27
```

- [ ] **Step 3: Install**

Run: `python -m pip install -r requirements.txt -r requirements-dev.txt`
Expected: exits 0.

- [ ] **Step 4: Create tests package and fixtures**

Create empty `tests/__init__.py`. Then copy the fixtures captured during planning (this session's scratchpad):

```powershell
New-Item -ItemType Directory -Force tests\fixtures
Copy-Item "C:\Users\Ihab\AppData\Local\Temp\claude\c--Projects-music-fetcher\c18de86d-ac30-47f8-a24e-c36f07db3532\scratchpad\anghami_playlist.html" tests\fixtures\playlist_page.html
Copy-Item "C:\Users\Ihab\AppData\Local\Temp\claude\c--Projects-music-fetcher\c18de86d-ac30-47f8-a24e-c36f07db3532\scratchpad\anghami_deleted.html" tests\fixtures\deleted_page.html
```

If the scratchpad files are gone, re-download (same content shape):

```powershell
curl.exe -s -o tests\fixtures\playlist_page.html -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36" -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" -H "Accept-Language: en-US,en;q=0.9" --compressed "https://play.anghami.com/playlist/6471050"
curl.exe -s -o tests\fixtures\deleted_page.html -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36" -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" -H "Accept-Language: en-US,en;q=0.9" --compressed "https://play.anghami.com/playlist/109221721"
```

- [ ] **Step 5: Sanity-check fixtures**

Run: `Select-String -Path tests\fixtures\playlist_page.html -Pattern "MusicPlaylist" -Quiet; Select-String -Path tests\fixtures\deleted_page.html -Pattern "Playlist deleted" -Quiet`
Expected: `True` twice.

- [ ] **Step 6: Verify pytest runs**

Run: `python -m pytest --collect-only -q`
Expected: exits 0 ("no tests ran" is fine).

- [ ] **Step 7: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 2: `anghami.py` — errors + URL validation

**Files:**
- Create: `anghami.py`
- Create: `tests/test_anghami.py`

**Interfaces:**
- Produces: `anghami.validate_playlist_url(url: str) -> str` (returns playlist id, raises `InvalidUrl`); exception classes `AnghamiError`, `InvalidUrl`, `PlaylistNotFound`, `ParseError` (all in `anghami.py`, `AnghamiError` is the base).

- [ ] **Step 1: Write failing tests**

`tests/test_anghami.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'anghami'`.

- [ ] **Step 3: Implement**

`anghami.py`:

```python
"""Anghami public-playlist import. All Anghami-specific fetching and parsing lives
here so a site change only ever touches this module."""
import json
import re

import requests
from bs4 import BeautifulSoup

PLAYLIST_URL_RE = re.compile(r"^https?://play\.anghami\.com/playlist/(\d+)(?:[/?#].*)?$")

# Anghami returns 406 Not Acceptable without browser-like headers.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class AnghamiError(Exception):
    pass


class InvalidUrl(AnghamiError):
    pass


class PlaylistNotFound(AnghamiError):
    pass


class ParseError(AnghamiError):
    pass


def validate_playlist_url(url: str) -> str:
    m = PLAYLIST_URL_RE.match(url.strip())
    if not m:
        raise InvalidUrl(url)
    return m.group(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 3: `anghami.py` — JSON-LD parser

**Files:**
- Modify: `anghami.py`
- Modify: `tests/test_anghami.py`

**Interfaces:**
- Consumes: fixtures from Task 1.
- Produces: `anghami.parse_playlist_page(html_text: str) -> dict` returning `{"name": str, "tracks": [{"artist": str, "title": str}], "declared_total": int}`; raises `PlaylistNotFound` on the "Playlist deleted" page, `ParseError` when no usable `MusicPlaylist` block exists.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_anghami.py`:

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: new tests FAIL with `AttributeError: module 'anghami' has no attribute 'parse_playlist_page'`; Task 2 tests still PASS.

- [ ] **Step 3: Implement**

Append to `anghami.py`:

```python
def parse_playlist_page(html_text: str) -> dict:
    """Extract the playlist from the page's schema.org MusicPlaylist JSON-LD block."""
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        if not (isinstance(data, dict) and data.get("@type") == "MusicPlaylist"):
            continue
        tracks = []
        for rec in data.get("track") or []:
            title = (rec.get("name") or "").strip()
            artist = ((rec.get("byArtist") or {}).get("name") or "").strip()
            if title:
                tracks.append({"artist": artist, "title": title})
        if not tracks:
            raise ParseError("MusicPlaylist block has no readable tracks")
        try:
            declared = int(data.get("numTracks") or 0)
        except (TypeError, ValueError):
            declared = 0
        return {
            "name": (data.get("name") or "Anghami playlist").strip(),
            "tracks": tracks,
            "declared_total": max(declared, len(tracks)),
        }
    if "Playlist deleted" in html_text:
        raise PlaylistNotFound()
    raise ParseError("no MusicPlaylist ld+json block found")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 4: `anghami.py` — `fetch_anghami_playlist`

**Files:**
- Modify: `anghami.py`
- Modify: `tests/test_anghami.py`

**Interfaces:**
- Produces: `anghami.fetch_anghami_playlist(url: str) -> dict` returning `{"name", "tracks", "declared_total", "url"}` (parser result + the input url, stripped). Raises `InvalidUrl` before any network I/O; `PlaylistNotFound` on HTTP 404; `requests.RequestException` passes through; parser errors propagate.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_anghami.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: new tests FAIL with `AttributeError: module 'anghami' has no attribute 'fetch_anghami_playlist'`.

- [ ] **Step 3: Implement**

Append to `anghami.py`:

```python
def fetch_anghami_playlist(url: str) -> dict:
    validate_playlist_url(url)
    clean_url = url.strip()
    resp = requests.get(clean_url, headers=REQUEST_HEADERS, timeout=15)
    if resp.status_code == 404:
        raise PlaylistNotFound()
    resp.raise_for_status()
    result = parse_playlist_page(resp.text)
    result["url"] = clean_url
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 5: Factor the SSE search loop into a shared helper

**Files:**
- Modify: `main.py` (the `/api/search-stream` endpoint, currently `main.py:478-558`)
- Create: `tests/test_endpoints.py`

**Interfaces:**
- Produces: `spotify_search_stream_response(sp, songs: List[dict], start_index: int) -> StreamingResponse` in `main.py` — the exact current streaming behavior, parameterized by track list. Each `songs` element needs keys `artist`, `title` (used for search + cache key) plus whatever the frontend renders (`filename`, `album`). Task 6 calls this with Anghami tracks.
- The `/api/search-stream` endpoint's externally visible behavior is unchanged.

- [ ] **Step 1: Write failing test (pin the refactor with the library stream)**

`tests/test_endpoints.py`:

```python
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
```

Note: `search_spotify_track` is looked up as a module global inside the generator (`main.search_spotify_track`), so `monkeypatch.setattr(main, ...)` intercepts it. The 0.5 s throttle only runs on cache misses; with two tracks the test takes ~1 s.

- [ ] **Step 2: Run test to verify current state**

Run: `python -m pytest tests/test_endpoints.py -v`
Expected: PASS already (this pins current behavior BEFORE refactoring — if it fails, fix the test, not main.py).

- [ ] **Step 3: Refactor**

In `main.py`, replace the `search_stream` endpoint (`main.py:478-558`) with a helper + thin endpoint. The `generate()` body is **byte-for-byte the current one** — only `songs` now comes from the parameter:

```python
def spotify_search_stream_response(sp: spotipy.Spotify, songs: List[dict], start_index: int) -> StreamingResponse:
    """Shared SSE search loop: streams one Spotify match per song. Used by both the
    local-library flow and the Anghami import flow."""
    cache = load_cache()
    _search_state["stop"] = False
    _search_state["skip"] = False

    async def generate():
        dirty = False
        for i, song in enumerate(songs):
            if i < start_index:
                continue

            # Always yield to the event loop so concurrent POSTs (stop, skip,
            # create-playlist, etc.) are dispatched promptly. Without this, a
            # run of cached songs has no real await point and the loop hogs
            # the event loop, queueing every other request behind it.
            await asyncio.sleep(0)

            if _search_state["stop"]:
                if dirty:
                    save_cache(cache)
                yield f"data: {json.dumps({'stopped': True, 'index': i, 'total': len(songs)})}\n\n"
                return

            if _search_state["skip"]:
                _search_state["skip"] = False
                payload = json.dumps({"index": i, "total": len(songs), "song": song, "match": None, "skipped": True})
                yield f"data: {payload}\n\n"
                continue

            key = make_cache_key(song["artist"], song["title"])
            if key in cache:
                match = cache[key]
            else:
                try:
                    # sp.search is a blocking HTTP call — keep it off the event
                    # loop so Stop/Skip POSTs are handled while it's in flight.
                    match = await asyncio.to_thread(
                        search_spotify_track, sp, song["artist"], song["title"]
                    )
                    cache[key] = match
                    dirty = True
                except spotipy.SpotifyException as e:
                    if getattr(e, "http_status", None) == 429:
                        retry_after = 60
                        try:
                            retry_after = int((e.headers or {}).get("Retry-After", 60))
                        except Exception:
                            pass
                        if dirty:
                            save_cache(cache)
                        yield f"data: {json.dumps({'rate_limited': True, 'retry_after': retry_after, 'index': i, 'total': len(songs)})}\n\n"
                        return
                    match = None
                except Exception:
                    match = None
                await asyncio.sleep(0.5)

            if dirty and i % 100 == 0:
                save_cache(cache)
                dirty = False

            payload = json.dumps({"index": i, "total": len(songs), "song": song, "match": match})
            yield f"data: {payload}\n\n"

        if dirty:
            save_cache(cache)
        yield f"data: {json.dumps({'done': True, 'total': len(songs)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/search-stream")
async def search_stream(start_index: int = 0):
    sp = get_spotify()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Folder scanning walks the filesystem and parses tags — run it in a worker
    # thread so a large first-time scan doesn't freeze the event loop.
    songs = await asyncio.to_thread(scan_music_folders)
    return spotify_search_stream_response(sp, songs, start_index)
```

One subtlety: inside the helper, `search_spotify_track`, `load_cache`, `save_cache` must remain **module-global lookups** (as written above), not captured references — the tests monkeypatch them on the module. Do not import them into local variables.

- [ ] **Step 4: Run tests to verify behavior is unchanged**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 6: Anghami endpoints

**Files:**
- Modify: `main.py`
- Modify: `tests/test_endpoints.py`

**Interfaces:**
- Consumes: `anghami.fetch_anghami_playlist` (Task 4), `spotify_search_stream_response` (Task 5).
- Produces:
  - `POST /api/anghami/fetch` body `{"url": str}` → `{"name": str, "url": str, "total": int, "declared_total": int, "truncated": bool}`; errors 400/404/502 with the exact detail strings from Global Constraints.
  - `GET /api/anghami/search-stream?start_index=N` → SSE stream, same event shapes as `/api/search-stream`; 401 if not authenticated, 409 `No Anghami playlist fetched yet.` if fetch hasn't run.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_endpoints.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_endpoints.py -v`
Expected: new tests FAIL — fetch/stream endpoints 404, `main` has no attribute `_anghami_session`.

- [ ] **Step 3: Implement**

In `main.py`:

(a) Top of file: add `import requests` to the imports (it's currently imported inside `debug_create_playlist`; the function-local import can stay) and `import anghami` after the third-party imports.

(b) Near `_search_state` (`main.py:29`), add:

```python
_anghami_session: Optional[dict] = None
```

(c) With the other request models, add:

```python
class AnghamiFetchRequest(BaseModel):
    url: str
```

(d) New section before `# ── Playlists ──`:

```python
# ── Anghami import ────────────────────────────────────────────────────────────

@app.post("/api/anghami/fetch")
async def anghami_fetch(req: AnghamiFetchRequest):
    global _anghami_session
    try:
        # Network fetch + parse are blocking — keep them off the event loop.
        data = await asyncio.to_thread(anghami.fetch_anghami_playlist, req.url)
    except anghami.InvalidUrl:
        raise HTTPException(status_code=400, detail="That doesn't look like an Anghami playlist link (expected play.anghami.com/playlist/…)")
    except anghami.PlaylistNotFound:
        raise HTTPException(status_code=404, detail="Anghami says this playlist doesn't exist or isn't public.")
    except anghami.ParseError:
        raise HTTPException(status_code=502, detail="Couldn't read the playlist page — Anghami may have changed their site.")
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Couldn't reach Anghami — check your connection and try again.")
    # Shape tracks like scanned songs so the shared stream + frontend table work unchanged.
    songs = [{"filename": "", "album": "", "artist": t["artist"], "title": t["title"]}
             for t in data["tracks"]]
    _anghami_session = {
        "name": data["name"], "url": data["url"],
        "declared_total": data["declared_total"], "songs": songs,
    }
    return {
        "name": data["name"], "url": data["url"], "total": len(songs),
        "declared_total": data["declared_total"],
        "truncated": data["declared_total"] > len(songs),
    }


@app.get("/api/anghami/search-stream")
async def anghami_search_stream(start_index: int = 0):
    sp = get_spotify()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _anghami_session:
        raise HTTPException(status_code=409, detail="No Anghami playlist fetched yet.")
    return spotify_search_stream_response(sp, _anghami_session["songs"], start_index)
```

Note: Stop/Skip intentionally reuse the existing `/api/search-control` and `_search_state` — this is a single-user local app; only one search runs at a time.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 7: Frontend — tabs + results-view factory (library only, behavior unchanged)

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Produces: global `views = {library, anghami-ready factory}`, `activeTab`, `showTab(tab)`, `createResultsView(cfg)`. Library view instantiated as `views.library = createResultsView({ key: 'library', prefix: '', streamPath: '/api/search-stream', showFile: true, emptyHtml: ... })`. Task 8 instantiates `views.anghami` with `prefix: 'ang-'`.
- View instance API (used by inline handlers and Task 8): `.startSearch(resume?)`, `.stopSearch()`, `.skipTrack()`, `.continueSearch()`, `.filterChanged()`, `.selectAllMatched()`, `.clearSelection()`, `.toggleSelectAll(cb)`, `.toggleRow(i)`, `.prevPage()`, `.nextPage()`, `.renderTable()`, `.updateStats()`, `.reset()`, `.state`.

This task only restructures — after it, the app must look and behave exactly as before (Anghami tab exists but shows a placeholder).

- [ ] **Step 1: Add tab styles**

In the `<style>` block (before the `@media` rule at `static/index.html:504`), add:

```css
.tabs { display:flex; gap:4px; margin-bottom:18px; border-bottom:1px solid var(--line); }
.tab {
  background:none; border:none; cursor:pointer; padding:10px 16px;
  font:inherit; font-size:13px; font-weight:600; color:var(--text-dim);
  border-bottom:2px solid transparent; margin-bottom:-1px;
}
.tab:hover { color:var(--text); }
.tab.tab-active { color:var(--text); border-bottom-color:var(--green-light); }
```

(`--line`, `--text-dim`, `--text`, `--green-light` all exist in the `:root` block at `static/index.html:10-24`.)

- [ ] **Step 2: Restructure the main view into tabs**

Inside `<section id="main-view" class="hide">` (`static/index.html:541`):

1. Immediately after the opening tag, insert the tab bar:

```html
    <div class="tabs reveal">
      <button id="tab-btn-library" class="tab tab-active" onclick="showTab('library')">My Library</button>
      <button id="tab-btn-anghami" class="tab" onclick="showTab('anghami')">Import from Anghami</button>
    </div>
```

2. Wrap everything from the `<!-- Sources -->` comment down to (and including) the pagination `<div class="strip">…</div>` in `<div id="tab-library"> … </div>`.
3. After the closing `</div>` of `tab-library`, add the placeholder (Task 8 fills it):

```html
    <div id="tab-anghami" class="hide"></div>
```

4. `playlist-panel` stays where it is (after both tab divs, inside `main-view`) — it belongs to the global bottom-bar flow, shared by both tabs.

- [ ] **Step 3: Update inline handlers in the library markup to the view API**

In the library markup, replace these handler attributes (leave folder/upload handlers untouched — they stay global):

| Element | Old | New |
|---|---|---|
| `#search-btn` | `onclick="startSearch()"` | `onclick="views.library.startSearch()"` |
| Select Matched btn | `onclick="selectAllMatched()"` | `onclick="views.library.selectAllMatched()"` |
| Clear btn | `onclick="clearSelection()"` | `onclick="views.library.clearSelection()"` |
| `#filter-input` | `oninput="filterChanged()"` | `oninput="views.library.filterChanged()"` |
| `#status-filter` | `onchange="filterChanged()"` | `onchange="views.library.filterChanged()"` |
| `#stop-btn` | `onclick="stopSearch()"` | `onclick="views.library.stopSearch()"` |
| `#skip-btn` | `onclick="skipTrack()"` | `onclick="views.library.skipTrack()"` |
| `#continue-btn` | `onclick="continueSearch()"` | `onclick="views.library.continueSearch()"` |
| `#header-cb` | `onchange="toggleSelectAll(this)"` | `onchange="views.library.toggleSelectAll(this)"` |
| `#prev-btn` | `onclick="prevPage()"` | `onclick="views.library.prevPage()"` |
| `#next-btn` | `onclick="nextPage()"` | `onclick="views.library.nextPage()"` |

- [ ] **Step 4: Rewrite the script's search/table code as a factory**

In the `<script>` block: **delete** the global `state` object (`index.html:691-701`), the progress rAF pair (`scheduleProgressUpdate`), and the functions `startSearch`, `stopSearch`, `skipTrack`, `continueSearch`, `scheduleRender`, `getFiltered`, `filterChanged`, `selectAllMatched`, `clearSelection`, `toggleSelectAll`, `toggleRow`, `renderTable`, `updateStats`, `prevPage`, `nextPage`. **Keep** `PAGE_SIZE`, `toast`, `esc`, `init`, `showNotAuth`, `showMain`, `login`, folder functions, upload functions, playlist/modal functions (edited below).

Insert in their place:

```js
  // ── Results view factory (one instance per tab) ────────────────────────────
  let activeTab = 'library';
  const views = {};
  const activeView = () => views[activeTab];

  function createResultsView(cfg) {
    // cfg: { key, prefix, streamPath, showFile, emptyHtml }
    const $ = name => document.getElementById(cfg.prefix + name);
    const COLS = cfg.showFile ? 7 : 6;
    const state = {
      songs: [],
      stats: { matched: 0, notFound: 0, skipped: 0, selected: 0 },
      searching: false,
      stopped: false,
      resumeIndex: 0,
      page: 0,
      filter: { text: '', status: 'all' },
      renderTimer: null,
      _es: null,
    };

    // Progress (rAF-coalesced), per view
    let _rafId = null, _rafData = null;
    function scheduleProgressUpdate(d) {
      _rafData = d;
      if (_rafId) return;
      _rafId = requestAnimationFrame(() => {
        _rafId = null;
        const ev = _rafData;
        const pct = Math.round((ev.index + 1) / ev.total * 100);
        $('progress-fill').style.width = pct + '%';
        $('progress-count').textContent = `${ev.index + 1} / ${ev.total}`;
        $('scanning-track').textContent =
          `${ev.song.artist || 'Unknown Artist'} — ${ev.song.title || ev.song.filename}`;
      });
    }

    function startSearch(resume = false) {
      if (state.searching) return;
      if (!resume) {
        state.songs = [];
        state.page = 0;
        state.stats = { matched: 0, notFound: 0, skipped: 0, selected: 0 };
      }
      state.searching = true;
      state.stopped = false;

      $('search-btn').disabled = true;
      $('progress-bar-wrap').classList.remove('hide');
      $('stop-btn').classList.remove('hide');
      $('skip-btn').classList.remove('hide');
      $('continue-btn').classList.add('hide');
      const scanEl = $('scanning-track');
      scanEl.classList.add('pulse-dot');
      scanEl.textContent = '—';

      const startIdx = resume ? state.resumeIndex : 0;
      const es = new EventSource(`${cfg.streamPath}?start_index=${startIdx}`);
      state._es = es;

      es.onmessage = e => {
        const d = JSON.parse(e.data);

        if (d.stopped || d.rate_limited) {
          es.close();
          state.searching = false;
          state.stopped = true;
          state.resumeIndex = d.index;
          $('search-btn').disabled = false;
          $('stop-btn').classList.add('hide');
          $('skip-btn').classList.add('hide');
          $('continue-btn').classList.remove('hide');
          scanEl.classList.remove('pulse-dot');
          if (d.rate_limited) {
            const mins = Math.max(1, Math.ceil(d.retry_after / 60));
            scanEl.textContent = `Rate limit reached at ${d.index} / ${d.total}. Try again in ~${mins} min.`;
            toast(`Spotify rate-limited the app. Try again in ~${mins} min.`, 'warn', 6000);
          } else {
            scanEl.textContent = `Paused at ${d.index} / ${d.total}`;
          }
          scheduleRender();
          return;
        }

        if (d.done) {
          es.close();
          state.searching = false;
          state.stopped = false;
          state.resumeIndex = 0;
          $('search-btn').disabled = false;
          $('progress-bar-wrap').classList.add('hide');
          scanEl.classList.remove('pulse-dot');
          renderTable();
          return;
        }

        state.songs.push({ idx: state.songs.length, song: d.song, match: d.match, selected: false, skipped: !!d.skipped });
        if (d.match) state.stats.matched++;
        else if (d.skipped) state.stats.skipped++;
        else if (d.match === null) state.stats.notFound++;
        state.resumeIndex = d.index + 1;

        scheduleProgressUpdate(d);
        if ((d.index + 1) % 500 === 0) scheduleRender();
      };

      es.onerror = () => {
        es.close();
        state.searching = false;
        $('search-btn').disabled = false;
        scanEl.classList.remove('pulse-dot');
        if (!state.stopped) $('progress-bar-wrap').classList.add('hide');
        scheduleRender();
      };
    }

    function stopSearch()     { fetch('/api/search-control/stop', { method: 'POST' }); }
    function skipTrack()      { fetch('/api/search-control/skip', { method: 'POST' }); }
    function continueSearch() { startSearch(true); }

    function scheduleRender() {
      if (state.renderTimer) return;
      state.renderTimer = setTimeout(() => { state.renderTimer = null; renderTable(); }, 300);
    }

    function getFiltered() {
      const { text, status } = state.filter;
      return state.songs.filter(item => {
        const { song, match, selected, skipped } = item;
        if (status === 'matched'  && !match)                      return false;
        if (status === 'notfound' && (match !== null || skipped)) return false;
        if (status === 'skipped'  && !skipped)                    return false;
        if (status === 'selected' && !selected)                   return false;
        if (text) {
          const q = text.toLowerCase();
          return song.artist.toLowerCase().includes(q) ||
                 song.title.toLowerCase().includes(q)  ||
                 (song.filename || '').toLowerCase().includes(q);
        }
        return true;
      });
    }

    let _filterTimer = null;
    function filterChanged() {
      clearTimeout(_filterTimer);
      _filterTimer = setTimeout(() => {
        state.filter.text   = $('filter-input').value;
        state.filter.status = $('status-filter').value;
        state.page = 0;
        renderTable();
      }, 200);
    }

    function selectAllMatched() {
      let added = 0;
      state.songs.forEach(s => { if (s.match && !s.selected) { s.selected = true; added++; } });
      state.stats.selected += added;
      renderTable();
    }

    function clearSelection() {
      state.songs.forEach(s => s.selected = false);
      state.stats.selected = 0;
      renderTable();
    }

    function toggleSelectAll(cb) {
      const filtered = getFiltered();
      let delta = 0;
      for (const item of filtered) {
        if (!item.match) continue;
        if (cb.checked && !item.selected)      { item.selected = true;  delta++; }
        else if (!cb.checked && item.selected) { item.selected = false; delta--; }
      }
      state.stats.selected += delta;
      renderTable();
    }

    function toggleRow(globalIdx) {
      const item = state.songs[globalIdx];
      if (!item.match) return;
      item.selected = !item.selected;
      if (item.selected) state.stats.selected++;
      else state.stats.selected--;
      const rowEl = document.querySelector(`#${cfg.prefix || 'lib-'}table-wrap tr[data-idx="${globalIdx}"]`)
                 || document.querySelector(`tr[data-idx="${globalIdx}"]`);
      if (rowEl) {
        rowEl.querySelector('input[type=checkbox]').checked = item.selected;
        rowEl.classList.toggle('is-selected', item.selected);
      }
      updateStats();
    }

    function renderTable() {
      const filtered = getFiltered();
      const start = state.page * PAGE_SIZE;
      const page  = filtered.slice(start, start + PAGE_SIZE);
      const tbody = $('tbody');

      if (page.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${COLS}" style="padding:48px 16px; text-align:center; color:var(--text-faint); font-size:12px;">${
          state.songs.length === 0 ? cfg.emptyHtml : 'No songs match the current filter.'
        }</td></tr>`;
      } else {
        tbody.innerHTML = page.map((item, i) => {
          const globalIdx = item.idx;
          const { song, match, selected, skipped } = item;
          const statusHtml = match
            ? '<span class="badge badge--matched">Matched</span>'
            : skipped
              ? '<span class="badge badge--skipped">Skipped</span>'
              : match === null
                ? '<span class="badge badge--notfound">Not Found</span>'
                : '<span class="badge badge--idle">—</span>';
          const matchHtml = match
            ? `<div class="truncate-cell" style="color:#86efac">${esc(match.artist)} — ${esc(match.name)}</div>
               <div class="truncate-cell" style="color:var(--text-faint); font-size:11px;">${esc(match.album)}</div>`
            : '<span style="color:var(--text-faint)">—</span>';
          const fileCell = cfg.showFile
            ? `<td class="truncate-cell" style="color:var(--text-dim);" title="${esc(song.filename)}">${esc(song.filename)}</td>`
            : '';
          return `<tr data-idx="${globalIdx}" class="${selected ? 'is-selected' : ''}">
            <td style="text-align:center;">
              <input type="checkbox" ${selected ? 'checked' : ''} ${!match ? 'disabled' : ''} onchange="views.${cfg.key}.toggleRow(${globalIdx})" />
            </td>
            <td class="row-num">${start + i + 1}</td>
            ${fileCell}
            <td class="truncate-cell" title="${esc(song.artist)}">${esc(song.artist) || '<span style="color:var(--text-faint)">Unknown</span>'}</td>
            <td class="truncate-cell" title="${esc(song.title)}">${esc(song.title) || '<span style="color:var(--text-faint)">Unknown</span>'}</td>
            <td>${matchHtml}</td>
            <td>${statusHtml}</td>
          </tr>`;
        }).join('');
      }

      const total = filtered.length;
      const totalPages = Math.ceil(total / PAGE_SIZE);
      $('page-info').textContent = total
        ? `${start + 1}–${Math.min(start + PAGE_SIZE, total)} of ${total}` : '';
      $('prev-btn').disabled = state.page === 0;
      $('next-btn').disabled = state.page >= totalPages - 1;
      updateStats();
    }

    function updateStats() {
      const { matched, notFound, skipped, selected } = state.stats;
      $('stat-total').textContent    = state.songs.length;
      $('stat-matched').textContent  = matched;
      $('stat-notfound').textContent = notFound;
      $('stat-skipped').textContent  = skipped;
      $('stat-selected').textContent = selected;
      if (activeTab === cfg.key) {
        document.getElementById('bottom-count').textContent = selected;
      }
      // Keep the header checkbox in sync with the current filter's selectable rows
      const selectable = getFiltered().filter(item => item.match);
      $('header-cb').checked =
        selectable.length > 0 && selectable.every(item => item.selected);
    }

    function prevPage() { if (state.page > 0) { state.page--; renderTable(); } }
    function nextPage() {
      if (state.page < Math.ceil(getFiltered().length / PAGE_SIZE) - 1) { state.page++; renderTable(); }
    }

    function reset() {
      if (state._es) { try { state._es.close(); } catch {} state._es = null; }
      state.songs = [];
      state.stats = { matched: 0, notFound: 0, skipped: 0, selected: 0 };
      state.page = 0;
      state.searching = false;
      state.stopped = false;
      state.resumeIndex = 0;
      $('progress-bar-wrap').classList.add('hide');
      $('search-btn').disabled = (cfg.key === 'anghami' && !anghamiPlaylist);
      renderTable();
    }

    return { state, startSearch, stopSearch, skipTrack, continueSearch, filterChanged,
             selectAllMatched, clearSelection, toggleSelectAll, toggleRow,
             prevPage, nextPage, renderTable, updateStats, reset };
  }

  views.library = createResultsView({
    key: 'library',
    prefix: '',
    streamPath: '/api/search-stream',
    showFile: true,
    emptyHtml: 'Add folders or upload files above, then click <b style="color:var(--green-light)">Find on Spotify</b>.',
  });

  // ── Tabs ───────────────────────────────────────────────────────────────────
  function showTab(tab) {
    activeTab = tab;
    document.getElementById('tab-library').classList.toggle('hide', tab !== 'library');
    document.getElementById('tab-anghami').classList.toggle('hide', tab !== 'anghami');
    document.getElementById('tab-btn-library').classList.toggle('tab-active', tab === 'library');
    document.getElementById('tab-btn-anghami').classList.toggle('tab-active', tab === 'anghami');
    if (views[tab]) views[tab].updateStats();  // refresh bottom-bar count for this tab
  }
```

Also declare (near the top of the script, before the factory — the factory's `reset` references it):

```js
  let anghamiPlaylist = null;  // { name, url, total, declared_total, truncated } once fetched
```

Note on `toggleRow`'s row lookup: after Task 8 there are two tables in the DOM and `data-idx` values collide between them. The factory scopes the query to its own table wrapper — Task 7 Step 5 adds `id="lib-table-wrap"` / Task 8 uses `id="ang-table-wrap"`. (For prefix `''` the wrapper id is `lib-table-wrap`, hence the `cfg.prefix || 'lib-'` fallback.)

- [ ] **Step 5: Add the table wrapper id**

Change the library table container `<div style="overflow-x:auto;">` (`static/index.html:620`) to `<div id="lib-table-wrap" style="overflow-x:auto;">`.

- [ ] **Step 6: Update the remaining global functions**

1. In `logout()`: replace the manual state resets (`state.songs = []` … `state.resumeIndex = 0` and the `state._es` close) with:

```js
    anghamiPlaylist = null;
    views.library.reset();
    if (views.anghami) views.anghami.reset();
```

2. In `addToPlaylist()`: change `state.songs.filter(...)` to `activeView().state.songs.filter(...)`.

- [ ] **Step 7: Verify no stale references**

Run: `Select-String -Path static\index.html -Pattern "state\.songs|state\.stats|state\.filter|state\.page|state\._es|state\.searching|state\.resumeIndex" | Where-Object { $_.Line -notmatch "views\.|activeView|const state|state\.renderTimer" }`
Expected: no output referring to a global `state` outside the factory. Manually double-check any hits.

- [ ] **Step 8: Manual verification — library flow unchanged**

Start the app (`python -m uvicorn main:app --reload --reload-include "*.py"`), open http://127.0.0.1:8000 and verify:
- Two tabs render; "My Library" active, Anghami tab empty.
- Connect state, folders, upload UI unchanged.
- Find on Spotify streams results, Stop/Continue/Skip work, filters/selection/pagination work, stats update, header checkbox syncs.
- Add to Playlist works; bottom count matches selection.
- Switching to the Anghami tab and back loses nothing.

- [ ] **Step 9: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 8: Frontend — Anghami tab UI

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `createResultsView`, `showTab`, `anghamiPlaylist`, endpoints from Task 6.
- Produces: `views.anghami`, `fetchAnghamiPlaylist()`.

- [ ] **Step 1: Fill the Anghami tab markup**

Replace `<div id="tab-anghami" class="hide"></div>` with (mirrors the library sections, `ang-` prefixed ids, no File column):

```html
    <div id="tab-anghami" class="hide">

      <!-- Anghami source -->
      <div class="section">
        <div class="section-label">Anghami Playlist</div>
        <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
          <input id="ang-url-input" type="text" placeholder="https://play.anghami.com/playlist/…"
            class="input" style="flex:1; min-width:16rem;"
            onkeydown="if(event.key==='Enter') fetchAnghamiPlaylist()" />
          <button id="ang-fetch-btn" onclick="fetchAnghamiPlaylist()" class="btn btn-success">Fetch Playlist</button>
        </div>
        <div id="ang-fetch-error" class="hide" style="margin-top:10px; font-size:12px; color:#f87171;"></div>
        <div id="ang-meta" class="hide" style="margin-top:10px; font-size:12px; color:var(--text-dim);">
          <b id="ang-meta-name" style="color:var(--text);"></b>
          <span id="ang-meta-count" style="margin-left:8px; font-variant-numeric:tabular-nums;"></span>
        </div>
        <div id="ang-truncated-warning" class="hide" style="margin-top:6px; font-size:12px; color:#fbbf24;"></div>
      </div>

      <!-- Toolbar -->
      <div class="section" style="padding-top:12px; padding-bottom:12px;">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <button id="ang-search-btn" onclick="views.anghami.startSearch()" class="btn btn-primary" disabled>Find on Spotify</button>
          <button onclick="views.anghami.selectAllMatched()" class="btn btn-ghost">Select Matched</button>
          <button onclick="views.anghami.clearSelection()" class="btn btn-ghost">Clear</button>
          <input id="ang-filter-input" oninput="views.anghami.filterChanged()" type="text" placeholder="Filter artist / title…" class="input" style="width:14rem;" />
          <select id="ang-status-filter" onchange="views.anghami.filterChanged()" class="input">
            <option value="all">All songs</option>
            <option value="matched">Matched</option>
            <option value="notfound">Not Found</option>
            <option value="skipped">Skipped</option>
            <option value="selected">Selected</option>
          </select>
        </div>
      </div>

      <!-- Progress -->
      <div id="ang-progress-bar-wrap" class="section hide" style="padding-top:14px; padding-bottom:14px;">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:10px;">
          <button onclick="views.anghami.stopSearch()" id="ang-stop-btn" class="btn btn-danger">Stop</button>
          <button onclick="views.anghami.skipTrack()" id="ang-skip-btn" class="btn btn-warn">Skip Track</button>
          <button onclick="views.anghami.continueSearch()" id="ang-continue-btn" class="btn btn-success hide">Continue</button>
          <span style="flex:1"></span>
          <span id="ang-progress-count" style="font-size:11px; color:var(--text-dim); font-variant-numeric:tabular-nums;">0 / 0</span>
        </div>
        <div class="progress-track">
          <div id="ang-progress-fill" class="progress-fill" style="width:0%"></div>
        </div>
        <div style="margin-top:10px; font-size:11px; display:flex; gap:8px; align-items:center; min-height:18px;">
          <span style="color:var(--text-faint); font-weight:600; letter-spacing:.04em;">Now scanning</span>
          <span id="ang-scanning-track" style="color:var(--text);">—</span>
        </div>
      </div>

      <!-- Stats -->
      <div class="stats">
        <div class="stat"><span class="stat-label">Total</span><span class="stat-value" id="ang-stat-total">0</span></div>
        <div class="stat stat--matched"><span class="stat-label">Matched</span><span class="stat-value" id="ang-stat-matched">0</span></div>
        <div class="stat stat--notfound"><span class="stat-label">Not Found</span><span class="stat-value" id="ang-stat-notfound">0</span></div>
        <div class="stat stat--skipped"><span class="stat-label">Skipped</span><span class="stat-value" id="ang-stat-skipped">0</span></div>
        <div class="stat stat--selected"><span class="stat-label">Selected</span><span class="stat-value" id="ang-stat-selected">0</span></div>
      </div>

      <!-- Table -->
      <div id="ang-table-wrap" style="overflow-x:auto;">
        <table class="data-table">
          <thead>
            <tr>
              <th style="width:38px; text-align:center;"><input type="checkbox" id="ang-header-cb" onchange="views.anghami.toggleSelectAll(this)" /></th>
              <th style="width:48px;">#</th>
              <th>Artist</th>
              <th>Title</th>
              <th>Spotify Match</th>
              <th style="width:100px;">Status</th>
            </tr>
          </thead>
          <tbody id="ang-tbody">
            <tr><td colspan="6" style="padding:48px 16px; text-align:center; color:var(--text-faint); font-size:12px;">
              Paste an Anghami playlist link above and click <b style="color:var(--green-light)">Fetch Playlist</b>.
            </td></tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div class="strip">
        <span id="ang-page-info" style="font-variant-numeric:tabular-nums;"></span>
        <div style="display:flex; gap:6px;">
          <button onclick="views.anghami.prevPage()" id="ang-prev-btn" disabled class="btn btn-ghost">← Prev</button>
          <button onclick="views.anghami.nextPage()" id="ang-next-btn" disabled class="btn btn-ghost">Next →</button>
        </div>
      </div>

    </div>
```

- [ ] **Step 2: Instantiate the Anghami view and add the fetch logic**

In the script, right after `views.library = createResultsView({...})`, add:

```js
  views.anghami = createResultsView({
    key: 'anghami',
    prefix: 'ang-',
    streamPath: '/api/anghami/search-stream',
    showFile: false,
    emptyHtml: 'Playlist fetched. Click <b style="color:var(--green-light)">Find on Spotify</b> to match its tracks.',
  });

  // ── Anghami fetch ──────────────────────────────────────────────────────────
  async function fetchAnghamiPlaylist() {
    const input = document.getElementById('ang-url-input');
    const url = input.value.trim();
    if (!url) return;

    const v = views.anghami;
    const hasUnadded = v.state.songs.some(s => s.selected && s.match);
    if (hasUnadded && !confirm('Fetching a new playlist will replace the current results, including selected tracks you haven\'t added yet. Continue?')) {
      return;
    }

    const errEl = document.getElementById('ang-fetch-error');
    errEl.classList.add('hide');
    const btn = document.getElementById('ang-fetch-btn');
    btn.disabled = true; btn.textContent = 'Fetching…';
    try {
      const res = await fetch('/api/anghami/fetch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || res.statusText);
      }
      const data = await res.json();
      anghamiPlaylist = data;
      v.reset();  // clears any previous results; re-enables ang-search-btn (anghamiPlaylist is set)

      document.getElementById('ang-meta').classList.remove('hide');
      document.getElementById('ang-meta-name').textContent = data.name;
      document.getElementById('ang-meta-count').textContent = `${data.total} track${data.total === 1 ? '' : 's'}`;
      const warnEl = document.getElementById('ang-truncated-warning');
      if (data.truncated) {
        warnEl.textContent = `Anghami's page only exposes ${data.total} of ${data.declared_total} tracks — the rest can't be imported.`;
        warnEl.classList.remove('hide');
      } else {
        warnEl.classList.add('hide');
      }
      toast(`Fetched "${data.name}" — ${data.total} tracks`);
    } catch (e) {
      errEl.textContent = e.message;
      errEl.classList.remove('hide');
    }
    btn.disabled = false; btn.textContent = 'Fetch Playlist';
  }
```

- [ ] **Step 3: Reset Anghami UI on logout**

In `logout()`, after `anghamiPlaylist = null;` / resets from Task 7, also add:

```js
    document.getElementById('ang-url-input').value = '';
    document.getElementById('ang-meta').classList.add('hide');
    document.getElementById('ang-fetch-error').classList.add('hide');
    document.getElementById('ang-truncated-warning').classList.add('hide');
```

- [ ] **Step 4: Pre-fill the new-playlist modal from the Anghami playlist name**

Replace `openModal()` with:

```js
  function openModal()  {
    const nameInput = document.getElementById('new-pl-name');
    if (activeTab === 'anghami' && anghamiPlaylist && !nameInput.value.trim()) {
      nameInput.value = anghamiPlaylist.name;
    }
    document.getElementById('modal').classList.remove('hide');
    nameInput.focus();
  }
```

- [ ] **Step 5: Manual verification — full Anghami flow**

With the server running and Spotify connected:
1. Anghami tab → paste a garbage URL → inline error, exact copy from Global Constraints.
2. Paste `https://play.anghami.com/playlist/109221721` (deleted) → "doesn't exist or isn't public" error.
3. Paste `https://play.anghami.com/playlist/6471050` → name + track count appear, Find enabled.
4. Find on Spotify → rows stream in (no File column), Stop → Continue resumes, Skip works.
5. Filters, select-all, pagination work; stats correct; bottom count follows the Anghami tab.
6. + New → name pre-filled with the Anghami playlist name; create; Add to Playlist adds the selected tracks; duplicates reported on second add.
7. Fetch a different playlist with selections pending → confirm dialog appears.
8. Switch tabs both ways → both tables keep their contents; bottom count switches.

- [ ] **Step 6: Checkpoint** — report to Ihab; he reviews/commits.

---

### Task 9: Documentation + final verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Update README.md**

1. Intro line: mention the second flow, e.g. "…adds the matches to a playlist. It can also import a public Anghami playlist by URL."
2. In **Using the App**, add after Step 5:

```markdown
### Importing an Anghami playlist

The **Import from Anghami** tab converts a public Anghami playlist into a Spotify playlist:

1. Open the playlist in Anghami, use **Share → Copy Link** to get a URL like `https://play.anghami.com/playlist/123456`, and paste it into the tab.
2. Click **Fetch Playlist** — the playlist name and track count appear. Private or deleted playlists can't be imported; only playlists visible on Anghami's public web player work.
3. Click **Find on Spotify** and review matches exactly like the local-library flow (Stop/Continue, Skip, filters, selection).
4. Pick or create a target Spotify playlist — **+ New** pre-fills the Anghami playlist's name — and click **Add to Playlist**.

Anghami's page occasionally exposes only part of a very long playlist; if so, the app shows how many of the declared tracks it could read. Searches share the same cache as the library flow (`data/search_cache.json`).
```

3. Project structure: add `anghami.py`, `requirements-dev.txt`, and `tests/` entries.
4. Troubleshooting: add:

```markdown
**Anghami import says the site may have changed**
The importer reads the playlist data embedded in Anghami's public web page. If Anghami
changes that page, fetching fails with this message — check for an updated version of
this app, or file an issue. All Anghami-specific code is in `anghami.py`.
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 3: End-to-end sanity run**

Run the app and do one complete import (fetch real playlist → search → add 2–3 tracks to a test playlist), plus one library-flow search to confirm nothing regressed. Ideally include an Arabic-titled playlist to eyeball match quality (known risk: transliteration differences between Anghami and Spotify metadata).

- [ ] **Step 4: Checkpoint** — report to Ihab; he reviews/commits.
