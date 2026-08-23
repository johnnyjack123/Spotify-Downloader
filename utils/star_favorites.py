#!/usr/bin/env python3
"""
Separate, minimal script: recursively scans a folder for .mp3 files and
stars them as favorites on Navidrome (Subsonic API). No file is copied or
moved -- starring is a server-side annotation only, so a song can be a
favorite AND part of its album without any duplication.

Usage:
    python star_favorites.py /path/to/music_library

.env (same directory):
  NAVIDROME_URL=http://192.168.1.10:4533
  NAVIDROME_USER=youruser
  NAVIDROME_PASS=yourpass
"""

import os
import sys
import hashlib
import logging
from pathlib import Path

import requests
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from dotenv import load_dotenv
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("star_favorites")

load_dotenv()

NAVIDROME_URL = os.getenv("NAVIDROME_URL", "").strip().rstrip("/")
NAVIDROME_USER = os.getenv("NAVIDROME_USER", "").strip()
NAVIDROME_PASS = os.getenv("NAVIDROME_PASS", "").strip()

SUBSONIC_CLIENT = "star-favorites-script"
SUBSONIC_VERSION = "1.16.1"


def _auth_params() -> dict:
    salt = hashlib.md5(os.urandom(8)).hexdigest()
    token = hashlib.md5((NAVIDROME_PASS + salt).encode()).hexdigest()
    return {"u": NAVIDROME_USER, "t": token, "s": salt, "v": SUBSONIC_VERSION, "c": SUBSONIC_CLIENT, "f": "json"}


def read_tags(path: Path) -> tuple[str, str] | None:
    try:
        audio = MP3(path, ID3=ID3)
        title = str(audio.tags.get("TIT2", [""])[0]) if audio.tags.get("TIT2") else path.stem
        artist = str(audio.tags.get("TPE1", [""])[0]) if audio.tags.get("TPE1") else None
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
    # Fallback: title-only match if exactly one candidate
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

    if len(sys.argv) < 2:
        log.error("Bitte einen Ordnerpfad angeben: python star_favorites.py /pfad/zum/ordner")
        sys.exit(1)

    root = Path(sys.argv[1])
    if not root.is_dir():
        log.error(f"Ordner nicht gefunden: {root}")
        sys.exit(1)

    mp3_files = list(root.rglob("*.mp3"))
    if not mp3_files:
        log.warning(f"Keine .mp3-Dateien in {root} gefunden.")
        return

    starred, not_found, failed = 0, 0, 0
    for path in tqdm(mp3_files, desc="Starre Favoriten", unit="song"):
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

    log.info(f"Fertig. Als Favorit markiert: {starred}, nicht gefunden: {not_found}, fehlgeschlagen: {failed}.")


if __name__ == "__main__":
    main()