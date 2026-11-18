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
`previews`. Put one `<id>.zip` in the input folder per entry. The tool
derives URLs, sizes and hashes from the staged assets. Skin ZIPs contain
`skin/Monokai <title>/skin.xml` and game-relative assets. The shared
package uses ID `shared` and supplies the two Inter fonts. Include
`fonts/Inter-OFL.txt` in future shared archives. The original ZIP uses
the small licence file from `assets/appearance/`, which packaging needs
to retain if it removes the ZIPs.

```powershell
$py = 'C:/Users/leona/Dev/edopro-agents/venv/Scripts/python.exe'
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
The tool performs no network requests.
