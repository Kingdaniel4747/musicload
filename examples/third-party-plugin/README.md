# Example Third-Party Plugin for Musicload

This is an example third-party plugin that demonstrates how to create custom plugins for musicload.

## What This Plugin Does

The `demo` plugin reads song recommendations from a local JSON file. This is a simple example to show the plugin structure without requiring external API dependencies.

## Plugin Structure

```
musicload-demo/
├── README.md
├── pyproject.toml          # Package configuration with entry point
└── musicload_demo/
    ├── __init__.py
    └── plugin.py           # Plugin implementation
```

## Installation

From this directory:

```bash
pip install -e .
```

Or install from a published package:

```bash
pip install musicload-demo
```

## Usage

### CLI (One-time sync)

```bash
musicload plugins list
# Should show "demo" in the list

musicload plugins run demo --config '{"file": "/path/to/songs.json"}'
```

### Cron (Scheduled sync)

Add to your `cron.yaml`:

```yaml
plugins:
  my-demo-sync:
    type: demo
    sync: false
    schedule: "0 9 * * *"  # Daily at 9am
    config:
      file: /home/user/music-recommendations.json
```

### Input File Format

Create a JSON file with song recommendations:

```json
{
  "songs": [
    {
      "artist": "Artist Name",
      "title": "Song Title",
      "album": "Album Name"
    },
    {
      "artist": "Another Artist",
      "title": "Another Song"
    }
  ]
}
```

## How It Works

1. **Plugin Class**: Implements the `Plugin` protocol from `musicload.plugins.base`
2. **Configuration**: Defines required/optional config fields via `config_schema`
3. **Validation**: Validates configuration in `validate_config()`
4. **Song Fetching**: Returns a list of `Song` objects from `fetch_songs()`
5. **Entry Point**: Registered in `pyproject.toml` under `[project.entry-points."musicload.plugins"]`

## Creating Your Own Plugin

Use this as a template:

1. Copy this directory structure
2. Rename `musicload_demo` to `musicload_yourplugin`
3. Update `plugin.py` with your logic:
   - Change plugin name
   - Define your config schema
   - Implement config validation
   - Implement song fetching from your source
4. Update `pyproject.toml` with your package details
5. Install and test!

### Tips

- **Error Handling**: Raise `PluginError` for fetch failures
- **HTTP Requests**: Use `httpx` (already a musicload dependency)
- **Parsing**: Use appropriate libraries (BeautifulSoup, json, xml, etc.)
- **Logging**: Import and use `logging.getLogger(__name__)`
- **Testing**: Test with `musicload plugins run` before adding to cron

### Common Use Cases

- **Music Discovery APIs**: Last.fm, Apple Music, etc.
- **RSS/Atom Feeds**: Specialized podcast feeds, music blogs
- **Social Media**: Liked tracks from SoundCloud, Bandcamp, etc.
- **Local Sources**: Read from databases, CSV files, etc.
- **Web Scraping**: Scrape music recommendation sites (respect robots.txt!)

## Publishing Your Plugin

1. Update `pyproject.toml` with your details
2. Build: `python -m build`
3. Publish to PyPI: `python -m twine upload dist/*`
4. Users install: `pip install musicload-yourplugin`

Musicload will automatically discover your plugin via entry points!
