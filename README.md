# Musicload

Musicload is a self-hosted, mobile-friendly music discovery and download companion for Navidrome. Search and preview music, download complete albums or playlists, keep local files organized, and let Navidrome discover the results through its normal library scan.

Musicload writes to your own music directory. Navidrome remains the player and library server; no direct Navidrome API connection is required for library synchronization.

## Features

- Search songs and albums on YouTube Music.
- Paste YouTube, YouTube Music, and Deezer playlist links.
- Explore country charts, global charts, new releases, moods, genres, and curated playlists.
- Preview online results and local files in the built-in mini-player.
- Download individual tracks, complete albums, playlists, charts, or recommendations.
- Choose Opus, MP3, or FLAC output. FLAC is an output format and does not guarantee that the source was lossless.
- Track live download progress, speed, and ETA; cancel one or all active downloads.
- Organize files as `Artist/Year - Album/Track - Title` or use a flat directory.
- Add artwork, metadata, multi-artist tags, and LRCLIB lyrics in sidecar `.lrc` files.
- Avoid repeat downloads through a persistent recording index and an unavailable-track cooldown.
- Search, play, and delete files in the local library.
- Find possible duplicates by matching meaningful words in song names, then review and delete files manually.
- Install Musicload as a PWA and share recognized songs from Android directly to the search.
- Optionally show each user's ListenBrainz Weekly Exploration and download changed recommendations on a weekly schedule.
- Optionally use Navidrome accounts for login and administrator permissions.
- Manage application settings and `cookies.txt` from the web interface.
- Optionally write web downloads to M3U playlists and send Gotify notifications.
- Use the web interface or the `musicload` command-line client.

## Quick start

Clone the repository, then change only the left side of the music volume in `docker-compose.yml`:

```yaml
volumes:
  - /mnt/storage/media/Music:/downloads
  - ./.musicload:/data
```

Start Musicload:

```bash
docker compose up -d
```

Open `http://SERVER_IP:8000` and select the **Settings** button. Application options are stored in `/data/settings.json` and survive container updates.

The host port and volume mounts remain Docker-level settings because Docker needs them before Musicload starts. All Musicload behavior is configured in the web interface. Existing environment variables remain supported as fallbacks; saved web settings take precedence.

## Use the same library as Navidrome

Mount the same host music folder into both containers:

```yaml
# Musicload: write access
- /mnt/storage/media/Music:/downloads

# Navidrome: read-only access is sufficient
- /mnt/storage/media/Music:/music:ro
```

Musicload's default album layout is:

```text
Artist/
└── 2026 - Album/
    ├── 01 - First Track.opus
    ├── 01 - First Track.lrc
    └── 02 - Second Track.opus
```

Navidrome adds new files during its next regular library scan.

## Web settings

The administrator can configure the following from the Settings dialog:

- Default audio format and file organization
- Download directory and flat filename template
- Primary-artist folder naming
- ReplayGain/R128 processing when `rsgain` is available
- Official-audio/UGC filtering
- Retry cooldowns and lyrics-cache lifetime
- Cookie behavior and uploaded `cookies.txt`
- M3U playlist creation and Remote-User prefixes
- ListenBrainz web integration
- Navidrome login, signed-session secret, and HTTPS-only cookies
- Gotify download notifications
- Internal web port and CORS origins

Settings used while the server or authentication middleware starts are marked as restart-sensitive. Restart the container after changing the web port, CORS, ListenBrainz, or Navidrome login settings:

```bash
docker compose restart musicload
```

If the internal web port changes, update the container side of the Compose port mapping as well. For example, port `8001` requires `8001:8001`.

`MUSICLOAD_DATA_DIR` remains a bootstrap setting because it tells Musicload where `settings.json` itself is stored. The provided Docker image sets it to `/data`.

## Duplicate finder

Open **Settings → Library maintenance** and select **Find duplicates**. Musicload then opens the Library tab with the result groups.

The finder compares the meaningful words in song names. Capitalization, punctuation, accents, and common additions such as `official audio`, `lyrics`, `copy`, or a remaster year do not prevent a match. It does not compare file contents and does not automatically decide which copy should be kept.

Possible matches may still be different releases, masters, or formats. Review the cover, title, artist, album, path, format, size, and duration, and play each file before deciding. Every deletion is manual and requires confirmation; Musicload never selects or deletes a file on your behalf.

## Install as an app

For reliable PWA installation and Android sharing, serve Musicload through a trusted HTTPS address, for example with Caddy, Nginx Proxy Manager, Cloudflare Tunnel, or Tailscale.

### Android

1. Open the HTTPS address in Chrome.
2. Choose **Install app** or **Add to Home screen**.
3. To find a recognized song, share the Google result with Musicload.
4. Preview the matched audio track and download it.

### iPhone and iPad

1. Open the HTTPS address in Safari.
2. Select **Share** and **Add to Home Screen**.
3. Launch Musicload from its home-screen icon.

The PWA uses a network-first service worker and is not intended to work offline.

## Optional Navidrome login

Open **Settings**, enter the Navidrome URL, generate a session secret, save, and restart Musicload. Musicload validates credentials through Navidrome and never stores the user's password.

Sessions are signed, use HTTP-only cookies, and expire after seven days. Keep **HTTPS-only session cookie** enabled behind HTTPS. Disable it only for local HTTP testing.

When Navidrome login is disabled, anyone who can reach Musicload is treated as an administrator. Do not expose an unauthenticated instance directly to the public internet.

## Optional ListenBrainz Weekly Exploration

Enable the ListenBrainz tab in **Settings** and restart Musicload. Each signed-in user can then:

1. Save a ListenBrainz username.
2. Preview up to 50 matched Weekly Exploration tracks.
3. Download tracks individually or together.
4. Optionally choose a weekday and local time for automatic downloads.

Scheduled downloads use a separate per-account M3U playlist and run only when the matched recommendation set changes.

## Cookies

Open **Settings → Cookies** to upload a Netscape-format `cookies.txt`. Uploaded cookies are stored privately under `/data` and can be replaced or removed from the same dialog. They may help with content that requires a signed-in YouTube session.

## Command line

Search:

```bash
musicload search "Artist Track"
```

Download a search result, URL, or playlist:

```bash
musicload download --query "Artist Track"
musicload download --url "https://music.youtube.com/watch?v=..."
musicload download --url "https://www.deezer.com/playlist/..."
```

Explore charts and moods:

```bash
musicload explore charts --country DE
musicload explore moods
```

Enrich an existing library with missing metadata, artwork, lyrics, and optional ReplayGain tags:

```bash
musicload tag --dry-run /path/to/music
musicload tag /path/to/music
```

ReplayGain requires the external `rsgain` executable. Musicload reports its availability in Settings and skips ReplayGain safely when it is not installed.

## Data and privacy

Musicload stores settings, accounts, caches, logs, duplicate/download indexes, and uploaded cookies under `MUSICLOAD_DATA_DIR` (`/data` in Docker). Keep this directory persistent and private.

Please download only media you are authorized to save and follow applicable laws and service terms.

## License

Musicload is distributed under the [MIT License](LICENSE).

If Musicload improves your library workflow, you can [support the project on Ko-fi](https://ko-fi.com/kingdaniel4747).
