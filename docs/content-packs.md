# On-demand content packs

## Integration API

Create one `PackCache.for_config(cfg)` for the page. A regular run uses the
app data directory's `packs/` folder. A run with `--config PATH` uses
`packs/` beside that file. The manifest URL precedence is
`EDOPRO_CONTENT_MANIFEST_URL`, the `content_manifest_url` config setting,
then `content.manifest.DEFAULT_MANIFEST_URL`. Catalogues cached for one URL
are independent of those cached for another.

Call the async functions on a worker event loop. Call the synchronous cache
and appearance functions on a worker too. Forward progress and results from
the worker to the GUI via queued signals.

| Public API | Behaviour |
|---|---|
| `manifest.parse_manifest(data: bytes \| str) -> Manifest` | Validates the wire format and returns visible packs. |
| `Manifest.get(pack_id: str) -> Pack \| None` | Looks up a visible entry by ID. |
| `catalogue.bundled_manifest() -> Manifest` | Loads the embedded snapshot without network or disk access. |
| `catalogue.resolve_manifest_url(config_url: str = "") -> str` | Resolves and validates the URL override. |
| `await catalogue.fetch_catalogue(cache, *, config_url="", timeout=15) -> Catalogue` | Returns the manifest, source and optional diagnostic. |
| `Catalogue.list_packs(cache, *, app_version, kind=None) -> tuple[PackStatus, ...]` | Lists visible entries and their current states. |
| `catalogue.pack_status(cache, pack, *, app_version) -> PackStatus` | Refreshes state for one entry, with its cached version if present. |
| `await download.download_pack(cache, pack, *, app_version, progress=None, cancel=None, timeout=30) -> Path` | Fetches or reuses an exact, verified version. |
| `await download.fetch_preview(cache, pack, preview, *, progress=None, cancel=None, timeout=30) -> Path` | Lazily fetches or reuses a verified PNG, JPEG or WebP. |
| `cache.PackCache(root: Path \| None = None)` | Uses a caller-provided location or the standard app data cache. |
| `PackCache.for_config(cfg) -> PackCache` | Resolves the location from the active app configuration. |
| `PackCache.cached_versions(pack_id) -> tuple[CachedPack, ...]` | Returns the archive files for this ID, newest versions first. |
| `PackCache.use_pack(pack_id, *, exact=None) -> ContextManager[Path \| None]` | Rechecks the digest and holds the archive against removal for the scope. |
| `PackCache.list_cached() -> tuple[CacheEntry, ...]` | Reports archive, preview, and partial paths and actual sizes. |
| `PackCache.remove_pack(pack_id) -> None` | Removes every cached version, preview and partial for a pack. |
| `PackCache.clear() -> None` | Clears content and catalogue data. |
| `PackCache.cleanup_partials(*, max_age_seconds=604800) -> int` | Removes old partials and returns the count. Active transfers keep their partials. |
| `extras.apply_appearance(edopro_path, config_path, skin="", ..., *, highlight_turn=None, pack_cache=None, skin_pack=None, shared_pack=None) -> None` | Applies selected skin files to the game, using the specified verified packs or the legacy bundle. |

`Catalogue.source` is `LIVE` for a successful refresh, including a
conditional HTTP 304, `CACHED` for the last valid manifest after a failed
refresh, or `BUNDLED` when there is no valid cached copy. Show
`Catalogue.detail` when present. The initial bundled snapshot is empty while
asset redistribution is under review.

`PackStatus.state` is `NOT_DOWNLOADED`, `DOWNLOADING`, `DOWNLOADED`,
`UPDATE_AVAILABLE` (another version or digest is cached) or
`INCOMPATIBLE`. The cache retains previous verified versions until removal
or clearing. `Progress(bytes_done, total_bytes, bytes_per_second)` may
have an unknown total for previews. `cancel` takes a `threading.Event`.
Use the failure reason on `DownloadError` and catch `CacheBusyError` and
`OSError` for concurrent use and filesystem failures.

## From skin selection to applied

1. Refresh the catalogue, show its source and list `kind="skin"`. Display
   the pack's author, licence, attribution and source link as text. Request
   a preview when needed and keep its placeholder visible during that fetch.
2. When the player selects an uncached, compatible skin, render **Not
   downloaded**. Resolve `catalogue.manifest.get("shared")` for the shared
   fonts package and inspect both packs' states. If the dependency is
   missing or incompatible, show that reason and disable the download/apply
   action. Render **Update available** if a different version is cached,
   **Downloaded** if the exact version is cached, or **Downloading** if
   another instance is already fetching the pack.
3. On **Download and apply**, capture the installation, config path and
   highlight choice. In the worker, call `download_pack` for each required
   archive, using the selected manifest entries, current app version,
   progress callback and one cancellation event. Calling it for an already
   cached entry verifies and reuses that entry. Render **Downloading
   (skin or shared files)** with bytes, total and rate, **Cancel** while
   downloading, and **Verifying** while the worker checks the complete file.
4. On cancellation, render **Cancelled** and offer **Resume**. Refresh the
   underlying cache state. On a transient failure, render **Download
   interrupted** with **Retry**. Both keep the partial when available. Render
   **Pack unavailable** for `NOT_FOUND`, **Insufficient disk space** for
   `DISK_SPACE`, **Verification failed** for `INTEGRITY`, or the service
   or filesystem error for other failures. On `CacheBusyError`, show that
   another operation owns the cache and offer another attempt. Keep the
   selection and use a fresh event for retries.
5. After both downloads verify, render **Downloaded** and then
   **Applying**. With the game closed, run
   `apply_appearance(edopro_path, cfg.config_path, skin=pack.title,`
   `highlight_turn=chosen_highlight, pack_cache=cache, skin_pack=pack,`
   `shared_pack=shared)` on the worker. For skins outside the four Monokai
   presets, leave `highlight_turn` at `None`. Disable cancellation during
   game writes. For a later highlight-only toggle on one of the original
   skins, pass `skin`, `highlight_turn` and the cache without an exact
   `skin_pack` so the installer edits just the active skin's turn colours.
6. Render **Applied** only when `apply_appearance` returns successfully.
   On failure, render **Could not apply** with the error and recheck the
   cache state. Cached packs can be reused for another apply attempt. The
   existing `apply_appearance(..., restore=True)` restores the previous
   appearance settings even if the pack cache was cleared.

## Manifest v1

The UTF-8 JSON object contains the positive integer `schema_version` and
an array of `packs`. Each pack has the fields in this example. IDs are
unique lowercase ASCII letters, digits and hyphens. Release versions use
`major.minor.patch`. URLs are absolute HTTP(S) links. Sizes are positive
byte counts and digests are lowercase hexadecimal SHA-256. Each pack has
at least one preview. Preview `size_bytes` is optional and the fetched
image is capped at 1 MiB. A pack may specify `"withheld": true` to hide it
pending licence review.

Current kinds are `skin`, `wallpaper`, `sound-effects`, `music`, `sleeves`
and `covers`. A current manifest with missing licence or attribution,
unknown fields, unknown kinds or invalid known fields fails validation.
For higher schema versions, readers ignore extra fields and skip unknown
kinds while validating the known entries.

```json
{
  "schema_version": 1,
  "packs": [
    {
      "id": "blue-grid",
      "kind": "skin",
      "title": "Blue Grid",
      "description": "A blue grid theme for the deck editor and duel field.",
      "author": "Example Artist",
      "license": "CC0-1.0",
      "attribution": "Blue Grid by Example Artist.",
      "source_url": "https://example.org/blue-grid",
      "version": "1.0.0",
      "download_url": "https://example.org/releases/blue-grid.1.0.0.zip",
      "size_bytes": 285901,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "previews": [
        {
          "url": "https://example.org/releases/blue-grid.1.0.0-1.png",
          "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        }
      ],
      "min_app_version": "5.5.0"
    }
  ]
}
```

## Staging a release

Use `packs.json` as the publisher's input metadata. It is an array of
entries with the editorial fields shown above. Use `preview_files` (a
list of filenames relative to the preview directory) in place of
`previews`. Supply a subfolder named `<id>` or a ZIP named `<id>.zip` for each entry. The tool
derives URLs, sizes and hashes from the staged assets. Skin ZIPs contain
`skin/Monokai <title>/skin.xml` and game-relative assets. The shared
package uses ID `shared` and supplies the two Inter fonts. Include
`fonts/Inter-OFL.txt` in future shared archives. The original ZIP uses
the small licence file from `assets/appearance/`, which packaging needs
to retain if it removes the ZIPs.

```powershell
$py = 'python'
& $py tools/build_content_release.py assets/appearance --preview-root assets/window/gallery --out build/content-proof --draft
```

The command generates [the five-entry review manifest](content-release-draft.json)
from the repository's original ZIPs and gallery previews. The existing
asset notes record unverified redistribution rights for the skins and
shared sleeve images. The local draft retains that information and emits
no upload commands. Its release URLs are proposed locations.

For reviewed content, choose a fresh `--tag`, leave off `--draft`, and
review the printed `gh release` commands. Use
`--base-manifest path/to/previous/manifest.json` to keep unchanged entries
and their original asset URLs while staging only new or updated packs.
The tool performs no network requests. A mixed release skips entries marked
`withheld: true` and their assets. Use `--draft` to stage those locally for review.

## Building library packs from folders

Make a release folder, for example `my-release/`. Put a small preview there and
create `packs.json`. This example describes a music pack. Copy the entry for each
additional pack, give it its own ID, kind, title and preview, and fill in the
creator's actual credit and licence information.

```json
[{
  "id": "practice-music", "kind": "music", "title": "Practice music",
  "description": "Two original practice cues.", "author": "Example Artist",
  "license": "CC0-1.0", "attribution": "Practice music by Example Artist.",
  "source_url": "https://example.org/practice-music",
  "version": "1.0.0", "min_app_version": "5.5.0",
  "preview_files": ["preview.png"], "withheld": true
}]
```

The pack folder's **contents** become the ZIP root. Put the following
`pack.json` and exactly the files it lists in each example folder.

| Kind and example folder | Complete example `pack.json` |
|---|---|
| `music`, `practice-music/` | `{"items":[{"file":"forbidden/Practice.wav","title":"Practice duel","scenes":["deck","duel"]},{"file":"forbidden/Victory.ogg","scenes":["win"]},{"file":"forbidden/cover.png"}]}` |
| `covers`, `practice-covers/` | `{"items":[{"file":"forbidden/cover.jpg"}]}` |
| `wallpaper`, `practice-walls/` | `{"items":[{"file":"images/Dawn.png","title":"Dawn over the water"}]}` |
| `sleeves`, `practice-sleeves/` | `{"items":[{"file":"images/Blue.jpg","name":"Blue ink","tags":["blue","ink"]}]}` |

For the music example place `Practice.wav`, `Victory.ogg` and `cover.png` in
`my-release/practice-music/forbidden/`. Use game IDs from
`assets/music/catalog.json`. A second game gets its own folder and rows.
Each recording names one or more of `menu`, `deck`, `duel`, `advantage`,
`disadvantage`, `win`, `lose`. A missing title defaults to the filename stem.
Use FLAC, Ogg Vorbis, MP3 or WAV. Each recorded game may have one optional
`cover.png`, `cover.jpg` or `cover.jpeg`. A covers-only pack follows that same
naming rule and can supply several games. Sleeve tags may be an empty array.

For a skin called Grid, create `my-release/grid/skin/Monokai Grid/skin.xml`
and put its PNG/JPEG assets under that skin folder. Use ID `grid`, kind `skin`
and title `Grid` in `packs.json`. The existing skin format also accepts the
bounded `duplicates.json` alias map. The reserved `shared` dependency carries
the Inter fonts and legacy sound and sleeve assets.

The Sounds page can download and read a regular `sound-effects` pack using
the [sound-set format](sounds.md#sound-effects-pack-format). The staging tool
currently rejects this kind except for the reserved `shared` appearance
dependency. Regular sound packs therefore need a separate publishing route.

```powershell
& $py tools/build_content_release.py my-release --out reviewed-draft --draft
# After review, set withheld to false on the entries chosen for publication.
& $py tools/build_content_release.py my-release --tag content-v2 --out ready-to-upload
```

The four library layouts accept 1–256 declared files in stored or deflated
ZIPs. Limits are 512 MiB expanded per pack, 128 MiB per recording, 16 MiB per
image and 256 KiB for UTF-8 `pack.json`. Static PNG/JPEG images may contain up
to 12 million pixels, with at most 8,192 pixels per side. Titles, names, and
each of up to 24 tags may contain 1–128 printable characters. Filenames start
with an ASCII letter or digit and have up to 80 characters including the
extension, using ASCII letters, digits, spaces, dots, underscores or hyphens.
The reader rejects extra members, unsafe or case-colliding paths, links,
device names, encrypted members, ZIP64 trailers, ZIP comments and unknown
metadata fields. The publisher runs the installer's layout and media checks.

## Page actions and library reads

Initialize Qt on the main thread. Use the existing background worker event
loop for the whole action, including catalogue reads, SHA-256 verification,
image decoding, library writes and removal. Send progress and phase changes
back through queued signals. Capture the config and selection before starting.

```python
from threading import Event
from content.cache import PackCache
from content.libraries import installed_origins, make_available, remove_pack

cache = PackCache.for_config(cfg)
# status comes from Catalogue.list_packs(...), version is the running app version.
cancel = Event()
changes = await make_available(
    cache, status, app_version=version, progress=on_bytes, phase=on_phase, cancel=cancel
)
# A separate Remove action on a worker:
changes = remove_pack(cache, status)
```

Use the selected pack's `author`, `license`, `attribution` and `source_url`
in **every** state, including failure and incompatibility. For the offline
library detail view read those same fields plus `id`, `version` and archive
`sha256` from the entry's `origin`. Personal items can have no origin.

| Kind | Call and result for the page |
|---|---|
| Music | Run `make_available` above. Browse through `music_library.tracks(game, scene, root=music_root)` or `LibraryView`. One recording can appear in several scenes. `use_track` and playlist actions remain explicit player choices. |
| Covers | Run `make_available`. Use `music.packs.cover_path(game_id, music_root, size="grid")`. It prefers the latest installed matching cover, falls back to the bundled size, and returns `None` for a placeholder. `music.packs.pack_media(music_root)` supplies the credited entries for recordings and covers. |
| Wallpaper | Run `make_available`. Enumerate `ThemeLibrary(theme_root).wallpapers()`. Select with `replace(theme, wallpaper="image", image=row.digest)` and use the existing theme preview and save APIs. A saved theme retains its photo after pack removal. |
| Sleeves | Run `make_available`. Read `sleeves.Library(sleeve_root).entries()` for named, tagged and credited entries. Pass a selected entry ID to `sleeves.apply` when the player chooses a slot. |
| Skin | Run `make_available` for the selected skin and its legacy `shared` dependency. This makes the verified archives available in the cache. Then use the existing explicit Apply/Restore calls described above for the selected installation. |
| Sound effects | The Sounds page uses its own [download flow](../studio/pages/sounds_collections.py) to verify an archive and read a sound set. Applying cues is a separate action. The reserved `shared` dependency uses the skin flow. |

The three library roots above are `cache.root.parent / "soundtracks"`,
`"themes"` and `"sleeves"` respectively, all siblings of `packs/`.
`Changes.added`, `.removed` and `.retained` identify sleeve entries by their
stable library ID and other media by archive member. Replacing an edition
may return both the retired and replacement member. Keep the selected
manifest entry for credit display when a pack contributes several media.

For all four library kinds, render **Not downloaded**, **Downloading** (bytes,
total, rate, Cancel), **Downloaded** (cached), **Verifying**, **Installing**,
then **Available in library** when the action succeeds. On page load compare
`installed_origins(cache, status.pack.id)` with the selected version and
digest. An installed edition remains usable after cache eviction. Show
**Update available** when another edition is installed or cached. Honour
**Incompatible** even if an older edition remains usable. The phase callback
reports `downloading` (includes a cache hit), `verifying`, and `installing`.
Disable Cancel during installation. Skin and shared assets stop at
**Downloaded** before the separate **Applying / Applied** states.

On cancellation offer **Resume** with a fresh event. For a transfer failure
use the download errors above. Show **Invalid pack** on layout/media
`ValueError`, **Busy / Retry** for a lock conflict, and the filesystem message
for storage failures. Keep the previous library contents visible on a failed
validation. Refresh the catalogue state and installed origins after each action.
If the catalogue drops a pack, pass its saved ID to `remove_pack(cache, pack_id)`.
Render **Removing** during `remove_pack`, then refresh the library and cache.
Show the retained items when edits or sleeve assignments kept a player's copy.
Removal never uninstalls an active EDOPro skin or playlist. Use their own
Restore actions if the player also wants to change the game.

### Carry sleeve choices across game appearance changes

`extras.apply_appearance` reapplies saved sleeve assignments after skin apply
and restore. An explicit legacy sleeve selection takes priority for its slot
in that operation. Theme operations take a game path and theme ID; the page
owns the active sleeve library and config path. Group the calls on one worker
for install, switch back, undo, remove or recovery that changes the active skin:

```python
from pathlib import Path
from state_lock import appearance_lock
import sleeves
import themes

with appearance_lock(game):
    themes.install_theme(game, theme_id, theme, image=theme_library.image(theme))
    sleeves.reapply(sleeve_library, game, Path(cfg.config_path))
```

For a failure in the second call, report that the theme change succeeded and
offer to retry the sleeve operation. The same installation lock covers sleeve
apply/reapply/undo, skin operations, theme changes, game music settings and
publication of curated textures. Hold it only during synchronous game work.
