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
