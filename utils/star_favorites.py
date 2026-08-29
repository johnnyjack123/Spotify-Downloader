#!/usr/bin/env python3
"""
Separate, minimal script: recursively scans a folder for audio files and
stars them as favorites on Navidrome (Subsonic API). No file is copied or
moved -- starring is a server-side annotation only.

Ordering matters here: Navidrome records a "dateLoved" timestamp for each
song at the moment the star request is received, and that's what most
clients (Feishin, Symfonium, Tempo) use to sort a "Favorites" view by
"recently starred". Since spotify_to_feishin.py already sets each file's
mtime to the real Spotify "added_at" date, this script stars files in
mtime order (oldest first) -- so the resulting dateLoved order on
Navidrome matches your original Spotify Liked Songs order. In your
client, make sure the Favorites/Starred view is sorted by "date starred"
(not alphabetically) to see this.

Usage:
    python star_favorites.py                  # uses OUTPUT_DIR from .env
    python star_favorites.py /pfad/zum/ordner  # explicit override

.env (same directory):
  OUTPUT_DIR=./music_library     # shared with spotify_to_feishin.py
  NAVIDROME_URL=http://192.168.1.10:4533
  NAVIDROME_USER=youruser
  NAVIDROME_PASS=yourpass
"""

import os
import sys
import time
import hashlib
import logging
from pathlib import Path

import requests
from mutagen import File as MutagenFile
from dotenv import load_dotenv
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("star_favorites")

load_dotenv()

OUTPUT_DIR_DEFAULT = os.getenv("OUTPUT_DIR", "").strip()
NAVIDROME_URL = os.getenv("NAVIDROME_URL", "").strip().rstrip("/")
NAVIDROME_USER = os.getenv("NAVIDROME_USER", "").strip()
NAVIDROME_PASS = os.getenv("NAVIDROME_PASS", "").strip()

SUBSONIC_CLIENT = "star-favorites-script"
SUBSONIC_VERSION = "1.16.1"
AUDIO_EXTENSIONS = ("*.mp3", "*.opus", "*.ogg", "*.flac", "*.m4a")


def _auth_params() -> dict:
    salt = hashlib.md5(os.urandom(8)).hexdigest()
    token = hashlib.md5((NAVIDROME_PASS + salt).encode()).hexdigest()
    return {"u": NAVIDROME_USER, "t": token, "s": salt, "v": SUBSONIC_VERSION, "c": SUBSONIC_CLIENT, "f": "json"}


def read_tags(path: Path) -> tuple[str, str] | None:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None or not audio.tags:
            log.warning(f"Keine Tags lesbar in {path}, überspringe.")
            return None
        title = (audio.tags.get("title") or [path.stem])[0]
        artist = (audio.tags.get("artist") or [None])[0]
        if not artist:
            log.warning(f"Keine Artist-Metadaten in {path}, überspringe.")
            return None
        return title, artist
    except Exception as e:
        log.warning(f"Konnte Tags nicht lesen aus {path}: {e}")
        return None


def find_song_id(title: str, artist: str) -> str | None:
    params = _auth_params()
    params["query"] = title
    try:
        r = requests.get(f"{NAVIDROME_URL}/rest/search3", params=params, timeout=15)
        r.raise_for_status()
        songs = r.json().get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
    except (requests.RequestException, ValueError) as e:
        log.error(f"Suche fehlgeschlagen für '{artist} - {title}': {e}")
        return None

    for s in songs:
        if s.get("title") == title and s.get("artist") == artist:
            return s["id"]
    title_matches = [s for s in songs if s.get("title") == title]
    if len(title_matches) == 1:
        return title_matches[0]["id"]
    return None


def star_song(song_id: str) -> bool:
    params = _auth_params()
    params["id"] = song_id
    try:
        r = requests.get(f"{NAVIDROME_URL}/rest/star", params=params, timeout=15)
        r.raise_for_status()
        return "error" not in r.json().get("subsonic-response", {})
    except (requests.RequestException, ValueError) as e:
        log.error(f"Star-Aufruf fehlgeschlagen für Song-ID {song_id}: {e}")
        return False


def main():
    if not (NAVIDROME_URL and NAVIDROME_USER and NAVIDROME_PASS):
        log.error("NAVIDROME_URL / NAVIDROME_USER / NAVIDROME_PASS fehlen in der .env-Datei.")
        sys.exit(1)

    if len(sys.argv) >= 2:
        root = Path(sys.argv[1])
    elif OUTPUT_DIR_DEFAULT:
        root = Path(OUTPUT_DIR_DEFAULT)
        log.info(f"Kein Pfad angegeben, nutze OUTPUT_DIR aus der .env: {root}")
    else:
        log.error("Kein Ordnerpfad angegeben und OUTPUT_DIR ist auch nicht in der .env gesetzt.")
        sys.exit(1)

    if not root.is_dir():
        log.error(f"Ordner nicht gefunden: {root}")
        sys.exit(1)

    audio_files = []
    for pattern in AUDIO_EXTENSIONS:
        audio_files.extend(root.rglob(pattern))

    if not audio_files:
        log.warning(f"Keine Audiodateien in {root} gefunden.")
        return

    # Sort by mtime (spotify_to_feishin.py sets this to the real Spotify
    # "added_at" date) so the resulting Navidrome "dateLoved" order matches
    # your original Liked Songs order -- oldest liked first.
    audio_files.sort(key=lambda p: p.stat().st_mtime)

    starred, not_found, failed = 0, 0, 0
    for path in tqdm(audio_files, desc="Starre Favoriten (in Spotify-Reihenfolge)", unit="song"):
        tags = read_tags(path)
        if not tags:
            failed += 1
            continue
        title, artist = tags
        song_id = find_song_id(title, artist)
        if not song_id:
            log.warning(f"Nicht in Navidrome gefunden: {artist} - {title}")
            not_found += 1
            continue
        if star_song(song_id):
            starred += 1
        else:
            failed += 1
        time.sleep(0.05)  # ensure strictly increasing dateLoved timestamps

    log.info(f"Fertig. Als Favorit markiert: {starred}, nicht gefunden: {not_found}, fehlgeschlagen: {failed}.")


if __name__ == "__main__":
    main()