# Music Fetcher

A local web app that scans your audio library, finds each track on Spotify, and adds the matches to a playlist.

---

## Prerequisites

- **Python 3.10+** (tested on 3.14)
- **A Spotify account**
- **A Spotify Developer app** (free, takes ~5 minutes)

---

## 1. Spotify Developer Setup

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and log in with your Spotify account.
2. Click **Create app**.
3. Fill in any name and description.
4. Under **Redirect URIs**, add: `http://127.0.0.1:8000/callback`
   > Use the exact IP — `localhost` is no longer accepted by Spotify.
5. Under **APIs/SDKs**, check **Web API**.
6. Save the app.
7. Open the app's **Settings → User Management** and add the email of every Spotify account that will use this app (including your own).
   > Since May 2025, apps default to **Development Mode**, which requires every user to be on this allowlist. Without it, write actions like creating playlists return `403 Forbidden`.
8. Copy the app's **Client ID** and **Client Secret** from the Settings page.

---

## 2. Installation

Clone or download the project, then from the project folder:

```
python -m pip install -r requirements.txt
```

---

## 3. Configuration

Create a `.env` file in the project root with:

```
SPOTIFY_CLIENT_ID="your_client_id_here"
SPOTIFY_CLIENT_SECRET="your_client_secret_here"
```

`.env` is git-ignored — never commit it.

---

## 4. Running the App

From the project folder:

```
python -m uvicorn main:app --reload --reload-include "*.py"
```

Then open http://127.0.0.1:8000 in your browser.

> `--reload-include "*.py"` keeps uvicorn from restarting every time the app writes its JSON caches. The `watchfiles` package (in `requirements.txt`) is required for that flag to work.

---

## 5. Using the App

### Step 1 — Connect Spotify
Click **Connect Spotify** in the top-right corner. You'll be sent to Spotify's consent screen; after approving, you're returned to the app. Your session is cached in `data/spotify_token_cache` so you don't log in every time.

### Step 2 — Add your music

You have four ways to point the app at audio files:

| Method | How |
|--------|-----|
| **Browse…** | Click **Browse…** next to the folder input. A native folder dialog opens on your machine; pick a folder and it's added immediately. Best for local libraries. |
| **Paste a path** | Type or paste a folder path (e.g. `E:\music`) into the input and click **Add** (or press Enter). |
| **Upload Files / Upload Folder** | Use the buttons in the drop zone to upload audio files or an entire folder tree. Uploaded files are copied into `uploads/`. Best when the music isn't on the same machine as the app. |
| **Drag & Drop** | Drag files or folders onto the drop zone. Folder trees are walked recursively. |

Folders are scanned recursively and the list is saved to `data/folders.json`.

### Step 3 — Find on Spotify
Click **Find on Spotify**. The app:

1. Scans every configured folder + the `uploads/` directory for audio files
2. Reads metadata tags (artist, title, album) — falls back to parsing the filename if tags are missing
3. Streams results to the page one track at a time

While searching you can:

- **Stop** — pause the search; the **Continue** button resumes from where you left off
- **Skip Track** — skip the song currently being scanned (useful if a track hangs)
- See the *Now scanning:* line update live with the current track and overall progress

Spotify search results are cached in `data/search_cache.json` and file metadata in `data/metadata_cache.json`, so subsequent runs are nearly instant for already-seen files.

If Spotify rate-limits the app (HTTP 429), the search stops gracefully with a "try again in N min" message — your progress so far is preserved and you can resume later.

### Step 4 — Review results
The table shows every file with its Spotify match. Use the filters above the table:

| Filter | Shows |
|--------|-------|
| All songs | Everything |
| Matched | Songs successfully found on Spotify |
| Not Found | Songs with no Spotify match |
| Selected | Only your checked rows |

The text filter searches across artist, title, and filename. The page-header checkbox selects every matched row across the entire current filter (not just the visible page).

### Step 5 — Add to playlist
1. Pick a playlist from the dropdown at the bottom, or click **+ New** to create one.
2. The bottom panel shows the playlist's current contents.
3. Check the tracks you want (or click **Select All Matched**).
4. Click **Add to Playlist**.

Duplicates are filtered out twice: once within your selection and once against tracks already in the target playlist. The result alert tells you how many were added and how many were skipped as duplicates.

---

## Project Structure

```
music_fetcher/
├── main.py                        # FastAPI backend
├── requirements.txt               # Python dependencies
├── .env                           # Spotify credentials (gitignored — create this yourself)
├── .gitignore
├── README.md
├── static/
│   ├── index.html                 # Frontend (single-page app)
│   └── tailwind.css               # Pre-built Tailwind stylesheet
├── data/                          # Runtime state (gitignored)
│   ├── folders.json               # Configured folder paths
│   ├── search_cache.json          # Spotify search results cache
│   ├── metadata_cache.json        # Local-file metadata cache
│   └── spotify_token_cache        # OAuth tokens
└── uploads/                       # Browser-uploaded files (gitignored)
```

---

## Supported Audio Formats

| Format | Extensions |
|--------|------------|
| MP3 | `.mp3` |
| FLAC | `.flac` |
| AAC | `.m4a`, `.aac` |
| OGG Vorbis | `.ogg` |

---

## Diagnostic Endpoints

These are useful when something looks wrong:

| URL | Purpose |
|-----|---------|
| `/api/auth-info` | Shows the cached token's scope, expiry, and the authenticated user's email/ID. Use this to confirm scopes are correct after re-auth. |
| `/api/debug-create-playlist` | Bypasses spotipy and POSTs directly to Spotify's API; returns the raw status, headers, and body. Useful for diagnosing 403s. |

---

## Troubleshooting

**`uvicorn` not recognized**
Run it via Python: `python -m uvicorn main:app --reload --reload-include "*.py"`.

**Redirect URI not accepted on Spotify dashboard**
Use `http://127.0.0.1:8000/callback`, not `localhost`. Spotify stopped accepting `localhost` as a hostname in April 2025.

**`Could not create playlist: 403 Forbidden, reason: None`**
Your Spotify account isn't on the app's User Management allowlist (or the entry didn't save). See step 7 of the Spotify Developer Setup. After adding yourself, **Logout** in the app, wait ~5 minutes for propagation, then **Connect Spotify** again. Verify with `/api/auth-info` that the email there matches what's in the dashboard.

**Spotify rate-limited the app**
Spotify enforces a rolling per-app limit (~180 requests/minute). The search loop sleeps 0.5 s between API calls to stay well under it. If you hit a 429, the search pauses cleanly — wait the indicated cooldown and click **Continue**. Long cooldowns (hours) are escalation penalties; once the timer ends, the cache means you don't pay the API cost for already-searched tracks.

**Page hangs / unresponsive while scanning a large library**
The app yields to the event loop on every iteration so the page should stay responsive. If it doesn't, make sure you're running with `--reload-include "*.py"` (the bare `--reload` watches every file, including `data/*.json`, and restarts mid-search). Restart the server.

**Song not found on Spotify**
Search uses the file's artist + title tags. Files with missing or incorrect tags often fail. Re-tag with [Mp3tag](https://www.mp3tag.de/) and delete `data/search_cache.json` to force a re-search.

**Token expired / auth error**
Click **Logout** in the app (it deletes `data/spotify_token_cache`), then **Connect Spotify** to re-grant.

**Import warnings in VS Code**
Open the Command Palette (`Ctrl+Shift+P`), run **Python: Select Interpreter**, and choose the interpreter at the path returned by `python -c "import sys; print(sys.executable)"`.

---

## Notes

- The app is intended to run locally — `127.0.0.1` only. There's no auth on the API; don't expose it to a network.
- The Spotify Developer app must remain in **Development Mode** unless you apply for **Extended Quota Mode** (Spotify-reviewed; needed only if you want to share the app with users outside the allowlist).
