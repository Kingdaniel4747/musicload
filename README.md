# Musicload

Musicload is a self-hosted music discovery and download service. It combines a fast web interface with YouTube Music search, local-file playback, album downloads, scheduled synchronisation, and optional source plugins such as ListenBrainz.

It is designed to run alongside a personal music server. Downloaded files stay on your own storage and can be indexed by Navidrome, Jellyfin, or another compatible server.

## What it does

- Search for studio audio tracks and download them as Opus, MP3, or FLAC
- Browse albums, new releases, charts, moods, and local files
- Listen to short previews and delete local files from the web interface
- Download complete albums into artist/album folders
- Run scheduled playlist, explore, and plugin synchronisation
- Use ListenBrainz, RSS, Reddit, and Billboard source plugins
- Install the web interface as a mobile app

## Run locally with Docker Compose

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/musicload.git
cd musicload
docker compose up -d
```

Open `http://SERVER_IP:8000`. This starts both the web interface and the cron worker. Before the first start, copy `cron.example.yaml` to `cron.yaml` and configure the schedules you want.

For a NAS folder, different port, user ID, or GitHub image owner, edit the values directly in `docker-compose.yml`. The music path is the left side of this line:

```yaml
- ./music/folder:/downloads
```

Useful everyday commands:

```bash
# Show live logs
docker compose logs -f

# Install the newest published version
docker compose pull && docker compose up -d

# Stop everything
docker compose down
```

Musicload stores audio files and persistent application data in the selected music folder. The application data is kept in its hidden `.musicload` subfolder.

## Run the published image

After publishing the GitHub repository, GitHub Actions creates this image automatically:

```bash
docker pull ghcr.io/YOUR_GITHUB_USERNAME/musicload:latest

docker run -d \
  --name musicload \
  --restart unless-stopped \
  -p 8000:8000 \
  -v ./downloads:/downloads \
  -e MUSICLOAD_DOWNLOAD_DIR=/downloads \
  -e MUSICLOAD_AUDIO_FORMAT=opus \
  ghcr.io/YOUR_GITHUB_USERNAME/musicload:latest
```

For public `docker pull` access, set the published package to **Public** in the GitHub Packages settings after the first successful workflow run.

## Configuration

All settings use the `MUSICLOAD_` prefix. Common options:

```yaml
environment:
  - MUSICLOAD_DOWNLOAD_DIR=/downloads
  - MUSICLOAD_AUDIO_FORMAT=opus
  - MUSICLOAD_ORGANIZATION_MODE=album
  - MUSICLOAD_REPLAYGAIN=false
  - MUSICLOAD_WEB_PORT=8000
  # Optional: defaults to /downloads/.musicload
  - MUSICLOAD_DATA_DIR=/downloads/.musicload
```

See [`docker-compose.yml`](docker-compose.yml) and [`cron.example.yaml`](cron.example.yaml) for the complete setup. The Compose file also contains ready-to-uncomment options for cookies, Gotify, Navidrome protection, and Nginx Proxy Manager.

## HTTPS and the Android share feature

The Android share-to-Musicload feature needs a real installed PWA. Serve Musicload through HTTPS, for example with Caddy, Nginx Proxy Manager, Cloudflare Tunnel, or Tailscale. A plain `http://192.168.x.x` address can be used for the website, but Android may not register it as a reliable share target.

## Publish your own repository and Docker image

1. Create an empty GitHub repository named `musicload`.
2. Push this repository to it:

   ```bash
   git init
   git add .
   git commit -m "Initial Musicload release"
   git branch -M main
   git remote add origin https://github.com/YOUR_GITHUB_USERNAME/musicload.git
   git push -u origin main
   ```

3. Open the repository's **Actions** tab. The included workflow builds and publishes `ghcr.io/YOUR_GITHUB_USERNAME/musicload:latest` for every push to `main`.
4. After the first run, open the package settings under **Packages** and set its visibility to **Public** if other people should be able to pull it.
5. Add release notes and a version tag whenever you publish a new release.

## License

This repository is licensed under the [MIT License](LICENSE). The copyright notice in that file must remain in copies and derivatives.
