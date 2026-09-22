<div align="center">

# Spotify to Local Music Library

**[🇩🇪 Deutsch](#deutsch) | [🇬🇧 English](#english)**

</div>

---

<a id="deutsch"></a>

## Deutsch

Lädt Songs aus einer Spotify-Playlist, deinen "Liked Songs" oder einem ganzen Album als lokale Audiodateien herunter, versehen mit vollständigen Metadaten (Titel, Interpret, Album, Tracknummer, Erscheinungsjahr, Genre, Cover) und Lyrics. Alles wird ordentlich in Ordnern nach Interpret/Album sortiert.

### Features

- Playlists, Liked Songs oder ganze Alben herunterladen
- Automatische Metadaten: Titel, Interpret, Album, Albumkünstler, Tracknummer, Erscheinungsjahr, ISRC
- Genre-Anreicherung über MusicBrainz
- Synchronisierte Lyrics (als `.lrc`-Datei) über LRCLIB
- Cover-Art pro Album
- Ausgabeformat MP3 oder Opus
- Verhindert doppelte Downloads (auch über mehrere Playlists hinweg)
- Manuelle Korrekturen über eine einfache Textdatei möglich
- Räumt und aktualisiert bestehende Dateien automatisch auf, wenn sich Metadaten ändern

### Voraussetzungen

- Python 3.11 oder neuer
- [ffmpeg](https://ffmpeg.org/download.html) installiert und im `PATH`
- Ein Spotify-Developer-Account mit einer eigenen App ([developer.spotify.com](https://developer.spotify.com/dashboard))

### Installation

```bash
git clone https://github.com/johnnyjack123/Spotify-Downloader
mv .env.example .env
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Einrichtung

`.env`-Datei bearbeiten:

| Variable | Beschreibung |
|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Zugangsdaten deiner Spotify-App |
| `SPOTIFY_REDIRECT_URI` | Muss exakt mit der in der Spotify-App eingetragenen Redirect-URI übereinstimmen |
| `OUTPUT_DIR` | Zielordner für die Musikbibliothek |
| `OUTPUT_FORMAT` | `mp3` oder `opus` |
| `PLAYLIST_NAME` | *(optional)* Standard-Playlist, falls kein Kommandozeilen-Argument angegeben wird |

Beim ersten Start öffnet sich ein Login-Link für Spotify im Terminal. Öffne ihn in einem Browser (auf beliebigem Gerät), logge dich ein und kopiere die resultierende URL zurück ins Terminal, falls danach gefragt wird.

### Nutzung

```bash
# Eine bestimmte Playlist
python main.py --playlist "Roadtrip Mix"

# Die eigenen Liked Songs
python main.py --playlist "Liked Songs"

# Ein ganzes Album (Name oder Spotify-Link)
python main.py --album "The Dark Side of the Moon"
python main.py --album "https://open.spotify.com/album/..."
```

### Fehlende Songs manuell nachtragen

Findet das Skript für einen Song keinen passenden Treffer, kannst du ihn manuell zuordnen. Lege dafür eine `overrides.txt` im `OUTPUT_DIR` an:

```
# Spotify-Link                                          YouTube-Link
https://open.spotify.com/track/xxxxxxxxxxxxxxxxxxxxxx    https://www.youtube.com/watch?v=xxxxxxxxxxx
```

Und starte den Lauf mit zusätzlichem Flag:

```bash
python main.py --playlist "Liked Songs" --use-overrides
```

### Favoriten auf einem Musikserver markieren (optional)

Betreibst du einen eigenen Subsonic-kompatiblen Musikserver (z. B. Navidrome), kann `star_favorites.py` deine heruntergeladenen Liked Songs dort automatisch als Favoriten markieren:

```bash
cd utils
mv .env.example .env # Einträge in .env bearbeiten
python star_favorites.py /pfad/zur/musikbibliothek
```

Das ist komplett optional und unabhängig vom eigentlichen Download — ohne diese Variablen läuft `main.py` ganz normal weiter, nur ohne automatisches Markieren.
Sind Nutzerdaten für z.B. Navidrome hinterlegt, werden heruntergeladenen Songs aus "Liked Songs" automatisch favorisiert.

---

<a id="english"></a>

## English

Downloads songs from a Spotify playlist, your Liked Songs, or an entire album as local audio files, complete with full metadata (title, artist, album, track number, release year, genre, cover art) and lyrics. Everything is organized neatly into artist/album folders.

### Features

- Download playlists, Liked Songs, or entire albums
- Automatic metadata: title, artist, album, album artist, track number, release year, ISRC
- Genre enrichment via MusicBrainz
- Synced lyrics (as `.lrc` files) via LRCLIB
- Cover art per album
- Output as MP3 or Opus
- Prevents duplicate downloads (even across multiple playlists)
- Manual corrections possible via a simple text file
- Automatically reorganizes and updates existing files when metadata changes

### Requirements

- Python 3.11 or newer
- [ffmpeg](https://ffmpeg.org/download.html) installed and on your `PATH`
- A Spotify Developer account with your own app ([developer.spotify.com](https://developer.spotify.com/dashboard))

### Installation

```bash
git clone https://github.com/johnnyjack123/Spotify-Downloader
mv .env.example .env
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Setup

Edit `.env`-file:

| Variable | Description |
|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Credentials from your Spotify app |
| `SPOTIFY_REDIRECT_URI` | Must exactly match the redirect URI configured in your Spotify app |
| `OUTPUT_DIR` | Destination folder for your music library |
| `OUTPUT_FORMAT` | `mp3` or `opus` |
| `PLAYLIST_NAME` | *(optional)* Default playlist used when no command-line argument is given |

On first run, a Spotify login link is printed in the terminal. Open it in a browser (on any device), log in, and paste the resulting URL back into the terminal if prompted.

### Usage

```bash
# A specific playlist
python main.py --playlist "Roadtrip Mix"

# Your own Liked Songs
python main.py --playlist "Liked Songs"

# An entire album (name or Spotify link)
python main.py --album "The Dark Side of the Moon"
python main.py --album "https://open.spotify.com/album/..."
```

### Manually fixing missing songs

If the script can't find a good match for a song, you can map it manually. Create an `overrides.txt` in your `OUTPUT_DIR`:

```
# Spotify link                                            YouTube link
https://open.spotify.com/track/xxxxxxxxxxxxxxxxxxxxxx      https://www.youtube.com/watch?v=xxxxxxxxxxx
```

Then run with the extra flag:

```bash
python main.py --playlist "Liked Songs" --use-overrides
```

### Starring favorites on a music server (optional)

If you run your own Subsonic-compatible music server (e.g. Navidrome), `star_favorites.py` can automatically mark your downloaded Liked Songs as favorites there:

```bash
cd utils
mv .env.example .env # Edit entries in .env
python star_favorites.py /path/to/musiclibrary
```

This is entirely optional and independent of the main download process — without these variables, `main.py` runs normally, just without automatic starring.
If user data is stored for e.g. Navidrome, downloaded songs from "Liked Songs" will be automatically favorited.