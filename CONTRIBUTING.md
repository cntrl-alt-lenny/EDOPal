# Contributing to EDOPal

EDOPal has a Qt 6 window that calls the same artwork backend as the console sync. Feature engines can be used independently of the window. See the [documentation index](docs/index.md) for their public interfaces.

## Run from source

Use the project's Python environment. From a fresh developer environment, install the pinned runtime dependencies from `requirements.txt`. For tests and screenshots, also install the developer tools on the last line.

```powershell
$py = 'python' # Set to the full path to your project's Python if needed.
& $py -m pip install -r requirements.txt
& $py -m pip install pytest==9.2.0 pytest-qt==4.5.0 ruff==0.15.15 Pillow==13.0.0
```

Work against a disposable installation and pass a config path inside your checkout. The demo maker refuses to overwrite an existing game folder. Use a fresh demo directory each time.

```powershell
& $py -c "from pathlib import Path; from tools.studio_screenshots import create_demo; create_demo(Path('build/demo').resolve())"
& $py main.py --gui --config build/demo/config.json --edopro-path build/demo/Games/ProjectIgnis
```

**Check artwork coverage** reads local artwork. **Sync HD cards** in the source app makes real image requests. For offline review, use the screenshot renderer below. The normal packaged launch opens the Qt window. A plain source launch runs the console flow and `python main.py --legacy-gui` opens the original Tk window. Run `python main.py --help` for power flags.

## Source layout

| Area | Files and packages |
| --- | --- |
| Qt shell, sidebar pages and widgets | [studio/](studio/README.md) |
| Artwork, coverage, database discovery and CLI | [main.py](main.py), [card_databases.py](card_databases.py), [artwork_flow.py](artwork_flow.py), [artwork_coverage.py](artwork_coverage.py) |
| Preferences, installation selection and shared helpers | [config.py](config.py), [installation_picker.py](installation_picker.py), [state_lock.py](state_lock.py), [network_tls.py](network_tls.py) |
| Appearance and audio engines | [themes](themes/README.md), [sleeves](sleeves/__init__.py), [sounds](docs/sounds.md), [music](music/__init__.py), [music_library.py](music_library.py), [music_import.py](music_import.py), [music_player.py](music_player.py), [extras.py](extras.py) |
| Tournament imports and personal decks | [tournament_decks.py](tournament_decks.py), [goat_versions.py](goat_versions.py), [decks](decks/__init__.py) |
| Content, transfer, health and replays | [content](docs/content-packs.md), [backup](docs/backup.md), [doctor and replays](docs/doctor-replays.md) |
| Legacy Tkinter UI, available from source | [gui.py](gui.py), [appearance_gallery.py](appearance_gallery.py), [desktop_dialogs.py](desktop_dialogs.py), [music_window.py](music_window.py), [tournament_window.py](tournament_window.py) |

Both interfaces use `artwork_flow.py` for artwork choices and summaries. Qt uses the shared deck-contents view for tournament previews and saved decks. Each imported-deck sync gets its own job configuration and cancellation event.

## Pages, tests and screenshots

Use the [Qt page guide](studio/README.md#add-or-replace-a-page) for page registration, shared widgets and background tasks. Run network requests, scans, decoding and installation changes on a worker. Create widgets and pixmaps on the UI thread.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q
ruff check .
ruff format --check .
python tools/studio_screenshots.py --out build/edopal-shots --size 960x600 --size 1280x800
```

The renderer uses synthetic installations, simulated downloads and the real Qt pages. It writes one directory per size. Use `--page <sidebar-id>` for a page’s scenarios, `--page shell` for HD Cards and Settings, or `--page all` (the default) for the overview. Run with `--help` to list the IDs. Check 960×600 for compact banners and sidebar scrolling, and follow the release recipe for the curated 1280×800 images. [Release screenshot recipe](docs/screenshots/README.md)

## Safe game writes

- Stage and validate replacements before publishing them. Replace files atomically and preserve originals for undo when touching existing player files. New deck and replay copies use unused names and publish without overwriting an existing file.
- For a recoverable change, write a journal with the expected file states and saved originals before publishing changes. Validate every target again during undo or recovery. Stop on an unexpected edit so the player can keep it.
- Use the lock that owns the write. Appearance, generated themes, sleeves, effects and audio settings share the installation-level `.edopro-appearance.lock`. Restore has its own game and app-data locks, and music uses scene locks for playlists. Keep the lock files as stable identities. Close the game for appearance, audio and other guarded writes. An unknown process state also stops guarded writes. Ask the player to close EDOPro and retry.
- Keep installation locks out of network waits. Qt artwork jobs keep a restore point per installation through `cards_undo.py`. CLI artwork jobs use atomic replacement without this journal. [Write locations and recovery](docs/safety.md)

## Releases

### Packaging contract

Publish exactly one portable ZIP per platform.

- `EDOPal-Windows-vVERSION.zip`
- `EDOPal-macOS-vVERSION.zip`
- `EDOPal-Linux-vVERSION.zip`

Windows and Linux archive the executable and a platform ReadMe. The macOS ZIP contains a single `.app` with the ReadMe in `Contents/Resources/`. Keep the PyInstaller builds one-file and use windowed mode on Windows and macOS. Updates remain manual downloads from GitHub Releases. See the packaging history below for the launcher and startup issues these rules address.

### Build and verify

Build from the repository root with the runtime pins in `requirements.txt`, PyInstaller 6.20.0 and Pillow 13.0.0. CI and local builds use the same commands.

```sh
python -m tools.release build
python -m tools.release verify
```

The build freezes `tools/release.spec`, inspects the embedded payload and writes the platform zip to `dist/`. Verification extracts it into a fresh directory, creates a disposable game and config, then runs both offline flags from the extracted program. Each process has a 60-second deadline. Use `verify --repeat 6` for first-launch and five warm GUI timings. On a headless Windows host set `QT_QPA_PLATFORM=offscreen`. Windows CI uses `windows`, macOS uses `cocoa` for each architecture, and Linux runs separately on Xvfb/xcb and headless Weston/Wayland.

Set `APP_FILE` at the top of `.github/workflows/build.yml` to change all executable, bundle and download filenames. Local builds default to `config.APP_NAME`. ReadMe templates and Finder metadata take the display name from that constant. `assets/app-icon.png` is the sole icon source. Replacing it regenerates the window PNG, multi-size ICO and Finder ICNS during the next build. For the 16, 20 and 24 px ICO entries, the build can use `assets/app-icon-small.png` with widened face details. `assets/logo.svg` is the master vector and `assets/logo-mark.png` the transparent mark shown in the sidebar.

The release includes Qt Core, Gui, Widgets and Svg, platform plugins from `tools/qt_payload.py`, the audio decoder, CFFI and certifi. PNG is built into QtGui. JPEG, ICO and WebP use image plugins. EDOPal renders its SVG glyphs directly with QtSvg. The Tk interface is available with `python main.py --legacy-gui` from source. Skin installation prefers the verified per-user pack cache. Only `shared.zip` ships for the Inter game fonts, built-in sleeves and Thonk effects. The Inter licence, attribution, UI font, appearance previews and music catalogue/covers also ship.

The executable must be smaller than **80 MB on Windows, 105 MB on macOS and 120 MB on Linux** (decimal). The packaging audit measured a 71.32 MB Windows backend-plus-widget zip and estimated 83 MB for universal macOS and 96 MB for Linux. The margins cover the full EDOPal window, SVG, platform libraries and wheel variation. The verifier checks the actual embedded names for the four skin archives and unwanted Qt modules independently of size.

macOS builds need the checksum-verified python.org universal2 interpreter, dual-architecture native dependencies and a 14.4 deployment target. The workflow verifies both slices of the selected Qt binaries before freezing, sets the bundle metadata, signs ad hoc, archives with `ditto` and verifies the extracted signature. The macOS ReadMe is inside the app. Linux uses Ubuntu 22.04 x86_64. Install the Qt host libraries listed in the release workflow before freezing so dependency analysis can resolve the X11 and Wayland clients.

Pushing a `release-test/**` branch runs the build matrix. To dispatch against a pushed work branch and inspect its results with GitHub CLI:

```powershell
$branch = git branch --show-current
gh workflow run build.yml --ref $branch
gh run list --workflow build.yml --branch $branch --event workflow_dispatch --limit 5
$runId = Read-Host "ID of the dispatch shown above"
gh run watch $runId --exit-status
gh run view $runId
gh run view $runId --log-failed
gh run download $runId --dir dist/ci
```

Branch pushes and manual runs upload workflow artifacts. The publishing job requires a tag push and is skipped for every manual dispatch, even on a tag. The matrix uses `fail-fast: false` so all three platforms report their result.

### Publish

Push a `v*` tag whose version matches `VERSION` to publish all three verified ZIPs. Release notes are auto-generated by GitHub (`generate_release_notes: true` in build.yml) from the commits since the previous tag, so keep commit subjects short and user-readable. The README deliberately has no auto-updating "What's New" panels — the owner removed them; don't re-add them.

## History and rationale

### Portable packaging history

- The Windows console build opened a black console on every Explorer double-click. Hiding it at runtime appeared to work from a terminal but failed in Explorer, which creates a different console. The verifier checks the PE subsystem, and the packaged smoke probes check output through pipes. `should_pause_before_exit()` requires interactive standard input so a double-click never waits for invisible input.
- Finder opened a bare Unix binary in Terminal in v5.2.0. A windowed macOS build puts the same one-file binary inside a Finder app. ZIP preserves the Unix execute bit, which was lost in the v5.0.0 download. It also works with native extraction tools.
- A measured one-directory build started faster but required a support directory and launcher. The old `EDOPro-HD-Sync.bat`, `.command` and `.sh` launchers broke on executable permissions, Windows checksum checks and an invalid update destination. The stale copy step stopped every Windows install of v5.1.0 and v5.1.1. The launchers were removed in v5.2.0.

### Soundtrack covers

Soundtrack entries in `assets/music/catalog.json` identify their LaunchBox front-cover source, original resolution and region. Use the release year of the edition pictured. Konami lists the contemporary game bundles in its [North American](https://www.db.yugioh-card.com/yugiohdb/card_list.action?request_locale=en) and [European](https://www.db.yugioh-card.com/yugiohdb/card_list.action?request_locale=de) records. Personal recordings belong in the user's soundtrack library.

### Deck-first sync

`--my-decks` points the deck filter at `<edopro>/deck`, scanned recursively for `.ydk`. On a fresh install (>2,000 cards indexed, <500 images on disk) the console flow asks "Quick start: only sync the cards in your N deck(s)?" before committing to a full download. The original packaged Tk options window recommended deck-first mode when it found decks on a mostly empty install.

### Card database scanning
EDOPro stores cards in SQLite `.cdb` files. The tool scans:
- `cards.cdb` at the EDOPro root (often an empty placeholder - skip if 0 bytes)
- every `*.cdb` in `expansions/`
- every `*.delta.cdb` under `repositories/`
- `prerelease-*.cdb` and `release-*.cdb` directly under `repositories/delta-bagooska/`. Later release or delta data takes precedence over provisional definitions.

From these it builds two maps:
- `id_to_name` - every card ID -> name
- `name_to_official` - name -> all official Konami IDs seen for that name (only IDs < 100,000,000)

### Artwork sources and matching

The numbered list records the original fallback design. The GOAT and Pre-Errata policy below describes the later priority change for era cards.

1. **Manual override** - `BUILTIN_MANUAL_MAP` in `config.py` plus the user's optional `manual_map.json`. The built-in map pins multi-art suffix cards (e.g. two "Ring of Destruction (Pre-Errata)" variants) to distinct official artworks, since suffix stripping alone would give them all the same image.
2. **Direct ID on YGOProDeck** - tries `https://images.ygoprodeck.com/images/cards/{card_id}.jpg` for every queued card that reaches this step. YGOProDeck hosts official, Rush Duel and anime/custom cards under the same IDs EDOPro uses. Use `--recheck-missing` to retry cards as new sets are added.
3. **Name-matched HD** - for GOAT/Pre-Errata suffix cards only. Strips the suffix, finds the base card's official IDs, and tries those on YGOProDeck.
4. **Pre-Errata offset fallback** - if a Pre-Errata suffix matched but the base card was missing from the scanned DBs, try `card_id - 10` on YGOProDeck.
5. **Project Ignis backup** - `https://pics.projectignis.org:2096/pics/{id}.jpg` is the image server EDOPro itself downloads from. The URL was recovered from nixpkgs' from-source build and uses port `2096`. Keep the community server as the final default fallback so the YGOProDeck CDN absorbs the bulk. Field art tries `sources["field_backup"]` (`.../pics/field/{id}.png`) after YGOProDeck's cropped JPEG. The old GitHub `ProjectIgnis/Images` repository was deleted.

### Card ID rules
- IDs < 100,000,000 -> official Konami cards (these also count toward the "Official" failure bucket)
- IDs >= 100,000,000 -> Rush Duel (160M range, tracked via the DB filename) and anime/custom cards. All still get the direct YGOProDeck attempt.

### Field Spell playmat art
A separate field-art pass writes JPEG or PNG to `pics/field/`. `download_field_art()` tries the card's own cropped image from YGOProDeck and Project Ignis, then an eligible donor illustration. A donor can come from disk or either provider. If those fail and the card face is available, EDOPal makes a local crop using the applicable card layout. Derived crops can be upgraded when a published image becomes available. Disable the pass with `--no-field-art` or `"field_art": false`.

### GOAT / Pre-Errata artwork: sharpness vs. correct wording
`build_download_candidates()` tries manual overrides, base-card name matches,
and the Pre-Errata offset before the era entry's own CDN image. A successful
CDN request alone does not prove HD quality: Sinister Serpent (Pre-Errata)
exists there at 428x614 while the base card is 813x1185.

The artwork option "HD GOAT / Pre-Errata artwork" defaults on. Turning it
off sets `original_text=True`, preserving own-CDN/own-ProjectIgnis priority
for original wording; substitutes remain a fallback. CLI flags and the saved
`original_text` preference stay compatible.

`era_art_applied` stores the applied policy per EDOPro folder and card ID.
Untracked or changed era entries join the download queue even if their images
already exist. Downloads replace files atomically; failures preserve the old
image. Only successful card IDs are marked applied, so deck-only, cancelled,
and failed runs leave other cards pending. Dry runs never save this state.

### GOAT / Pre-Errata trick
Cards like "Dark Magician GOAT" have a custom DB ID but the same artwork as "Dark Magician". The suffix-stripping logic removes known suffixes (` GOAT`, ` (Pre-Errata)`, etc.) and looks up the base name in `name_to_official` to find the real Konami ID, then downloads that HD image. If a Pre-Errata card's base name is missing from the scanned DBs, its GOAT DB ID is usually the real passcode + 10, so the downloader tries `card_id - 10` before falling back to ProjectIgnis.

### Failure cache (failed_cards.json, failed_fields.json)
Cards that fail to download are remembered for 14 days so repeat runs skip them (`failed_cards.json` for card art, `failed_fields.json` for field art). **Only definitive misses are cached** — `_try_download` returns a `FetchResult` (`OK` / `MISSING` / `ERROR`), and a card is cached only when every source said `MISSING` (HTTP 404). Timeouts, connection errors, rate limits, and 5xx are `ERROR` (transient) and are retried on the next run — never cache them, or one bad Wi-Fi day would silence hundreds of cards for two weeks.

### Concurrency
By default, 50 async workers drain a shared `asyncio.Queue`. Each worker loops until the queue is empty. This keeps a steady number of requests in flight without spawning tens of thousands of coroutines.

### SSL certificates and proxies
PyInstaller bundles do not include system SSL certs automatically. The app uses `certifi` and passes it to `aiohttp.TCPConnector` via `ssl.create_default_context(cafile=certifi.where())`. The build uses `--collect-data certifi` to include the cert bundle. **Do not remove this - it will silently break all downloads.** `_create_http_session()` also uses `trust_env=True`, so standard `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` settings work. `EDOPRO_CA_BUNDLE` can add a private PEM CA bundle for managed networks.

### Optional textures
Texture packs are separate GitHub releases. The app only lists or downloads a pack when its release notes contain explicit `Attribution:` and `License:` lines. Never add texture assets to a release without confirming their creator and redistribution terms.

### Finding the EDOPro folder
The app locates the folder itself, so the user never has to know where EDOPro lives. `main.detect_edopro_folder()` runs once at startup (skipped entirely when `--edopro-path` was passed — an explicit answer is never second-guessed) and calls `config.find_edopro_folder()`, which checks, in order:

1. the caller's guesses — the remembered `edopro_path` from `config.json`, or the folder the app is sitting in
2. `EDOPRO_FOLDER_NAMES` joined onto each `_search_parents()` entry (home, Games, Desktop, Downloads, Documents, plus `%LOCALAPPDATA%\Programs`, `/Applications`, `~/.local/share`, …) — pure `isdir` checks, instant
3. one level inside each of those parents, for people who renamed the folder

Candidates are **scored, not first-match**: a folder with card databases (2) always beats one that merely looks like EDOPro (1), so a fresh install with no `.cdb` files yet never shadows a real one. The scan is capped by `_SEARCH_TIME_BUDGET_SECONDS` to bound the discovery scan.

Two folder predicates exist on purpose and must not be merged:
- `folder_has_card_databases()` — **mirrors `main.get_db_files()` exactly.** If that returns nothing, this must be False, or the app would "find" a folder it then can't sync. `tests/test_folder_detection.py` asserts the two agree on every shape (root `cards.cdb`, `expansions/*.cdb`, `repositories/**/*.delta.cdb`, empty dirs, an `expansions/` with no `.cdb`). Keep that test passing whenever either function changes.
- `looks_like_edopro_folder()` — deliberately looser (accepts `EDOPro.exe` alone), because a freshly installed EDOPro that has not downloaded its databases yet is still the right folder.

**The player reviews the installation before artwork sync.** The Qt installation bar shows the current selection and the **Change folder** action. In the original Tk interface, the OPTIONS screen's top card shows the folder with a **Change…** button, and pressing Start *is* the confirmation (`gui._render_folder` also disables Start when no folder is set). In the console flow, `main.confirm_edopro_folder()` asks "Use this folder?" defaulting to yes, and declining hands over to `prompt_for_edopro_path()`. The console prompt is gated on `cfg.folder_detected and cfg.interactive_prompts and not cfg.quiet`, so the window never double-asks and scripted runs never block.

### Updates
There is no self-updating machinery. `check_for_update()` compares `VERSION` against the latest GitHub tag and shows a notice pointing at the Releases page; the user downloads the new zip and replaces the one file. This replaced a launcher-driven auto-updater that broke the Windows install twice.

### Output files
- `pics/{id}.jpg` - downloaded card images (in the EDOPro folder)
- `pics/field/{id}.jpg` or `.png` - Field Spell playmat artwork
- `artwork-restore/` - the last changing Qt artwork sync per installation and config directory
- `failed_cards.json` / `failed_fields.json` - 14-day caches of definitively-missing art (in the packaged app's per-user data directory)
- `config.json` - user config (beside the script from source; in the packaged app's per-user data directory; override with `--config PATH`)
- `sync-report-*.txt` - optional timestamped reports beside `config.json`
- `manual_map.json` - optional per-card ID overrides (user-created, not tracked in git)
- `manual_map.example.json` - copyable example showing the override format
