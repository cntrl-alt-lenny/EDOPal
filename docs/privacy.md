# Privacy and network use

EDOPal has no account, telemetry or automatic reporting. It scans your installation and reads your decks, replays, settings and recordings on your device. It does not upload those files or your support summary. Like any web service, a host can see your IP address and the request it receives. For example, the image hosts see which card IDs were requested, and Format Library sees the event search text you enter.

## Requests made by the app

| Host | When and what is requested | Code |
| --- | --- | --- |
| `images.ygoprodeck.com` | HD Cards requests full card images or cropped Field Spell art by card ID. A Field Spell fallback can request a related card’s ID. Explicitly picking artwork for a sleeve can also request a cropped image by ID. Card-name search is local. | [Default image URLs](../config.py#L59), [artwork request code](../main.py#L873), [sleeve request](../sleeves/cards.py#L100) |
| `pics.projectignis.org:2096` | Card and Field Spell artwork fallback. The path contains the requested card or donor ID. With original wording selected, an era card’s own images can take priority over a substitute. | [Default fallback URLs](../config.py#L61), [card candidates](../main.py#L955), [field art fallback](../main.py#L1100) |
| `api.github.com` | The Qt window checks the latest app release at startup and on request from Settings. Console and legacy sync can do the same. The legacy texture option also reads this project’s release list or a texture release by tag. | [Release endpoints](../main.py#L189), [Qt update check](../studio/shell.py#L249), [texture metadata](../main.py#L1200) |
| `github.com` | The pack engine’s default catalogue is the `manifest.json` release asset under `cntrl-alt-lenny/edopro-studio-content`. Pack browsers such as **Get more music** can refresh the catalogue and download the selected assets. The legacy texture downloader follows asset URLs supplied by GitHub’s release API. | [Default catalogue URL](../content/manifest.py#L9), [draft catalogue](content-release-draft.json), [texture asset URL](../main.py#L1277) |
| GitHub asset redirect hosts such as `release-assets.githubusercontent.com` | GitHub may redirect release downloads for the manifest, packs, previews and legacy textures. The asset host is chosen by GitHub at request time. | [Catalogue request](../content/catalogue.py#L198), [pack and preview requests](../content/download.py#L168), [GitHub release assets](https://docs.github.com/en/rest/releases/assets) |
| `formatlibrary.com` | The tournament browser requests formats, event pages, standings, deck previews and published deck files. Requests include the selected format, event search text, page number, public event code or deck ID as needed. | [Format Library API client](../tournament_decks.py#L22), [event filters](../tournament_decks.py#L141) |
| `cdn.formatlibrary.com` | The legacy Tk tournament window can request a published deck thumbnail by its public deck ID. The Qt deck preview uses card images from the selected installation. | [Thumbnail URL](../tournament_decks.py#L243) · [Legacy caller](../tournament_window.py#L726) · [Qt preview](../studio/pages/decks_preview_data.py) |

Artwork sync asks for individual images. **My decks** and explicit deck syncs use your local deck files to decide which card IDs to request. The requests can reveal that set of IDs to the artwork hosts. The hosts do not receive the deck file, its name or its local path.

Hosts can vary with configuration. The `sources` setting can change the HD Cards image URLs. The pack engine checks `EDOPRO_CONTENT_MANIFEST_URL`, then `content_manifest_url` in the config, then its GitHub default. It follows the manifest’s HTTP(S) download and preview URLs. Preview requests happen on demand, and a catalogue refresh can send its cached ETag or last-modified value to the catalogue host. HTTP redirects can also introduce destinations chosen by the server. For managed networks the HTTP client honors standard proxy variables, including `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY`. The `EDOPRO_CA_BUNDLE` setting adds a private certificate bundle. [TLS settings](../network_tls.py) · [Catalogue cache and URL selection](../content/catalogue.py)

## Links that open in your browser

- `github.com` for the Releases page when you follow the update notice, and for the project, issues and licence links in Settings. App updates are downloaded manually. [Update link](../studio/shell.py#L55) · [About links](../studio/pages/settings_views.py)
- `formatlibrary.com` for public event and deck pages opened from the tournament browser. [Public links](../tournament_decks.py#L38)
- `gamesdb.launchbox-app.com` for cover credits opened from the legacy music window. Cover metadata is bundled and selecting a credit opens its link in the browser. [Music catalogue](../assets/music/catalog.json#L12) · [Cover credit control](../music_window.py#L744)

Pack source URLs are attribution metadata and can point to other sites. Kenney’s URL is a credit, and the repository URLs that the health check reads from your EDOPro settings are inspected locally.

## What stays on your device

The packaged app keeps a per-user data folder with the historical name `EDOPro HD Sync`. Its default locations are below. [Data directory code](../config.py#L92)

| System | Default folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\EDOPro HD Sync`, with an app-data fallback |
| macOS | `~/Library/Application Support/EDOPro HD Sync` |
| Linux and SteamOS | `$XDG_CONFIG_HOME/EDOPro HD Sync`, or `~/.config/EDOPro HD Sync` |

Settings, download retry caches, content packs, local libraries and recovery information stay on disk. Some journals live in the selected EDOPro installation. The `--config PATH` option sets the config location and the state paths based on it. See the [safety guide](safety.md) for the individual write locations.

The Health check’s **support summary** uses fixed finding names, counts, version numbers and platform. It omits local paths, filenames, deck and skin names, settings and repository URLs. Detailed findings, ordinary sync reports, screenshots, decks, replays and backup ZIPs may contain private details. A replay export includes the game’s original recording, which can contain player names, and any notes you added. Review files and sharing codes before posting them. [Support summary formatter](../doctor/privacy.py)
