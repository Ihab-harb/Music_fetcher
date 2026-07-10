# Anghami Playlist Import — Design

**Date:** 2026-07-10
**Status:** Approved for planning

## Summary

Add a second workflow to Music Fetcher: import a public Anghami playlist by URL, match its
tracks on Spotify, review the matches, and add them to a Spotify playlist. The flow lives in
its own tab, separate from the local-library flow, but reuses the app's existing Spotify
search, cache, streaming, and playlist machinery.

## Scope

- **In:** one public Anghami playlist at a time, imported by pasting its
  `play.anghami.com/playlist/<id>` URL. No Anghami login.
- **Out (deferred):** Anghami liked songs and private playlists (would require account
  access; revisit later — workaround is copying likes into a public Anghami playlist),
  multiple queued playlists, one-shot auto-convert without review.

## Approach

Scrape the public playlist page HTML. Anghami playlist pages on `play.anghami.com` are
server-rendered: track titles, artist names, and IDs are present in the initial HTML, no
login required. Chosen over reverse-engineering Anghami's internal JSON API (undocumented,
possibly signed/authenticated, more fragile) and over Playwright automation (heavy
dependency, unnecessary while the page is server-rendered).

**Known risk:** Anghami can change their markup; the parser is isolated in one module and a
parse failure surfaces a distinct error. **Open implementation-time check:** whether very
long playlists are fully present in the server-rendered HTML or truncated/lazy-loaded. If
truncated, first look for an embedded JSON blob or a simple follow-up request; only if
neither exists, document a maximum and surface a warning when it's hit. The parser's output
contract is unchanged either way.

## Architecture

### Data flow

paste URL → `POST /api/anghami/fetch` (scrape + parse) → `GET /api/anghami/search-stream`
(SSE, one Spotify match per track) → review table → existing `POST /api/add-to-playlist`.

### New module: `anghami.py`

Isolates all Anghami-specific logic. Public function:

```
fetch_anghami_playlist(url: str) -> {"name": str, "url": str, "tracks": [{"artist": str, "title": str}]}
```

- Validates the URL: must be `play.anghami.com/playlist/<numeric id>` (http/https, optional
  query string). Anything else → `InvalidUrl`.
- Fetches with `curl_cffi` (`impersonate="chrome"`), browser-like headers, 15-second
  timeout. **Amended during implementation (2026-07-11):** Anghami's WAF fingerprints the
  TLS handshake — plain `requests`/`httpx` get `403 Forbidden` even with full browser
  headers, while curl/Chrome TLS fingerprints get `200` (verified empirically). `curl_cffi`
  provides a requests-compatible API with a Chrome TLS fingerprint. The module exposes a
  single `RequestError` alias so callers/tests don't depend on the transport library.
  (Also verified: Anghami returns `406 Not Acceptable` when browser-like headers are
  missing entirely.)
- Parses the page's embedded `application/ld+json` blocks (verified present): the
  schema.org `MusicPlaylist` object carries the playlist `name`, `numTracks`, and a
  `track` array of `MusicRecording` objects (`name`, `byArtist.name`). BeautifulSoup
  (`beautifulsoup4`, added to `requirements.txt`) locates the script tags; `json.loads`
  does the rest. This is more change-resistant than scraping DOM markup, and comparing
  `numTracks` against the parsed track count gives built-in truncation detection for
  long playlists (surfaced to the user as a warning instead of silent loss).
- Deleted/private playlists return HTTP 200 with a "Playlist deleted" page (verified) —
  detected by the absence of a `MusicPlaylist` block plus that marker text.
- Typed errors: `InvalidUrl`, `PlaylistNotFound` (Anghami's "Playlist deleted" page),
  `ParseError` (page fetched but no tracks found — the "markup changed" signal), plus
  network errors passed through distinctly.

### New endpoints in `main.py`

1. **`POST /api/anghami/fetch`** — body `{url}`. Runs `fetch_anghami_playlist` in a worker
   thread (`asyncio.to_thread`), stores the result in a module-level `_anghami_session`
   (in-memory only, not persisted — re-pasting a URL is cheap), and returns
   `{name, url, total, declared_total, truncated}` (the tracks themselves reach the
   frontend via the search stream, so the fetch response stays small). Typed errors map
   to 4xx/502 responses with user-facing messages.
2. **`GET /api/anghami/search-stream?start_index=N`** — SSE stream identical in event shape
   to the existing `/api/search-stream` (per-track `{index, total, song, match}`, plus
   `stopped`, `rate_limited`, `done`, `skipped`), but iterating
   `_anghami_session["tracks"]` instead of scanned files. Returns 409 if no playlist has
   been fetched. Reuses:
   - the same Spotify search function and `search_cache.json` (keyed by artist+title, so
     Anghami tracks and local files of the same song share cache hits),
   - the same 0.5 s inter-request throttle and 429 → `rate_limited` handling,
   - the same Stop/Skip state via the existing `/api/search-control`.

   To avoid duplicating the ~70-line streaming loop, it is factored into a shared async
   generator that both `/api/search-stream` and `/api/anghami/search-stream` call with
   their own track list. The local-library endpoint's behavior is unchanged.

### Unchanged

Playlist endpoints (`/api/playlists`, `/api/create-playlist`,
`/api/playlist/{id}/tracks`, `/api/add-to-playlist`) — they already operate on bare
Spotify URIs and serve both flows as-is.

## Frontend (`static/index.html`)

A tab switcher at the top of the page: **My Library** (today's UI, unchanged) and
**Import from Anghami**. The Spotify connect button stays global in the header. Switching
tabs preserves both tabs' state (both live in the DOM). The Anghami tab contains:

1. **URL bar** — input + **Fetch Playlist** button. On success shows playlist name and
   track count. Fetching a new URL replaces the current Anghami table, with a confirm
   dialog if the current table has matched-but-not-yet-added results.
2. **Find on Spotify** — streams results row by row with the live *Now scanning* line;
   Stop/Continue and Skip Track behave exactly as in the library flow (shared control
   endpoint and state).
3. **Review table** — a second instance of the results table with the same columns,
   filters (All / Matched / Not Found / Selected), text filter, pagination, and
   select-all-across-filter mechanics. The table logic is factored into a reusable
   function/component that both tabs instantiate — no copy-paste fork of the table code.
4. **Add to playlist panel** — same dropdown + playlist-contents preview + Add button.
   Convenience: **+ New** pre-fills the name field with the Anghami playlist's name.

## Error handling

Shown inline under the URL bar (not `alert()`):

| Condition | Message (gist) |
|---|---|
| URL doesn't match the playlist pattern | "That doesn't look like an Anghami playlist link (expected play.anghami.com/playlist/…)" |
| Playlist deleted / not public | "Anghami says this playlist doesn't exist or isn't public." |
| Anghami unreachable / network error | "Couldn't reach Anghami — check your connection and try again." |
| Page fetched but no tracks parsed | "Couldn't read the playlist page — Anghami may have changed their site." |
| Spotify not connected / 429 | Existing shared handling (auth prompt / cooldown message with Continue) |

## Testing

- **Parser unit tests** against saved HTML fixtures: (a) a real playlist page, (b) the
  "Playlist deleted" page. Pins parsing without hitting Anghami in CI.
- **URL validator** table-driven cases: valid https/http/query-string variants, a song link
  (not playlist), other-host URLs, garbage.
- **Manual end-to-end** with a real public playlist before completion, including an
  Arabic-titled playlist — transliteration mismatch between Anghami metadata and Spotify's
  catalog is the main match-quality risk.

## Decisions log

- Likes/account access: deferred (options considered: session-cookie paste, Playwright
  login, manual export import).
- Integration style: separate flow/tab, not a unified source — user preference.
- Batch size: one playlist at a time.
- Extraction: HTML scraping (Option A) over internal API (B) and Playwright (C).
