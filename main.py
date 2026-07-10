import os
import html
import json
import asyncio
import re
import shutil
from pathlib import Path
from typing import List, Literal, Optional
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mutagen import File as MutagenFile
from pydantic import BaseModel
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg"}
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CACHE_FILE = DATA_DIR / "search_cache.json"
FOLDERS_FILE = DATA_DIR / "folders.json"
METADATA_CACHE_FILE = DATA_DIR / "metadata_cache.json"
UPLOAD_DIR = Path("uploads")

_search_state = {"stop": False, "skip": False}
_meta_cache: dict = {}
_meta_dirty: bool = False
_songs_cache: Optional[List[dict]] = None
_songs_cache_key: Optional[str] = None

app = FastAPI()
# Rejects requests whose Host header isn't local — blocks DNS-rebinding attacks
# against this unauthenticated local API.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.mount("/static", StaticFiles(directory="static"), name="static")


def write_json_atomic(path: Path, obj, **dump_kwargs):
    """Write JSON via a temp file + rename so a crash mid-write can't corrupt
    the existing file (the search cache can represent hours of rate-limited work)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, **dump_kwargs)
    os.replace(tmp, path)


class AddToPlaylistRequest(BaseModel):
    playlist_id: str
    track_uris: List[str]


class CreatePlaylistRequest(BaseModel):
    name: str


class AddFolderRequest(BaseModel):
    path: str


# ── Folder config ─────────────────────────────────────────────────────────────

def load_folders() -> List[str]:
    if FOLDERS_FILE.exists():
        try:
            with open(FOLDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("folders", [])
        except Exception:
            pass
    return []


def save_folders(folders: List[str]):
    write_json_atomic(FOLDERS_FILE, {"folders": folders}, indent=2)


# ── Spotify auth ──────────────────────────────────────────────────────────────

def get_sp_oauth() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri="http://127.0.0.1:8000/callback",
        scope="playlist-modify-public playlist-modify-private playlist-read-private user-read-email",
        cache_path=str(DATA_DIR / "spotify_token_cache"),
        open_browser=False,
        show_dialog=True,
    )


def get_spotify() -> Optional[spotipy.Spotify]:
    try:
        oauth = get_sp_oauth()
        token_info = oauth.get_cached_token()
        if not token_info:
            return None
        if oauth.is_token_expired(token_info):
            token_info = oauth.refresh_access_token(token_info["refresh_token"])
        # status_retries=0 prevents urllib3 from sleeping for the full Retry-After
        # window when Spotify returns 429 — we surface it ourselves and stop the search
        # gracefully instead of freezing the event loop for hours.
        return spotipy.Spotify(auth=token_info["access_token"], status_retries=0)
    except Exception:
        return None


# ── Spotify search cache ──────────────────────────────────────────────────────

def load_cache() -> dict:
    if Path(CACHE_FILE).exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: dict):
    write_json_atomic(CACHE_FILE, cache, ensure_ascii=False, indent=2)


def make_cache_key(artist: str, title: str) -> str:
    a = " ".join(artist.lower().split())
    t = " ".join(title.lower().split())
    return f"{a}::{t}"


# ── Metadata cache (persists across restarts) ─────────────────────────────────

def _load_meta_cache():
    global _meta_cache
    if METADATA_CACHE_FILE.exists():
        try:
            with open(METADATA_CACHE_FILE, "r", encoding="utf-8") as f:
                _meta_cache = json.load(f)
        except Exception:
            _meta_cache = {}


def _save_meta_cache():
    global _meta_dirty
    if not _meta_dirty:
        return
    try:
        write_json_atomic(METADATA_CACHE_FILE, _meta_cache, ensure_ascii=False)
        _meta_dirty = False
    except Exception:
        pass


_load_meta_cache()


# ── Music scanning ────────────────────────────────────────────────────────────

def extract_metadata(filepath: Path) -> dict:
    artist, title, album = "", "", ""
    try:
        audio = MutagenFile(str(filepath), easy=True)
        if audio is not None:
            def get_tag(key):
                val = audio.get(key, [])
                return val[0] if val else ""
            artist = get_tag("artist") or get_tag("albumartist")
            title = get_tag("title")
            album = get_tag("album")
    except Exception:
        pass

    if not title:
        stem = filepath.stem
        stem = re.sub(r"^\d+[\s.\-]+", "", stem).strip()
        if " - " in stem and not artist:
            parts = stem.split(" - ", 1)
            artist, title = parts[0].strip(), parts[1].strip()
        else:
            title = stem.strip()

    return {
        "path": str(filepath),
        "filename": filepath.name,
        "folder": str(filepath.parent),
        "artist": artist.strip(),
        "title": title.strip(),
        "album": album.strip(),
    }


def extract_metadata_cached(filepath: Path) -> dict:
    global _meta_dirty
    key = str(filepath)
    try:
        mtime = filepath.stat().st_mtime
    except OSError:
        return extract_metadata(filepath)
    entry = _meta_cache.get(key)
    if entry and abs(entry.get("mtime", 0) - mtime) < 0.001:
        return entry["meta"]
    meta = extract_metadata(filepath)
    _meta_cache[key] = {"mtime": mtime, "meta": meta}
    _meta_dirty = True
    return meta


def invalidate_songs_cache():
    global _songs_cache
    _songs_cache = None


def scan_music_folders() -> List[dict]:
    global _songs_cache, _songs_cache_key, _meta_dirty
    all_folders = load_folders()
    if UPLOAD_DIR.exists():
        all_folders = all_folders + [str(UPLOAD_DIR.resolve())]
    cache_key = json.dumps(sorted(all_folders))
    if _songs_cache is not None and _songs_cache_key == cache_key:
        return _songs_cache
    songs = []
    seen_paths = set()
    scanned_folders = []
    for folder in all_folders:
        if not os.path.exists(folder):
            continue
        scanned_folders.append(folder)
        for dirpath, _, filenames in os.walk(folder):
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in SUPPORTED_EXTENSIONS:
                    filepath = Path(dirpath) / fname
                    seen_paths.add(str(filepath))
                    songs.append(extract_metadata_cached(filepath))
    # Drop cache entries for files that no longer exist so the cache doesn't
    # grow forever as files are deleted or renamed. Only prune within folders
    # that were actually scanned — an offline external drive keeps its entries.
    # str(Path(...)) so prefixes use the same separators as the cache keys
    prefixes = tuple(str(Path(f)) + os.sep for f in scanned_folders)
    stale = [k for k in _meta_cache if k not in seen_paths and k.startswith(prefixes)]
    if stale:
        for key in stale:
            del _meta_cache[key]
        _meta_dirty = True
    songs.sort(key=lambda x: (x["artist"].lower(), x["title"].lower()))
    _songs_cache = songs
    _songs_cache_key = cache_key
    _save_meta_cache()
    return songs


def search_spotify_track(sp: spotipy.Spotify, artist: str, title: str) -> Optional[dict]:
    clean = lambda s: re.sub(r'["\(\)\[\]]', "", s).strip()
    query = clean(title)
    if artist:
        query = f"{clean(artist)} {query}"
    results = sp.search(q=query, type="track", limit=1)
    tracks = results["tracks"]["items"]
    if not tracks:
        return None
    t = tracks[0]
    return {
        "uri": t["uri"],
        "id": t["id"],
        "name": t["name"],
        "artist": t["artists"][0]["name"],
        "album": t["album"]["name"],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse("static/index.html", media_type="text/html; charset=utf-8")


@app.get("/login")
def login():
    return RedirectResponse(get_sp_oauth().get_authorize_url())


@app.get("/callback")
def callback(code: Optional[str] = None, error: Optional[str] = None):
    if error:
        return HTMLResponse(f"<p>Authorization error: {html.escape(error)}</p>")
    if not code:
        return HTMLResponse("<p>No code received.</p>")
    get_sp_oauth().get_access_token(code)
    return RedirectResponse("/")


@app.get("/api/auth-status")
def auth_status():
    sp = get_spotify()
    if sp:
        try:
            user = sp.current_user()
            return {"authenticated": True, "display_name": user.get("display_name", "User")}
        except Exception:
            pass
    return {"authenticated": False}


@app.get("/api/auth-info")
def auth_info():
    try:
        oauth = get_sp_oauth()
        token = oauth.get_cached_token()
    except Exception as e:
        return {"authenticated": False, "error": str(e)}
    if not token:
        return {"authenticated": False}
    info = {
        "authenticated": True,
        "scope": token.get("scope"),
        "expires_at": token.get("expires_at"),
        "expired": oauth.is_token_expired(token),
        "configured_scope": "playlist-modify-public playlist-modify-private playlist-read-private user-read-email",
    }
    granted = set((token.get("scope") or "").split())
    required = set(info["configured_scope"].split())
    info["missing_scopes"] = sorted(required - granted)
    sp = get_spotify()
    if sp:
        try:
            user = sp.current_user()
            info["user_id"] = user.get("id")
            info["display_name"] = user.get("display_name")
            info["email"] = user.get("email")
        except Exception as e:
            info["user_error"] = str(e)
    return info


@app.get("/api/debug-create-playlist")
def debug_create_playlist(name: str = "MusicFetcher Debug Test"):
    import requests
    oauth = get_sp_oauth()
    token = oauth.get_cached_token()
    if not token:
        return {"error": "not authenticated"}
    if oauth.is_token_expired(token):
        token = oauth.refresh_access_token(token["refresh_token"])
    access_token = token["access_token"]
    me_resp = requests.get(
        "https://api.spotify.com/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    user_id = None
    try:
        user_id = me_resp.json().get("id")
    except Exception:
        pass
    create_resp = None
    if user_id:
        create_resp = requests.post(
            f"https://api.spotify.com/v1/users/{user_id}/playlists",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"name": name, "public": False, "description": "debug"},
            timeout=10,
        )
    return {
        "client_id_in_use": os.getenv("SPOTIFY_CLIENT_ID"),
        "token_scope": token.get("scope"),
        "me_status": me_resp.status_code,
        "me_body": me_resp.text[:500],
        "user_id": user_id,
        "create_status": create_resp.status_code if create_resp is not None else None,
        "create_headers": dict(create_resp.headers) if create_resp is not None else None,
        "create_body": create_resp.text[:1000] if create_resp is not None else None,
    }


@app.get("/api/logout")
def logout():
    for p in (
        DATA_DIR / "spotify_token_cache",
        DATA_DIR / ".spotify_token_cache",
        Path(".spotify_token_cache"),
    ):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    return {"ok": True}


# ── Folder management ─────────────────────────────────────────────────────────

@app.get("/api/folders")
def get_folders():
    return {"folders": load_folders()}


@app.get("/api/pick-folder")
def pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        path = filedialog.askdirectory(parent=root)
        root.destroy()
        if path:
            path = str(Path(path).resolve())
        return {"path": path or ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Folder picker unavailable: {e}")


@app.post("/api/folders")
def add_folder(req: AddFolderRequest):
    path = req.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="Path is empty")
    if not Path(path).exists():
        raise HTTPException(status_code=400, detail=f"Folder not found: {path}")
    folders = load_folders()
    if path not in folders:
        folders.append(path)
        save_folders(folders)
    invalidate_songs_cache()
    return {"folders": folders}


@app.delete("/api/folders/{index}")
def remove_folder(index: int):
    folders = load_folders()
    if index < 0 or index >= len(folders):
        raise HTTPException(status_code=400, detail="Invalid index")
    folders.pop(index)
    save_folders(folders)
    invalidate_songs_cache()
    return {"folders": folders}


# ── File upload ───────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    UPLOAD_DIR.mkdir(exist_ok=True)
    saved = []
    skipped = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            skipped.append(f.filename)
            continue
        # Sanitize relative path to prevent path traversal
        parts = [p for p in Path(f.filename).parts if p not in ("..", ".", "") and not os.path.isabs(p)]
        if not parts:
            skipped.append(f.filename)
            continue
        dest = UPLOAD_DIR.joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(str(Path(*parts)))
    invalidate_songs_cache()
    return {"saved": saved, "skipped": skipped, "count": len(saved)}


# ── Search stream ─────────────────────────────────────────────────────────────

@app.post("/api/search-control/{action}")
def search_control(action: Literal["stop", "skip"]):
    _search_state[action] = True
    return {"ok": True}


@app.get("/api/search-stream")
async def search_stream(start_index: int = 0):
    sp = get_spotify()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Folder scanning walks the filesystem and parses tags — run it in a worker
    # thread so a large first-time scan doesn't freeze the event loop.
    songs = await asyncio.to_thread(scan_music_folders)
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


# ── Playlists ─────────────────────────────────────────────────────────────────

@app.get("/api/playlists")
def get_playlists():
    sp = get_spotify()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated")
    playlists = []
    results = sp.current_user_playlists(limit=50)
    while results:
        for item in results["items"]:
            playlists.append({"id": item["id"], "name": item["name"]})
        results = sp.next(results) if results.get("next") else None
    return {"playlists": playlists}


@app.post("/api/create-playlist")
def create_playlist(req: CreatePlaylistRequest):
    sp = get_spotify()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated")
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name is empty")
    try:
        user_id = sp.current_user()["id"]
        playlist = sp.user_playlist_create(user_id, name, public=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Spotify error: {e}")
    return {"id": playlist["id"], "name": playlist["name"]}


def _iter_playlist_items(sp: spotipy.Spotify, playlist_id: str):
    results = sp.playlist_items(playlist_id, limit=100)
    while results:
        for item in results.get("items") or []:
            yield item
        results = sp.next(results) if results.get("next") else None


def _track_from_item(item: dict) -> Optional[dict]:
    """Spotify's playlist-items response has used both `track` and `item` as the field
    name for the embedded track over the years; accept either."""
    return (item or {}).get("track") or (item or {}).get("item")


@app.get("/api/playlist/{playlist_id}/tracks")
def get_playlist_tracks(playlist_id: str):
    sp = get_spotify()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        tracks = []
        for item in _iter_playlist_items(sp, playlist_id):
            t = _track_from_item(item)
            if not t:
                continue
            uri = t.get("uri")
            if not uri:
                continue
            tracks.append({
                "uri": uri,
                "name": t.get("name", ""),
                "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
            })
        return {"tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Spotify error: {e}")


@app.post("/api/add-to-playlist")
def add_to_playlist(req: AddToPlaylistRequest):
    sp = get_spotify()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated")
    existing = set()
    try:
        for item in _iter_playlist_items(sp, req.playlist_id):
            t = _track_from_item(item) or {}
            if t.get("uri"):
                existing.add(t["uri"])
    except Exception:
        pass
    seen = set()
    new_uris = []
    for uri in req.track_uris:
        if uri in seen or uri in existing:
            continue
        seen.add(uri)
        new_uris.append(uri)
    added = 0
    try:
        for i in range(0, len(new_uris), 100):
            sp.playlist_add_items(req.playlist_id, new_uris[i: i + 100])
            added += len(new_uris[i: i + 100])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Spotify error after adding {added} tracks: {e}")
    return {"added": added, "duplicates_skipped": len(req.track_uris) - added}
