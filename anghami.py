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
        raw_tracks = data.get("track") or []
        if isinstance(raw_tracks, dict):
            raw_tracks = [raw_tracks]
        for rec in raw_tracks:
            if not isinstance(rec, dict):
                continue
            title = (rec.get("name") or "").strip()
            artist = ((rec.get("byArtist") or {}).get("name") or "").strip()
            if title:
                tracks.append({"artist": artist, "title": title})
        if not tracks:
            raise ParseError("MusicPlaylist block has no readable tracks")
        raw_declared = data.get("numTracks", data.get("numtracks"))
        try:
            declared = int(raw_declared or 0)
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
