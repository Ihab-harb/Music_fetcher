# Anghami Preview + Share-Link Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a fetched Anghami playlist's tracks in the results table before matching, and auto-resolve `open.anghami.com` share links to canonical playlist URLs.

**Architecture:** `anghami.py` gains a share-link recognizer + resolver (crawler-UA fetch, extract `anghami://playlist/<id>` from the Branch deep-link page) wired into `fetch_anghami_playlist`. The fetch endpoint returns the track list and the resolved URL; the frontend pre-fills the Anghami results table and the SSE handler updates rows in place.

**Tech Stack:** existing stack only — curl_cffi, BeautifulSoup, FastAPI, vanilla JS, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-11-anghami-preview-and-shortlinks-design.md`. Exact user-facing strings from it:
  - `ShareLinkIsNotPlaylist` → 400 `That share link points to a single song, not a playlist.`
  - `ShareLinkUnresolvable` → 404 `Couldn't resolve this share link — it may be expired or app-only. Open play.anghami.com in a browser, find the playlist, and paste the address-bar link instead.`
  - Updated `InvalidUrl` detail: `That doesn't look like an Anghami playlist link (expected play.anghami.com/playlist/… or an open.anghami.com share link)`
- The library tab's behavior must be unchanged (all frontend factory changes must be inert when `state.prefilled` is false and rows are never pre-filled).
- `curl_cffi` stays confined to `anghami.py`; callers/tests use `anghami.RequestError`.
- Windows/PowerShell; run Python tools via `python -m …`.
- **Commits:** per the execution agreement for this run (see controller/Ihab); the working tree already contains unrelated reviewed-but-uncommitted changes (caching guard, create-playlist endpoint fix) — never mix them into a feature commit without explicit instruction.
- Verified live facts the code comments should reflect: share links are Branch.io deep links; normal fetches never reach the playlist (desktop → landing, iOS → App Store, Android → intent); a crawler UA (Twitterbot) makes the page expose `anghami://playlist/<id>`; dead links expose no such marker.

---

### Task 1: `anghami.py` — share-link recognition + resolution

**Files:**
- Create: `tests/fixtures/share_link_resolvable.html` (copy from scratchpad, see Step 1)
- Create: `tests/fixtures/share_link_dead.html` (copy from scratchpad, see Step 1)
- Modify: `anghami.py`
- Test: `tests/test_anghami.py`

**Interfaces:**
- Consumes: existing `AnghamiError`, `REQUEST_HEADERS`, `requests` (curl_cffi), `validate_playlist_url`, `parse_playlist_page`.
- Produces:
  - `SHARE_URL_RE` — matches `https?://open.anghami.com/<token>` and `https?://anghami.app.link/<token>`, token `[A-Za-z0-9]+`, optional trailing `/?#…`.
  - `CRAWLER_HEADERS = {"User-Agent": "Twitterbot/1.0", "Accept": "text/html"}`
  - `class ShareLinkIsNotPlaylist(AnghamiError)`, `class ShareLinkUnresolvable(AnghamiError)`
  - `resolve_share_link(url: str) -> str` — returns `https://play.anghami.com/playlist/<id>`; raises the two new errors; network errors propagate as `RequestError`.
  - `fetch_anghami_playlist(url)` — unchanged signature; now accepts share links; result dict gains `"resolved_url": str` (canonical URL fetched; equals the stripped input for canonical input). `"url"` stays the stripped input.

- [ ] **Step 1: Install fixtures**

```powershell
Copy-Item "C:\Users\Ihab\AppData\Local\Temp\claude\c--Projects-music-fetcher\c18de86d-ac30-47f8-a24e-c36f07db3532\scratchpad\share_link_resolvable.html" tests\fixtures\share_link_resolvable.html
Copy-Item "C:\Users\Ihab\AppData\Local\Temp\claude\c--Projects-music-fetcher\c18de86d-ac30-47f8-a24e-c36f07db3532\scratchpad\short_link_crawler.html" tests\fixtures\share_link_dead.html
```

Sanity check (both must print True):

```powershell
Select-String -Path tests\fixtures\share_link_resolvable.html -Pattern "anghami://playlist/" -Quiet
-not (Select-String -Path tests\fixtures\share_link_dead.html -Pattern "anghami://playlist/" -Quiet)
```

If the scratchpad files are missing, re-capture:

```powershell
python -c "from curl_cffi import requests as c; open(r'tests\fixtures\share_link_resolvable.html','w',encoding='utf-8').write(c.get('https://open.anghami.com/bOKwEJXsG4b', headers={'User-Agent':'Twitterbot/1.0','Accept':'text/html'}, timeout=15).text)"
python -c "from curl_cffi import requests as c; open(r'tests\fixtures\share_link_dead.html','w',encoding='utf-8').write(c.get('https://open.anghami.com/gcBOIGGVPMb', headers={'User-Agent':'Twitterbot/1.0','Accept':'text/html'}, timeout=15).text)"
```

- [ ] **Step 2: Write failing tests**

Append to `tests/test_anghami.py`:

```python
# ── Share-link resolution ─────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://open.anghami.com/bOKwEJXsG4b",
    "http://open.anghami.com/bOKwEJXsG4b",
    "https://open.anghami.com/bOKwEJXsG4b?utm=share",
    "https://anghami.app.link/bOKwEJXsG4b",
    "  https://open.anghami.com/bOKwEJXsG4b  ",
])
def test_share_url_re_matches(url):
    assert anghami.SHARE_URL_RE.match(url.strip())


@pytest.mark.parametrize("url", [
    "https://open.anghami.com/",                      # no token
    "https://open.spotify.com/abc",                   # wrong host
    "https://play.anghami.com/playlist/123",          # canonical, not a share link
    "https://evil.com/open.anghami.com/abc",
])
def test_share_url_re_rejects(url):
    assert anghami.SHARE_URL_RE.match(url.strip()) is None


def _fake_get_returning(body, status=200):
    def fake_get(url, headers=None, timeout=None, impersonate=None, **kw):
        return _FakeResponse(status, body)
    return fake_get


def test_resolve_share_link_happy_path(monkeypatch):
    body = _read("share_link_resolvable.html")
    captured = {}

    def fake_get(url, headers=None, timeout=None, **kw):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse(200, body)

    monkeypatch.setattr(anghami.requests, "get", fake_get)
    resolved = anghami.resolve_share_link("https://open.anghami.com/bOKwEJXsG4b")
    assert resolved == "https://play.anghami.com/playlist/284374449"
    assert captured["headers"]["User-Agent"] == "Twitterbot/1.0"


def test_resolve_share_link_dead(monkeypatch):
    monkeypatch.setattr(anghami.requests, "get",
                        _fake_get_returning(_read("share_link_dead.html")))
    with pytest.raises(anghami.ShareLinkUnresolvable):
        anghami.resolve_share_link("https://open.anghami.com/gcBOIGGVPMb")


def test_resolve_share_link_song(monkeypatch):
    body = '<html><meta property="al:ios:url" content="anghami://song/12345?x=1"></html>'
    monkeypatch.setattr(anghami.requests, "get", _fake_get_returning(body))
    with pytest.raises(anghami.ShareLinkIsNotPlaylist):
        anghami.resolve_share_link("https://open.anghami.com/someSongToken")


def test_fetch_resolves_share_link_end_to_end(monkeypatch):
    """fetch_anghami_playlist on a share link: crawler fetch -> canonical fetch -> parse."""
    share_body = _read("share_link_resolvable.html")
    playlist_body = _read("playlist_page.html")
    calls = []

    def fake_get(url, headers=None, timeout=None, impersonate=None, **kw):
        calls.append({"url": url, "headers": headers, "impersonate": impersonate})
        if "open.anghami.com" in url:
            return _FakeResponse(200, share_body)
        return _FakeResponse(200, playlist_body)

    monkeypatch.setattr(anghami.requests, "get", fake_get)
    result = anghami.fetch_anghami_playlist(" https://open.anghami.com/bOKwEJXsG4b ")
    assert result["url"] == "https://open.anghami.com/bOKwEJXsG4b"
    assert result["resolved_url"] == "https://play.anghami.com/playlist/284374449"
    assert len(result["tracks"]) > 0
    assert len(calls) == 2
    assert calls[0]["headers"]["User-Agent"] == "Twitterbot/1.0"
    assert calls[1]["url"] == "https://play.anghami.com/playlist/284374449"
    assert calls[1]["impersonate"] == "chrome"


def test_fetch_canonical_url_gains_resolved_url(monkeypatch):
    monkeypatch.setattr(anghami.requests, "get",
                        _fake_get_returning(_read("playlist_page.html")))
    result = anghami.fetch_anghami_playlist("https://play.anghami.com/playlist/6471050")
    assert result["resolved_url"] == "https://play.anghami.com/playlist/6471050"
    assert result["url"] == "https://play.anghami.com/playlist/6471050"


def test_fetch_invalid_url_still_no_network(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("network must not be touched for an invalid URL")

    monkeypatch.setattr(anghami.requests, "get", explode)
    with pytest.raises(anghami.InvalidUrl):
        anghami.fetch_anghami_playlist("https://example.com/playlist/1")
```

Note: `_FakeResponse` and `_read` already exist in this file (Tasks 3–4 of the previous plan). `_FakeResponse.raise_for_status` raises `anghami.RequestError` for status ≥ 400.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: new tests FAIL with `AttributeError: module 'anghami' has no attribute 'SHARE_URL_RE'` (and the resolve/fetch ones similarly); all pre-existing tests PASS.

- [ ] **Step 4: Implement**

In `anghami.py`:

(a) After `PLAYLIST_URL_RE` (line 10), add:

```python
# Share links produced by the mobile app's Share button. These are Branch.io deep
# links: followed normally they never reach the playlist (desktop -> landing page,
# iOS -> App Store, Android -> app intent). Fetched with a social-crawler
# User-Agent, though, the page embeds app deep-links (anghami://playlist/<id>)
# we can extract. Dead/expired links expose no such marker.
SHARE_URL_RE = re.compile(
    r"^https?://(?:open\.anghami\.com|anghami\.app\.link)/([A-Za-z0-9]+)(?:[/?#].*)?$"
)

CRAWLER_HEADERS = {
    "User-Agent": "Twitterbot/1.0",
    "Accept": "text/html",
}
```

(b) After `class ParseError(AnghamiError)`, add:

```python
class ShareLinkIsNotPlaylist(AnghamiError):
    pass


class ShareLinkUnresolvable(AnghamiError):
    pass
```

(c) After `validate_playlist_url`, add:

```python
def resolve_share_link(url: str) -> str:
    """Resolve an open.anghami.com / anghami.app.link share link to the canonical
    play.anghami.com playlist URL via the crawler-UA trick (see SHARE_URL_RE note)."""
    resp = requests.get(url.strip(), headers=CRAWLER_HEADERS, timeout=15,
                        max_redirects=10)
    resp.raise_for_status()
    m = re.search(r"anghami://playlist/(\d+)", resp.text)
    if m:
        return f"https://play.anghami.com/playlist/{m.group(1)}"
    if "anghami://song/" in resp.text:
        raise ShareLinkIsNotPlaylist(url)
    raise ShareLinkUnresolvable(url)
```

(d) Replace `fetch_anghami_playlist` with:

```python
def fetch_anghami_playlist(url: str) -> dict:
    """Fetch and parse a playlist from a canonical play.anghami.com URL or an
    open.anghami.com/anghami.app.link share link (resolved first)."""
    input_url = url.strip()
    canonical = input_url
    if SHARE_URL_RE.match(input_url):
        canonical = resolve_share_link(input_url)
    validate_playlist_url(canonical)
    resp = requests.get(canonical, headers=REQUEST_HEADERS, timeout=15, impersonate="chrome")
    if resp.status_code == 404:
        raise PlaylistNotFound()
    resp.raise_for_status()
    result = parse_playlist_page(resp.text)
    result["url"] = input_url
    result["resolved_url"] = canonical
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_anghami.py -v`
Expected: all PASS (including the pre-existing fetch tests — `result["url"]` semantics are unchanged for canonical input).

- [ ] **Step 6: Full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS (endpoint tests unaffected — they mock `fetch_anghami_playlist` itself; the mocks gain `resolved_url` in Task 2).

- [ ] **Step 7: Checkpoint** — report; committing per the execution agreement.

---

### Task 2: Endpoint — tracks in response, resolved_url, new error mappings

**Files:**
- Modify: `main.py` (the `anghami_fetch` endpoint, currently `main.py:586-611`)
- Test: `tests/test_endpoints.py`

**Interfaces:**
- Consumes: `anghami.fetch_anghami_playlist` returning `{name, tracks, declared_total, url, resolved_url}`; exceptions `ShareLinkIsNotPlaylist`, `ShareLinkUnresolvable` (Task 1).
- Produces: `POST /api/anghami/fetch` response `{name, url, resolved_url, total, declared_total, truncated, tracks: [{artist, title}]}`; error mappings per Global Constraints. Task 3's frontend relies on `tracks` and the error details.

- [ ] **Step 1: Update existing tests + write failing tests**

In `tests/test_endpoints.py`:

(a) Replace `test_anghami_fetch_happy_path` with (mock gains `resolved_url`, expected response gains `resolved_url` + `tracks`):

```python
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
```

(b) In `test_anghami_fetch_truncated_flag`, add `"resolved_url": "u",` to the mocked dict (after `"url": url,`).

(c) Extend the error parametrize list with the two new cases and the updated InvalidUrl copy check:

```python
@pytest.mark.parametrize("exc,status,detail_start", [
    (anghami.InvalidUrl("x"), 400, "That doesn't look like an Anghami playlist link"),
    (anghami.PlaylistNotFound(), 404, "Anghami says this playlist doesn't exist"),
    (anghami.ParseError("x"), 502, "Couldn't read the playlist page"),
    (anghami.RequestError("x"), 502, "Couldn't reach Anghami"),
    (anghami.ShareLinkIsNotPlaylist("x"), 400, "That share link points to a single song"),
    (anghami.ShareLinkUnresolvable("x"), 404, "Couldn't resolve this share link"),
])
```

(d) Add a test pinning the full updated InvalidUrl copy:

```python
def test_invalid_url_detail_mentions_share_links(client, monkeypatch):
    def boom(url):
        raise anghami.InvalidUrl(url)
    monkeypatch.setattr(anghami, "fetch_anghami_playlist", boom)
    resp = client.post("/api/anghami/fetch", json={"url": "x"})
    assert resp.json()["detail"] == ("That doesn't look like an Anghami playlist link "
                                     "(expected play.anghami.com/playlist/… or an "
                                     "open.anghami.com share link)")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_endpoints.py -v`
Expected: the updated happy-path test FAILS (response lacks `resolved_url`/`tracks`); the two new error cases FAIL (unhandled exception → 500); the copy test FAILS.

- [ ] **Step 3: Implement**

In `main.py`, replace the `anghami_fetch` endpoint body:

```python
@app.post("/api/anghami/fetch")
async def anghami_fetch(req: AnghamiFetchRequest):
    global _anghami_session
    try:
        # Network fetch + parse are blocking — keep them off the event loop.
        data = await asyncio.to_thread(anghami.fetch_anghami_playlist, req.url)
    except anghami.InvalidUrl:
        raise HTTPException(status_code=400, detail="That doesn't look like an Anghami playlist link (expected play.anghami.com/playlist/… or an open.anghami.com share link)")
    except anghami.ShareLinkIsNotPlaylist:
        raise HTTPException(status_code=400, detail="That share link points to a single song, not a playlist.")
    except anghami.ShareLinkUnresolvable:
        raise HTTPException(status_code=404, detail="Couldn't resolve this share link — it may be expired or app-only. Open play.anghami.com in a browser, find the playlist, and paste the address-bar link instead.")
    except anghami.PlaylistNotFound:
        raise HTTPException(status_code=404, detail="Anghami says this playlist doesn't exist or isn't public.")
    except anghami.ParseError:
        raise HTTPException(status_code=502, detail="Couldn't read the playlist page — Anghami may have changed their site.")
    except anghami.RequestError:
        raise HTTPException(status_code=502, detail="Couldn't reach Anghami — check your connection and try again.")
    # Shape tracks like scanned songs so the shared stream + frontend table work unchanged.
    songs = [{"filename": "", "album": "", "artist": t["artist"], "title": t["title"]}
             for t in data["tracks"]]
    _anghami_session = {
        "name": data["name"], "url": data["url"],
        "declared_total": data["declared_total"], "songs": songs,
    }
    return {
        "name": data["name"], "url": data["url"], "resolved_url": data["resolved_url"],
        "total": len(songs), "declared_total": data["declared_total"],
        "truncated": data["declared_total"] > len(songs),
        "tracks": data["tracks"],
    }
```

(The new `except` clauses sit between `InvalidUrl` and `PlaylistNotFound`; all are distinct subclasses of `AnghamiError`, so order among them is cosmetic — mirror the listing above.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** — report; committing per the execution agreement.

---

### Task 3: Frontend — preview rows + in-place stream updates

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: fetch response `tracks` array (Task 2); existing factory internals (`state`, `renderTable`, `reset`) and `fetchAnghamiPlaylist`.
- Produces: `state.prefilled` flag on every view instance (false unless a fetch pre-filled the table). No new element IDs.

All four edits are in the `<script>` block of `static/index.html`. Line numbers reference the current file; adapt if drifted, but keep content exact.

- [ ] **Step 1: Add `prefilled` to factory state and reset()**

In the factory `state` object (search for `renderTimer: null,` inside `createResultsView`), add a line after `filter: { text: '', status: 'all' },`:

```js
      prefilled: false,
```

In `reset()` (currently `static/index.html:1379`), after `state.resumeIndex = 0;` add:

```js
      state.prefilled = false;
```

- [ ] **Step 2: Stream handler updates pre-filled rows in place**

Replace (currently `static/index.html:1209`):

```js
        state.songs.push({ idx: state.songs.length, song: d.song, match: d.match, selected: false, skipped: !!d.skipped });
        if (d.match) state.stats.matched++;
        else if (d.skipped) state.stats.skipped++;
        else if (d.match === null) state.stats.notFound++;
```

with:

```js
        const existing = state.songs[d.index];
        if (existing) {
          // Pre-filled preview row (Anghami tab): fill the match in place.
          existing.song = d.song;
          existing.match = d.match;
          existing.skipped = !!d.skipped;
        } else {
          state.songs.push({ idx: state.songs.length, song: d.song, match: d.match, selected: false, skipped: !!d.skipped });
        }
        if (d.match) state.stats.matched++;
        else if (d.skipped) state.stats.skipped++;
        else if (d.match === null) state.stats.notFound++;
```

(For the library tab `state.songs[d.index]` never exists on a fresh search — rows are appended exactly as before. On resume, `d.index` starts past the already-appended rows in both tabs, so `existing` is only ever a preview row.)

- [ ] **Step 3: Fresh search resets pre-filled rows instead of wiping them**

In `startSearch`, replace:

```js
      if (!resume) {
        state.songs = [];
        state.page = 0;
        state.stats = { matched: 0, notFound: 0, skipped: 0, selected: 0 };
      }
```

with:

```js
      if (!resume) {
        if (state.prefilled) {
          // Keep the preview rows; clear match state so the search refills them.
          state.songs.forEach(item => { item.match = undefined; item.selected = false; item.skipped = false; });
        } else {
          state.songs = [];
        }
        state.page = 0;
        state.stats = { matched: 0, notFound: 0, skipped: 0, selected: 0 };
      }
```

- [ ] **Step 4: Pre-fill the table after a successful fetch**

In `fetchAnghamiPlaylist` (currently `static/index.html:1438-1441`), replace:

```js
      const data = await res.json();
      anghamiPlaylist = data;
      v.reset();  // clears any previous results; re-enables ang-search-btn (anghamiPlaylist is set)
```

with:

```js
      const data = await res.json();
      anghamiPlaylist = data;
      v.reset();  // clears any previous results; re-enables ang-search-btn (anghamiPlaylist is set)
      v.state.songs = data.tracks.map((t, i) => ({
        idx: i,
        song: { artist: t.artist, title: t.title, filename: '', album: '' },
        match: undefined, selected: false, skipped: false,
      }));
      v.state.prefilled = true;
      v.renderTable();
```

- [ ] **Step 5: Widen the URL input placeholder**

In the Anghami tab markup, change the `ang-url-input` placeholder from
`https://play.anghami.com/playlist/…` to
`https://play.anghami.com/playlist/… or https://open.anghami.com/…`.

- [ ] **Step 6: Static verification**

1. `python -m pytest tests/ -q` — untouched, all pass.
2. Start the server (`python -m uvicorn main:app --port 8000` in background), GET `http://127.0.0.1:8000/` → 200, stop the server.
3. Grep sanity: `Select-String -Path static\index.html -Pattern "prefilled"` → exactly the four occurrences added above (state init, reset, the startSearch condition, the fetchAnghamiPlaylist assignment).

- [ ] **Step 7: Manual browser checklist (for Ihab, listed in the report)**

1. Fetch a canonical playlist URL → all tracks appear immediately as rows with "—" status; Total stat = playlist size; checkboxes disabled.
2. Find on Spotify → rows fill in place (no duplicates appended); Stop → Continue resumes into the remaining preview rows.
3. Re-click Find after completion → table resets to preview state and re-fills (instant, cached).
4. Fetch a share link (`https://open.anghami.com/bOKwEJXsG4b`) → resolves and previews "G.O.A.T" (154 tracks).
5. Paste a dead share link (`https://open.anghami.com/gcBOIGGVPMb`) → inline error with the "expired or app-only" copy.
6. Library tab: run a search — behavior identical to before (append-only, no preview).

- [ ] **Step 8: Checkpoint** — report; committing per the execution agreement.

---

### Task 4: Docs + live end-to-end

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Update README**

1. In the "Importing an Anghami playlist" section, replace the step-1 sentence about copying the link with:

```markdown
1. Paste either link form into the tab:
   - the playlist's web URL — open [play.anghami.com](https://play.anghami.com) in a browser, open the playlist, and copy the address-bar link (`https://play.anghami.com/playlist/123456`), or
   - a **share link** from the mobile app (`https://open.anghami.com/…`) — the app resolves it to the playlist automatically. Song share links and expired links are rejected with a clear message.
```

2. In the same section, after the "Fetch Playlist" step, add:

```markdown
   The playlist's tracks appear in the table immediately after fetching, so you can review
   what was found before matching anything on Spotify.
```

3. Troubleshooting — add:

```markdown
**Share link can't be resolved**
`open.anghami.com` links are app-share deep links. The app resolves them by reading the
link's public preview page; links that are expired, region-locked, or point at a single
song can't be imported. Open [play.anghami.com](https://play.anghami.com) in a browser,
find the playlist, and paste the address-bar link instead.
```

- [ ] **Step 2: Full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 3: Live end-to-end sanity**

```powershell
python -c "import anghami; r = anghami.fetch_anghami_playlist('https://open.anghami.com/bOKwEJXsG4b'); print(r['name'], len(r['tracks']), r['resolved_url'])"
```

Expected: `G.O.A.T 154 https://play.anghami.com/playlist/284374449` (track count may drift if the owner edits the playlist — any triple-digit count is a pass).

- [ ] **Step 4: Checkpoint** — report; committing per the execution agreement.
