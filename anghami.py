"""Anghami public-playlist import. All Anghami-specific fetching and parsing lives
here so a site change only ever touches this module."""
import json
import re

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException as RequestError
from bs4 import BeautifulSoup

PLAYLIST_URL_RE = re.compile(r"^https?://play\.anghami\.com/playlist/(\d+)(?:[/?#].*)?$")

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

# Anghami's WAF fingerprints the TLS handshake — plain requests/httpx get 403 even
# with browser headers; curl_cffi with impersonate="chrome" passes. 406 without
# browser-like Accept headers.
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


class ShareLinkIsNotPlaylist(AnghamiError):
    pass


class ShareLinkUnresolvable(AnghamiError):
    pass


def validate_playlist_url(url: str) -> str:
    m = PLAYLIST_URL_RE.match(url.strip())
    if not m:
        raise InvalidUrl(url)
    return m.group(1)


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
