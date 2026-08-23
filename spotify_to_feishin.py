#!/usr/bin/env python3
"""
Spotify -> YouTube Music (via ytmusicapi) -> local music library ("Repo") pipeline.

NOTE on song matching: Odesli/song.link's public API was retired by Linktree
on 2026-08-01, so it can't be used for automated cross-platform lookups
anymore (the landing pages still work for manual, one-off use though --
paste the Spotify link stored in each file's tags into https://song.link
yourself anytime). Instead of a raw YouTube text search, this script
searches YouTube Music's curated official "songs" catalog via ytmusicapi
and scores candidates by title/artist similarity plus duration match
against the real Spotify runtime. Low-confidence matches are flagged in
issues.log so you can spot-check them.

Manual overrides ("hosts file" for songs): if a track can't be resolved
automatically (or you just prefer a specific video), add a line to
<OUTPUT_DIR>/overrides.txt:
    <spotify_track_url_or_uri>    <youtube_url>
and run with --use-overrides. Metadata (title/artist/album/cover/ISRC)
always comes from Spotify regardless -- only the audio source changes.

Modes:
  python spotify_to_feishin.py                            # PLAYLIST_NAME from .env
  python spotify_to_feishin.py --playlist "Roadtrip Mix"
  python spotify_to_feishin.py --playlist "Liked Songs"
  python spotify_to_feishin.py --album "Album Name"
  python spotify_to_feishin.py --album <spotify_album_url_or_uri>
  python spotify_to_feishin.py --playlist "Liked Songs" --use-overrides
  python spotify_to_feishin.py --playlist "Liked Songs" --use-overrides custom_overrides.txt

Headless / SSH-only machines (e.g. a Raspberry Pi):
  The Spotify OAuth login does NOT try to auto-open a browser on this
  machine (open_browser=False). It prints the login URL to the console --
  open that URL in a browser on ANY device, log in, then paste the
  resulting redirect URL back into this terminal when prompted. A
  .spotify_cache file is created afterwards so future runs skip the login.

Setup: see README.md / requirements.txt
"""

import os
import re
import sys
import time
import base64
import sqlite3
import logging
import argparse
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, APIC, TXXX, TSRC, USLT
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus
from mutagen.flac import Picture
from tqdm import tqdm
from ytmusicapi import YTMusic
import yt_dlp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("spotify2repo")

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback")
PLAYLIST_NAME_DEFAULT = os.getenv("PLAYLIST_NAME", "").strip()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./music_library"))
OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "mp3").strip().lower()
if OUTPUT_FORMAT not in ("mp3", "opus"):
    log.warning(f"Unbekanntes OUTPUT_FORMAT '{OUTPUT_FORMAT}', falle zurück auf mp3.")
    OUTPUT_FORMAT = "mp3"

LRCLIB_ENDPOINT = "https://lrclib.net/api/get"
YT_MUSIC_SEARCH_LIMIT = 8
MATCH_CONFIDENCE_WARN_THRESHOLD = 0.6  # below this, flag the match as uncertain

LIKED_SONGS_ALIASES = {"liked songs", "lieblingssongs", "your library", "meine musik"}

DB_PATH_NAME = "library.db"
ISSUES_LOG_NAME = "issues.log"
OVERRIDES_FILE_NAME = "overrides.txt"

SPOTIFY_ALBUM_ID_RE = re.compile(r"(?:spotify\.com/album/|spotify:album:)([a-zA-Z0-9]+)")
SPOTIFY_TRACK_ID_RE = re.compile(r"(?:spotify\.com/track/|spotify:track:)([a-zA-Z0-9]+)")

ytmusic = YTMusic()  # unauthenticated public catalog search is sufficient here

issues_logger = logging.getLogger("issues")
issues_logger.setLevel(logging.INFO)
issues_logger.propagate = False


def setup_issues_logger(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(output_dir / ISSUES_LOG_NAME, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    issues_logger.addHandler(handler)


def log_issue(track: dict, category: str, detail: str):
    issues_logger.info(f"[{category}] {track['artist']} - {track['title']} | {track['spotify_url']} | {detail}")


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or "Unknown"


def parse_spotify_timestamp(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_args():
    parser = argparse.ArgumentParser(description="Spotify -> YouTube Music -> lokale Musikbibliothek")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--playlist", metavar="NAME", help="Name einer Playlist oder 'Liked Songs'.")
    group.add_argument("--album", metavar="NAME_OR_URL", help="Albumname oder Spotify-Album-Link/URI.")
    parser.add_argument(
        "--use-overrides", nargs="?", const="__default__", default=None, metavar="PATH",
        help=f"Nutzt eine Override-Datei (Spotify-Link -> YouTube-Link). "
             f"Ohne Pfad wird <OUTPUT_DIR>/{OVERRIDES_FILE_NAME} verwendet.",
    )
    args = parser.parse_args()

    if args.album:
        target, is_album = args.album, True
    elif args.playlist:
        target, is_album = args.playlist, False
    elif PLAYLIST_NAME_DEFAULT:
        target, is_album = PLAYLIST_NAME_DEFAULT, False
    else:
        log.error("Kein Ziel angegeben: nutze --playlist NAME, --album NAME, oder setze PLAYLIST_NAME in der .env.")
        sys.exit(1)

    overrides_path = None
    if args.use_overrides is not None:
        overrides_path = (OUTPUT_DIR / OVERRIDES_FILE_NAME if args.use_overrides == "__default__"
                           else Path(args.use_overrides))
    return target, is_album, overrides_path


# ---------------------------------------------------------------------------
# Manual overrides ("hosts file" for songs)
# ---------------------------------------------------------------------------

def load_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        log.warning(f"Override-Datei nicht gefunden: {path} -- wird ignoriert.")
        return {}

    overrides = {}
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            log.warning(f"Override-Datei {path}, Zeile {line_no}: ungültiges Format, übersprungen.")
            continue
        spotify_part, youtube_url = parts[0], parts[1]
        m = SPOTIFY_TRACK_ID_RE.search(spotify_part)
        if not m:
            log.warning(f"Override-Datei {path}, Zeile {line_no}: keine gültige Spotify-Track-ID/URL erkannt.")
            continue
        overrides[m.group(1)] = youtube_url

    log.info(f"{len(overrides)} Override(s) aus {path} geladen.")
    return overrides


# ---------------------------------------------------------------------------
# SQLite "repo inventory"
# ---------------------------------------------------------------------------

def init_db(output_dir: Path) -> sqlite3.Connection:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_dir / DB_PATH_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            spotify_id TEXT PRIMARY KEY,
            isrc TEXT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT NOT NULL,
            track_number INTEGER,
            spotify_url TEXT,
            youtube_url TEXT,
            resolved_via TEXT,
            file_path TEXT NOT NULL,
            source TEXT,
            added_at TEXT,
            downloaded_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_isrc ON tracks(isrc)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_album ON tracks(artist, album)")
    conn.commit()
    return conn


def already_in_repo(conn: sqlite3.Connection, spotify_id: str, isrc: str | None) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
    if row:
        return row
    if isrc:
        row = conn.execute("SELECT * FROM tracks WHERE isrc = ?", (isrc,)).fetchone()
        if row:
            return row
    return None


def record_track(conn: sqlite3.Connection, track: dict, file_path: Path, youtube_url: str, resolved_via: str, source: str):
    conn.execute("""
        INSERT OR REPLACE INTO tracks
        (spotify_id, isrc, title, artist, album, track_number, spotify_url, youtube_url, resolved_via, file_path, source, added_at, downloaded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        track["spotify_id"], track.get("isrc"), track["title"], track["artist"], track["album"],
        track["track_number"], track["spotify_url"], youtube_url, resolved_via, str(file_path), source,
        track.get("added_at"), datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------

def get_spotify_client() -> spotipy.Spotify:
    if not CLIENT_ID or not CLIENT_SECRET:
        log.error("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET fehlen in der .env-Datei.")
        sys.exit(1)
    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope="playlist-read-private playlist-read-collaborative user-library-read",
        cache_path=".spotify_cache",
        open_browser=False,  # headless-friendly: prints the login URL instead of opening a browser
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def find_playlist_id_by_name(sp: spotipy.Spotify, name: str) -> str:
    offset = 0
    name_lower = name.lower().strip()
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for pl in page["items"]:
            if pl["name"].lower().strip() == name_lower:
                return pl["id"]
        if page["next"] is None:
            break
        offset += 50
    raise ValueError(
        f"Keine Playlist mit dem Namen '{name}' gefunden. "
        f"Prüfe Tippfehler oder ob die Playlist privat/kollaborativ und für diesen Account sichtbar ist."
    )


def _track_dict(t: dict, added_at: str | None) -> dict | None:
    if not t or t.get("is_local") or not t.get("external_urls"):
        return None
    images = (t.get("album") or {}).get("images") or []
    return {
        "spotify_id": t["id"],
        "isrc": (t.get("external_ids") or {}).get("isrc"),
        "title": t["name"],
        "artist": ", ".join(a["name"] for a in t["artists"]),
        "album": t["album"]["name"],
        "track_number": t.get("track_number") or 0,
        "spotify_url": t["external_urls"]["spotify"],
        "cover_url": images[0]["url"] if images else None,
        "duration_s": round((t.get("duration_ms") or 0) / 1000),
        "added_at": added_at,
    }


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    tracks, offset = [], 0
    fields = ("items(added_at,track(id,name,track_number,duration_ms,external_ids,external_urls,"
              "is_local,artists(name),album(name,images))),next")
    while True:
        page = sp.playlist_items(playlist_id, fields=fields, offset=offset, limit=100)
        for item in page["items"]:
            td = _track_dict(item.get("track"), item.get("added_at"))
            if td:
                tracks.append(td)
        if page["next"] is None:
            break
        offset += 100
    return tracks


def get_liked_songs(sp: spotipy.Spotify) -> list[dict]:
    tracks, offset = [], 0
    while True:
        page = sp.current_user_saved_tracks(limit=50, offset=offset)
        for item in page["items"]:
            td = _track_dict(item.get("track"), item.get("added_at"))
            if td:
                tracks.append(td)
        if page["next"] is None:
            break
        offset += 50
    return tracks


def resolve_album_id(sp: spotipy.Spotify, name_or_url: str) -> tuple[str, str]:
    m = SPOTIFY_ALBUM_ID_RE.search(name_or_url)
    if m:
        album_id = m.group(1)
        album = sp.album(album_id)
        return album_id, album["name"]
    results = sp.search(q=name_or_url, type="album", limit=5)
    items = results.get("albums", {}).get("items", [])
    if not items:
        raise ValueError(f"Kein Album mit dem Namen '{name_or_url}' auf Spotify gefunden.")
    best = items[0]
    artists = ", ".join(a["name"] for a in best["artists"])
    log.info(f"Album gefunden: '{best['name']}' von {artists} ({best.get('release_date', '?')[:4]})")
    return best["id"], best["name"]


def get_album_tracks(sp: spotipy.Spotify, album_id: str) -> list[dict]:
    album = sp.album(album_id)
    album_name = album["name"]
    images = album.get("images") or []
    cover_url = images[0]["url"] if images else None

    simplified = []
    offset = 0
    while True:
        page = sp.album_tracks(album_id, limit=50, offset=offset)
        simplified.extend(page["items"])
        if page["next"] is None:
            break
        offset += 50

    tracks = []
    for i in range(0, len(simplified), 50):
        chunk = simplified[i:i + 50]
        full_tracks = sp.tracks([t["id"] for t in chunk if t.get("id")])["tracks"]
        for t in full_tracks:
            if not t or t.get("is_local"):
                continue
            tracks.append({
                "spotify_id": t["id"],
                "isrc": (t.get("external_ids") or {}).get("isrc"),
                "title": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "album": album_name,
                "track_number": t.get("track_number") or 0,
                "spotify_url": t["external_urls"]["spotify"],
                "cover_url": cover_url,
                "duration_s": round((t.get("duration_ms") or 0) / 1000),
                "added_at": None,  # albums have no personal "added_at"
            })
    return tracks


def load_tracks(sp: spotipy.Spotify, name: str, is_album: bool) -> tuple[list[dict], str]:
    if is_album:
        album_id, album_name = resolve_album_id(sp, name)
        log.info(f"Lade alle Tracks von Album '{album_name}'...")
        return get_album_tracks(sp, album_id), f"Album: {album_name}"
    if name.lower() in LIKED_SONGS_ALIASES:
        log.info("Lade 'Liked Songs' (dein Spotify-Herz-Feed), nicht als normale Playlist abrufbar sonst.")
        return get_liked_songs(sp), "Liked Songs"
    playlist_id = find_playlist_id_by_name(sp, name)
    log.info(f"Playlist '{name}' gefunden, lade Tracks...")
    return get_playlist_tracks(sp, playlist_id), f"Playlist: {name}"


# ---------------------------------------------------------------------------
# YouTube Music resolution via ytmusicapi (replaces the retired Odesli API)
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _score_candidate(result: dict, track: dict) -> float:
    title_sim = _similarity(result.get("title", ""), track["title"])
    artist_names = ", ".join(a.get("name", "") for a in (result.get("artists") or []))
    primary_artist = track["artist"].split(",")[0].strip()
    artist_sim = _similarity(artist_names, primary_artist) if artist_names else 0.0

    target_duration = track.get("duration_s")
    result_duration = result.get("duration_seconds")
    if target_duration and result_duration:
        duration_score = max(0.0, 1 - abs(result_duration - target_duration) / max(target_duration, 1))
    else:
        duration_score = 0.5  # neutral if we can't compare

    return 0.4 * title_sim + 0.3 * artist_sim + 0.3 * duration_score


def search_youtube_music(track: dict) -> tuple[str | None, float]:
    """Returns (music.youtube.com URL, confidence score 0..1)."""
    query = f"{track['artist']} {track['title']}"
    results = []
    try:
        results = ytmusic.search(query, filter="songs", limit=YT_MUSIC_SEARCH_LIMIT) or []
    except Exception as e:
        log.warning(f"YT-Music-Suche (songs) fehlgeschlagen für '{query}': {e}")

    if not results:
        try:
            results = ytmusic.search(query, filter="videos", limit=YT_MUSIC_SEARCH_LIMIT) or []
        except Exception as e:
            log.error(f"YT-Music-Suche (videos) fehlgeschlagen für '{query}': {e}")

    candidates = [(r, _score_candidate(r, track)) for r in results if r.get("videoId")]
    if not candidates:
        return None, 0.0

    best, best_score = max(candidates, key=lambda c: c[1])
    return f"https://music.youtube.com/watch?v={best['videoId']}", best_score


def resolve_youtube_url(track: dict, overrides: dict[str, str]) -> tuple[str | None, float, str]:
    """Returns (youtube_url, confidence, resolved_via) where resolved_via
    is 'override' or 'search'."""
    override_url = overrides.get(track["spotify_id"])
    if override_url:
        return override_url, 1.0, "override"
    youtube_url, confidence = search_youtube_music(track)
    return youtube_url, confidence, "search"


# ---------------------------------------------------------------------------
# LRCLIB lyrics
# ---------------------------------------------------------------------------

def fetch_lyrics(track: dict) -> tuple[str | None, str | None]:
    """Returns (synced_lrc_text, plain_text). Either can be None."""
    params = {
        "track_name": track["title"],
        "artist_name": track["artist"].split(",")[0].strip(),
        "album_name": track["album"],
    }
    if track.get("duration_s"):
        params["duration"] = track["duration_s"]
    try:
        r = requests.get(LRCLIB_ENDPOINT, params=params, timeout=10)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        data = r.json()
        return data.get("syncedLyrics") or None, data.get("plainLyrics") or None
    except (requests.RequestException, ValueError):
        return None, None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_audio(youtube_url: str, dest_path: Path, fmt: str) -> bool:
    tmp_template = str(dest_path.with_suffix(""))
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": tmp_template + ".%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt,
            "preferredquality": "192" if fmt == "mp3" else "128",
        }],
        "quiet": True, "no_warnings": True, "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])
        return dest_path.exists()
    except Exception as e:
        log.error(f"yt-dlp Download fehlgeschlagen für {youtube_url}: {e}")
        return False


def ensure_album_cover(album_dir: Path, cover_url: str | None):
    cover_path = album_dir / "cover.jpg"
    if cover_path.exists() or not cover_url:
        return
    try:
        r = requests.get(cover_url, timeout=15)
        r.raise_for_status()
        cover_path.write_bytes(r.content)
    except requests.RequestException as e:
        log.warning(f"Album-Cover konnte nicht gespeichert werden ({album_dir.name}): {e}")


# ---------------------------------------------------------------------------
# Tagging (MP3 / Opus)
# ---------------------------------------------------------------------------

def _download_cover_bytes(cover_url: str | None) -> bytes | None:
    if not cover_url:
        return None
    try:
        r = requests.get(cover_url, timeout=15)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def tag_mp3(path: Path, track: dict, plain_lyrics: str | None):
    audio = MP3(path, ID3=ID3)
    try:
        audio.add_tags()
    except Exception:
        pass

    audio.tags.setall("TIT2", [TIT2(encoding=3, text=track["title"])])
    audio.tags.setall("TPE1", [TPE1(encoding=3, text=track["artist"])])
    audio.tags.setall("TALB", [TALB(encoding=3, text=track["album"])])
    if track["track_number"]:
        audio.tags.setall("TRCK", [TRCK(encoding=3, text=str(track["track_number"]))])
    if track.get("isrc"):
        audio.tags.setall("TSRC", [TSRC(encoding=3, text=track["isrc"])])
    if plain_lyrics:
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=plain_lyrics))

    audio.tags.add(TXXX(encoding=3, desc="SPOTIFY_URL", text=track["spotify_url"]))
    if track.get("added_at"):
        audio.tags.add(TXXX(encoding=3, desc="SPOTIFY_ADDED_AT", text=track["added_at"]))

    cover_bytes = _download_cover_bytes(track.get("cover_url"))
    if cover_bytes:
        audio.tags.setall("APIC", [APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes)])

    audio.save()


def tag_opus(path: Path, track: dict, plain_lyrics: str | None):
    audio = OggOpus(path)
    audio["title"] = track["title"]
    audio["artist"] = track["artist"]
    audio["album"] = track["album"]
    if track["track_number"]:
        audio["tracknumber"] = str(track["track_number"])
    if track.get("isrc"):
        audio["isrc"] = track["isrc"]
    if plain_lyrics:
        audio["lyrics"] = plain_lyrics
    audio["spotify_url"] = track["spotify_url"]
    if track.get("added_at"):
        audio["spotify_added_at"] = track["added_at"]

    cover_bytes = _download_cover_bytes(track.get("cover_url"))
    if cover_bytes:
        pic = Picture()
        pic.data = cover_bytes
        pic.type = 3
        pic.mime = "image/jpeg"
        audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]

    audio.save()


def tag_file(path: Path, track: dict, plain_lyrics: str | None, fmt: str):
    try:
        if fmt == "mp3":
            tag_mp3(path, track, plain_lyrics)
        else:
            tag_opus(path, track, plain_lyrics)
    except Exception as e:
        log.error(f"Tagging fehlgeschlagen für {path}: {e}")


def apply_added_at_as_mtime(path: Path, added_at: str | None):
    ts = parse_spotify_timestamp(added_at)
    if ts is not None:
        os.utime(path, (ts, ts))


def write_lrc_sidecar(dest_path: Path, synced_lyrics: str | None):
    if not synced_lyrics:
        return
    lrc_path = dest_path.with_suffix(".lrc")
    lrc_path.write_text(synced_lyrics, encoding="utf-8")


def handle_lyrics(track: dict, dest_path: Path) -> str | None:
    """Fetches lyrics, writes .lrc sidecar if synced, logs the outcome, and
    returns the plain-text lyrics (for tag embedding), if any."""
    synced_lyrics, plain_lyrics = fetch_lyrics(track)
    if synced_lyrics:
        write_lrc_sidecar(dest_path, synced_lyrics)
    elif plain_lyrics:
        log_issue(track, "LYRICS", "Nur unsynchronisierte Lyrics gefunden (kein .lrc erzeugt)")
    else:
        log_issue(track, "LYRICS", "Keine Lyrics auf LRCLIB gefunden")
    return plain_lyrics


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    name, is_album, overrides_path = parse_args()
    sp = get_spotify_client()

    overrides = load_overrides(overrides_path) if overrides_path else {}

    try:
        tracks, source_label = load_tracks(sp, name, is_album)
    except (ValueError, SpotifyException) as e:
        log.error(str(e))
        sys.exit(1)

    if not tracks:
        log.warning("Keine (abrufbaren) Tracks gefunden. Nichts zu tun.")
        return

    total = len(tracks)
    log.info(f"{total} Tracks geladen ({source_label}). Ausgabeformat: {OUTPUT_FORMAT}")

    conn = init_db(OUTPUT_DIR)
    setup_issues_logger(OUTPUT_DIR)

    processed = skipped = failed = overridden = 0
    start_time = time.monotonic()
    pbar = tqdm(total=total, unit="song", desc="Verarbeite", dynamic_ncols=True)

    for track in tracks:
        existing = already_in_repo(conn, track["spotify_id"], track.get("isrc"))
        if existing:
            skipped += 1
            pbar.set_postfix_str(f"übersprungen: {track['title'][:30]}")
            pbar.update(1)
            continue

        try:
            artist_dir = OUTPUT_DIR / sanitize(track["artist"])
            album_dir = artist_dir / sanitize(track["album"])
            album_dir.mkdir(parents=True, exist_ok=True)

            base_name = (f"{track['track_number']:02d} - {sanitize(track['title'])}"
                         if track["track_number"] else sanitize(track["title"]))
            dest_path = album_dir / f"{base_name}.{OUTPUT_FORMAT}"

            youtube_url, confidence, resolved_via = resolve_youtube_url(track, overrides)
            if not youtube_url:
                log_issue(track, "DOWNLOAD", "Kein Treffer im YT-Music-Katalog gefunden (auch kein Override)")
                failed += 1
                pbar.update(1)
                continue
            if resolved_via == "override":
                overridden += 1
            elif confidence < MATCH_CONFIDENCE_WARN_THRESHOLD:
                log_issue(track, "MATCH_UNCERTAIN",
                          f"Konfidenz nur {confidence:.2f} -> bitte prüfen: {youtube_url}")

            if not download_audio(youtube_url, dest_path, OUTPUT_FORMAT):
                log_issue(track, "DOWNLOAD", f"yt-dlp Download fehlgeschlagen ({youtube_url}, via {resolved_via})")
                failed += 1
                pbar.update(1)
                continue

            plain_lyrics = handle_lyrics(track, dest_path)
            tag_file(dest_path, track, plain_lyrics, OUTPUT_FORMAT)
            apply_added_at_as_mtime(dest_path, track.get("added_at"))
            ensure_album_cover(album_dir, track.get("cover_url"))
            record_track(conn, track, dest_path, youtube_url, resolved_via, source_label)
            processed += 1

        except Exception as e:
            log.error(f"Unerwarteter Fehler bei '{track.get('title', '?')}': {e}")
            log_issue(track, "ERROR", str(e))
            failed += 1

        done = processed + skipped + failed
        rate = (time.monotonic() - start_time) / done if done else 0
        eta_min = rate * (total - done) / 60
        pbar.set_postfix_str(f"OK:{processed} Skip:{skipped} Fail:{failed} ETA:{eta_min:.1f}min")
        pbar.update(1)

    pbar.close()
    conn.close()
    log.info(f"Fertig. Neu: {processed} (davon {overridden} via Override), übersprungen: {skipped}, "
              f"fehlgeschlagen: {failed} (Details in {OUTPUT_DIR / ISSUES_LOG_NAME}).")


if __name__ == "__main__":
    main()