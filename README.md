# Spotify → YouTube Music → Navidrome Pipeline

Zwei unabhängige Skripte:

- **`spotify_to_feishin.py`** — lädt eine Spotify-Playlist, deine Liked Songs oder ein komplettes Album, sucht im **offiziellen YouTube-Music-Katalog** (via `ytmusicapi`) das passende Lied, wählt per Titel-/Artist-/Längenabgleich den zuverlässigsten Treffer, lädt das Audio per `yt-dlp` herunter (MP3 oder Opus), taggt die Datei vollständig (Titel, Artist, Album, Tracknummer, Cover, ISRC), holt Lyrics von LRCLIB und organisiert alles in `Artist/Album/`-Ordnern. Eine SQLite-Datenbank (`library.db`) im Zielordner verhindert doppelte Downloads.
- **`star_favorites.py`** — durchsucht rekursiv einen Ordner nach Audiodateien und markiert sie in Navidrome als Favorit (Subsonic-API), komplett getrennt vom Download-Vorgang.

## Wichtiger Hinweis: song.link/Odesli-API ist tot

Linktree hat die öffentliche Odesli/song.link-API (`v1-alpha.1`) am **1. August 2026 abgeschaltet** — automatisierte Anfragen bekommen seitdem Fehler statt Ergebnisse. Die song.link-**Webseite** funktioniert für manuelle, einzelne Abfragen weiterhin (du kannst den in jeder Datei gespeicherten Spotify-Link jederzeit selbst dort einfügen), aber es gibt keine API mehr für Automatisierung.

## Wie das Skript den richtigen Song findet

Statt einer reinen YouTube-Textsuche durchsucht das Skript über `ytmusicapi` gezielt den **kuratierten offiziellen "Songs"-Katalog** von YouTube Music — dieselben lizenzierten Aufnahmen, die auch an Spotify verteilt werden. Aus mehreren Kandidaten wird der beste per Score aus Titel-Ähnlichkeit, Interpreten-Ähnlichkeit und Songlängen-Abgleich (gegen die echte Spotify-Laufzeit) ausgewählt. Liegt die Konfidenz des besten Treffers unter 60 %, wird das explizit im Log vermerkt (`[MATCH_UNCERTAIN]`).

## Manuelle Overrides ("Hosts-Datei" für Songs)

Manche Songs findet die automatische Suche nicht oder nur unzuverlässig — dafür gibt es `<OUTPUT_DIR>/overrides.txt` (Beispiel in `overrides.example.txt`). Format wie eine Hosts-Datei, eine Zeile pro Song:

```
# Spotify-Link/URI                                          YouTube-Link
https://open.spotify.com/track/4NuKN3QLEypxlFUoxC2kmZ       https://www.youtube.com/watch?v=abc123
spotify:track:5f8N3xyzABC123def456                          https://youtu.be/xyz789
```

Mit `--use-overrides` (optional ein eigener Pfad statt des Standardpfads) prüft das Skript für jeden Track zuerst, ob ein Override existiert, und lädt in dem Fall **genau diesen YouTube-Link** herunter — ganz ohne YT-Music-Suche. Die Metadaten (Titel, Interpret, Album, Cover, ISRC) kommen dabei trotzdem ausschließlich von der Spotify-API, exakt wie bei automatisch gefundenen Songs, weil YouTube-Metadaten (Videotitel etc.) dafür nicht zuverlässig genug sind.

Typischer Workflow:

1. Normalen Lauf starten, in `issues.log` nachsehen, welche Songs fehlgeschlagen sind.
2. Für diese Songs manuell den passenden YouTube-Link suchen und in `overrides.txt` eintragen.
3. Denselben Befehl erneut mit `--use-overrides` ausführen — nur die noch fehlenden Songs (die ja nie in `library.db` gelandet sind) werden erneut versucht und finden dann den Override.

## Voraussetzungen

### Python-Abhängigkeiten

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### ffmpeg

`yt-dlp` braucht `ffmpeg`, um das heruntergeladene Audio in MP3/Opus umzuwandeln. Das ist reine Audio-Transkodierung (kein Video) und läuft auch auf einem Raspberry Pi 3/4/5 problemlos in wenigen Sekunden pro Song.

- **Arch/CachyOS**: `sudo pacman -S ffmpeg`
- **Ubuntu/Debian/Raspberry Pi OS**: `sudo apt install ffmpeg`
- **Windows**: `winget install ffmpeg` oder Binary von [ffmpeg.org](https://ffmpeg.org/download.html) laden und zum `PATH` hinzufügen

Prüfen, ob es funktioniert:

```bash
ffmpeg -version
```

### yt-dlp aktuell halten

YouTube ändert öfter interne APIs, wodurch ältere `yt-dlp`-Versionen kaputtgehen. Regelmäßig updaten:

```bash
pip install -U yt-dlp
```

Falls Downloads plötzlich mit Fehlern wie "Sign in to confirm you're not a bot" fehlschlagen, hilft meist ein `yt-dlp`-Update oder das Hinterlegen von Cookies (`--cookies-from-browser`), das aktuell nicht im Skript enthalten ist.

## .env-Datei

Beide Skripte lesen Konfiguration aus einer `.env`-Datei im selben Verzeichnis. Diese Datei ist **nicht Teil des Repos** und sollte nie committed werden.

```env
# spotify_to_feishin.py
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8080/callback
PLAYLIST_NAME=Lieblingssongs
OUTPUT_DIR=./music_library
OUTPUT_FORMAT=mp3

# star_favorites.py
NAVIDROME_URL=
NAVIDROME_USER=
NAVIDROME_PASS=
```

- `PLAYLIST_NAME` ist nur ein Fallback-Standardwert für Läufe ohne `--playlist`/`--album`-Flag.
- `OUTPUT_FORMAT`: `mp3` (universell kompatibel) oder `opus` (ca. halb so groß bei vergleichbarer Qualität, von Symfonium/Tempo/Feishin unterstützt).

## Ausführung

```bash
# Playlist aus PLAYLIST_NAME (.env)
python spotify_to_feishin.py

# Eine bestimmte Playlist oder "Liked Songs"
python spotify_to_feishin.py --playlist "Roadtrip Mix"
python spotify_to_feishin.py --playlist "Liked Songs"

# Ein komplettes Album (Name oder Spotify-Link/URI)
python spotify_to_feishin.py --album "The Dark Side of the Moon"
python spotify_to_feishin.py --album "https://open.spotify.com/album/xyz..."

# Mit manuellen Overrides für zuvor fehlgeschlagene Songs
python spotify_to_feishin.py --playlist "Liked Songs" --use-overrides

# Favoriten separat markieren
python star_favorites.py ./music_library
```

`--playlist` und `--album` schließen sich gegenseitig aus.

## Spotify-Login auf einem headless Gerät (z. B. Raspberry Pi)

Der Login versucht auf einem Server ohne Display keinen Browser mehr zu öffnen (`open_browser=False`), sondern gibt die Login-URL im Terminal aus. Öffne diese URL auf einem beliebigen Gerät mit Browser, logge dich ein, und kopiere die resultierende Redirect-URL zurück ins Terminal, wenn danach gefragt wird. Alternativ: einmal auf einem Gerät mit Browser einloggen und die entstandene `.spotify_cache`-Datei per `scp` auf den Pi kopieren — der Token ist nicht an eine Maschine gebunden.

## Albumreihenfolge in Navidrome

Navidrome sortiert Tracks innerhalb eines Albums nach dem `tracknumber`-Tag (bei Mehrfach-CDs zusätzlich nach `discnumber`), nicht nach Dateiname oder Download-Reihenfolge. Das Skript schreibt diesen Tag korrekt, die Reihenfolge stimmt also automatisch nach dem Scan.

## Lyrics

Für jeden Song wird automatisch bei [LRCLIB](https://lrclib.net) (kostenlos, kein API-Key) nach Lyrics gesucht. Sind synchronisierte Lyrics vorhanden, landen sie als `Song.lrc` neben der Audiodatei — Navidrome ab Version 0.63 zeigt diese direkt an. Gibt es nur unsynchronisierten Text, wird er als Tag eingebettet, aber es entsteht keine `.lrc`-Datei. Beide Fälle (keine Lyrics / nur unsynchronisiert) werden ins Log geschrieben.

## Fortschrittsanzeige

Während des Laufs zeigt eine Fortschrittsleiste (via `tqdm`) live an, wie viele Songs erledigt, übersprungen oder fehlgeschlagen sind, plus eine laufend aktualisierte ETA-Schätzung in Minuten.

## Logdatei: issues.log

Alles, was nicht rund läuft, landet mit Zeitstempel, Interpret, Titel, Spotify-Link und Kategorie in `<OUTPUT_DIR>/issues.log`:

- `[DOWNLOAD]` — kein Treffer im YT-Music-Katalog gefunden (auch kein Override) oder yt-dlp-Fehler
- `[MATCH_UNCERTAIN]` — automatischer Treffer mit niedriger Konfidenz (< 60 %) — lohnt sich, manuell zu prüfen oder per Override zu korrigieren
- `[LYRICS]` — keine Lyrics gefunden, oder nur unsynchronisierte vorhanden
- `[ERROR]` — unerwarteter Fehler während der Verarbeitung

## Empfohlene Navidrome-Konfiguration

Damit "Zuletzt hinzugefügt" das tatsächliche Spotify-Hinzufügedatum statt des Download-Datums widerspiegelt, in der `navidrome.toml`:

```toml
RecentlyAddedByModTime = true
```

Das Skript setzt die Datei-mtime bereits automatisch auf den echten `added_at`-Zeitstempel aus Spotify (bei Alben gibt es kein persönliches Hinzufügedatum, daher bleibt die mtime dort unverändert).