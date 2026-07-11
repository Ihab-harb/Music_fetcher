# Anghami Import: Playlist Preview + Share-Link Resolution — Design

**Date:** 2026-07-11
**Status:** Approved design (verbal); extends the 2026-07-10 Anghami import feature

## Summary

Two enhancements to the existing "Import from Anghami" tab:

1. **Preview** — after fetching a playlist, its tracks appear immediately in the results
   table (before any Spotify matching).
2. **Share-link resolution** — `open.anghami.com/<token>` links (the form the Anghami
   mobile app produces when sharing) are automatically resolved to the canonical
   `play.anghami.com/playlist/<id>` URL and imported like any other playlist.

## Background / verified facts (2026-07-11)

- `open.anghami.com/<token>` is a Branch.io-style deep link. Followed normally it never
  reaches the playlist (desktop → landing page, iOS → App Store, Android → app intent).
- **But** fetched with a social-crawler User-Agent (e.g. `Twitterbot/1.0`), the short-link
  page returns HTML embedding app deep-links of the form `anghami://playlist/<id>` (also
  `android-app://…/playlist/<id>` alternates). Verified live: token `bOKwEJXsG4b` →
  playlist id `284374449` → existing fetcher returned "G.O.A.T", 154/154 tracks.
- Dead/expired share links (verified with token `gcBOIGGVPMb`) return a generic app-promo
  page with no `anghami://` content marker — resolution must fail with a clear message.
- Song shares would carry `anghami://song/<id>` instead — distinguishable.

## Feature 1: Playlist preview

### Backend

`POST /api/anghami/fetch` adds `"tracks": [{"artist": str, "title": str}]` to its
response (the data already sits in `_anghami_session`; no extra requests).

### Frontend

- On successful fetch, `fetchAnghamiPlaylist()` pre-fills `views.anghami.state.songs`
  with one row per track: `{idx, song: {artist, title, filename: "", album: ""},
  match: undefined, selected: false, skipped: false}`, then renders.
  The factory already renders `match === undefined` as the idle "—" badge with a
  disabled checkbox, and filters/stats already treat it as neither matched nor
  not-found — no rendering changes needed. The stats bar's Total immediately shows the
  playlist size.
- Factory change 1 (inert for the library tab, which never pre-fills): the SSE
  `onmessage` handler updates the row at `d.index` in place when it exists
  (set `match`/`skipped`, increment stats), appending only when it doesn't.
- Factory change 2: fresh (non-resume) `startSearch()` currently clears `state.songs`;
  when rows are pre-filled it instead resets each row's `match`/`selected`/`skipped`
  to the preview state and zeroes stats, so re-searching doesn't blank the table.
  A state flag (`state.prefilled`) set by `fetchAnghamiPlaylist` distinguishes the cases.

## Feature 2: Share-link resolution

### anghami.py

- `SHARE_URL_RE` recognizing `https?://open.anghami.com/<token>` and
  `https?://anghami.app.link/<token>` (token: `[A-Za-z0-9]+`, optional query/fragment).
- New function `resolve_share_link(url: str) -> str`:
  1. GET the share URL with crawler headers (`User-Agent: Twitterbot/1.0`,
     `Accept: text/html`), via curl_cffi, 15 s timeout, redirects allowed but capped
     (the desktop fingerprint loop does not occur with the crawler UA; a redirect cap
     guards regressions).
  2. Search the response body for `anghami://playlist/(\d+)`.
  3. Found → return `https://play.anghami.com/playlist/<id>`.
  4. Not found but `anghami://song/` present → raise `ShareLinkIsNotPlaylist`.
  5. Neither → raise `ShareLinkUnresolvable` (dead/expired/app-only link).
- `fetch_anghami_playlist(url)` becomes the single entry point: if the URL matches
  `SHARE_URL_RE`, resolve first, then continue with the canonical URL exactly as today.
  `validate_playlist_url` keeps its current contract for canonical URLs; `InvalidUrl`
  is raised only when the input matches neither form.
- New exceptions subclass `AnghamiError`: `ShareLinkIsNotPlaylist`, `ShareLinkUnresolvable`.

### main.py

Error mapping additions in `/api/anghami/fetch` (before the generic handlers):

| Exception | Status | Detail (user-facing, verbatim) |
|---|---|---|
| `ShareLinkIsNotPlaylist` | 400 | `That share link points to a single song, not a playlist.` |
| `ShareLinkUnresolvable` | 404 | `Couldn't resolve this share link — it may be expired or app-only. Open play.anghami.com in a browser, find the playlist, and paste the address-bar link instead.` |

The `InvalidUrl` message is updated to acknowledge both accepted forms:
`That doesn't look like an Anghami playlist link (expected play.anghami.com/playlist/… or an open.anghami.com share link)`.

The fetch response gains `"resolved_url"` = the canonical URL actually fetched (equals
`url` for canonical input), so the UI can show what a share link resolved to.

### Frontend

- No new controls: both link forms go in the same input. On success with a share link,
  the meta line shows the playlist name/count as usual.
- Placeholder text of the URL input widens to `https://play.anghami.com/playlist/… or
  https://open.anghami.com/…`.

## Error handling summary

Unchanged paths (deleted playlist, parse failure, network, 429, auth) keep their
existing messages. The two new share-link errors appear inline under the URL bar like
all fetch errors.

## Testing

- Fixtures: crawler-served share-link page for a resolvable link (captured live) and
  the dead-link promo page (already captured 2026-07-10 as scratch; re-captured into
  `tests/fixtures/share_link_resolvable.html` / `share_link_dead.html`).
- Unit tests: `SHARE_URL_RE` matching (both hosts, query strings, non-matches);
  `resolve_share_link` happy path / song link / dead link (parsing the fixtures with
  monkeypatched transport); `fetch_anghami_playlist` share-link path chains resolution +
  fetch (both transport calls mocked).
- Endpoint tests: fetch with share URL (mocked `fetch_anghami_playlist`) returns
  `resolved_url`; new error mappings pin status + detail copy; fetch response includes
  `tracks`.
- Frontend preview behavior: manual click-through (no JS test rig): preview rows appear
  after fetch, search fills them in place, re-search resets instead of blanking,
  Stop/Continue keeps preview intact.
- Live sanity check before completion: resolve a real share link end-to-end.

## Decisions log

- Preview shown in the main results table, not a separate list (user choice, 2026-07-11).
- Share links auto-resolved via crawler-UA fetch (upgraded from "reject with guidance"
  after live verification proved resolution works, 2026-07-11).
- Dead links and song shares get distinct, actionable error copy.
