# Contributing to EDOPro HD Cards Downloader

## What this is
This portable desktop app finds EDOPro installations and downloads missing HD card and field artwork. Players can also preview skins, sleeves, audio and tournament decks before applying them to a selected installation.

**Release packaging requirements must be preserved.** Publish **exactly one asset per platform** — no checksum files, no launchers — so the Releases page reads simply "Windows, macOS, Linux":
- `EDOPal-Windows-vVERSION.zip`
- `EDOPal-macOS-vVERSION.zip`
- `EDOPal-Linux-vVERSION.zip`

**The macOS zip contains only the app. The Windows and Linux zips contain the app and a platform-specific `ReadMe.txt`.** That is the whole promise of the download — unzip, double-click, done. `build.yml` fails the build if a zip ever grows a third entry, loses either file, or ships a non-executable app. Nothing is installed and no support folder is created beside the download; small settings, retry caches, and optional reports use the normal per-user application-data directory for the platform.

**The Windows build must stay `--windowed`, and `build.yml` checks the PE subsystem byte to prove it.** A console build shows a stray black console window behind the app on every double-click. Do **not** try to fix that at runtime by hiding the console: that was tried and it does not hold. The console Explorer creates for a double-click is not the same object as the pseudo-console a terminal hands a child process, so the fix cannot be verified from any terminal — it looked like it worked in testing and still shipped the bug. Not creating a console at all is the only version that cannot regress. Output still reaches an inherited pipe or terminal, so `--health-check` and the CI smoke test are unaffected. On macOS `--windowed` is what *produces* the `.app` bundle, so it is required there too (see the macOS packaging rule above).

Related: `should_pause_before_exit()` requires an interactive `sys.stdin`. A windowed double-click has no console, so pausing for Enter there would hang the app on input nobody can see.

**Windows and Linux ship a bare executable; macOS ships a `.app` bundle.** A zip preserves the executable bit (a browser download does not — mode 644 caused the "you do not have appropriate access privileges" bug in v5.0.0), so a raw binary double-clicks fine on Windows and Linux. **macOS is different: Finder runs a bare Unix executable by opening Terminal**, so v5.2.0 put a blue Terminal window behind the app on every launch. A `.app` bundle is the only fix — verified locally, bare executable opens Terminal, bundle does not. The bundle is still *one icon* in Finder, so the download remains a single app, and it is not a launcher script: the bundle's executable is the same one-file PyInstaller binary. `build.yml` fails the build if the macOS zip does not contain a real bundle (`Contents/Info.plist`). Keep `.zip` (not `.7z`) for native unzip support.

**Build with PyInstaller `--onefile`, never `--onedir`.** Onedir is faster to start (0.3 s vs 1.9 s measured on the owner's machine, 23.6 MB binary) but spreads the app over ~1,000 files in an `_internal/` folder, which forced a launcher script to exist purely to hide it. That launcher was the single biggest source of bugs in the project's history (see below). One file, ~2 s to start, no launcher: that trade is deliberate — do not reverse it to shave a second off a tool people run occasionally.

**There is no launcher, and adding one back is a regression.** `EDOPro-HD-Sync.bat` / `.command` / `.sh` were deleted in v5.2.0. They existed to install the onedir app into a support folder and self-update it, and they broke repeatedly: the macOS executable-bit bug (v5.0.0), the Windows checksum bug, and finally a Windows launcher left copying a single `.exe` after the app became a folder — which created `app\EDOPro-HD-Sync.exe.new` inside a directory that was never created, so **every Windows install of v5.1.0/v5.1.1 failed outright**. Three launchers in three languages could not be kept in step with the packaging. Updates are now just a notice in the app pointing at the Releases page.

## Running / developing locally
```bash
pip install -r requirements.txt
python main.py                  # normal sync
python main.py --dry-run        # preview only
python main.py --force          # re-download everything
python main.py --generate-config  # write a default config.json
python main.py --health-check   # quick offline sanity check
```

## Project structure
```text
main.py, card_databases.py           # database scanning and artwork sync
config.py, installation_picker.py    # preferences, discovery and folder selection
gui.py, appearance_gallery.py        # desktop home, progress and appearance previews
extras.py, state_lock.py             # local appearance changes, undo and write coordination
music_library.py, music_import.py    # catalogue, personal files and per-game playlists
music_window.py, music_player.py     # soundtrack browsing, playback and seeking
tournament_window.py                 # tournament browser and artwork hand-off
tournament_decks.py, goat_versions.py # Format Library imports and historical card versions
network_tls.py                       # certificate settings shared by remote services
release_assets/ReadMe-*.txt          # platform instructions
.github/workflows/build.yml          # release packaging and smoke checks
```

## Desktop interfaces

A plain packaged launch opens the Studio shell. Use `python main.py --gui` from source or `--legacy-gui` for the original Tk interface during the page migration. Power flags retain their console flow. The [Studio developer guide](studio/README.md) covers page registration, the shared task runner, component and token usage, headless screenshots and tests.

Both desktop interfaces share artwork choices and summary models in `artwork_flow.py`. Each sync for imported decks gets its own configuration and cancellation event. The backend stays independent of Qt.


## Soundtrack covers

Soundtrack entries in `assets/music/catalog.json` identify their LaunchBox front-cover source, original resolution and region. Use the release year of the edition pictured. Konami lists the contemporary game bundles in its [North American](https://www.db.yugioh-card.com/yugiohdb/card_list.action?request_locale=en) and [European](https://www.db.yugioh-card.com/yugiohdb/card_list.action?request_locale=de) records. Personal recordings belong in the user's soundtrack library.

## Deck-first sync

`--my-decks` (or the window's tick-box) points the deck filter at `<edopro>/deck`, scanned recursively for `.ydk`. On a fresh install (>2,000 cards indexed, <500 images on disk) the console flow asks "Quick start: only sync the cards in your N deck(s)?" before committing to a full ~13,000-card download. The packaged options window checks this automatically and recommends deck-first mode when it finds decks on a mostly empty install.

## Architecture

### Card database scanning
EDOPro stores cards in SQLite `.cdb` files. The tool scans:
- `cards.cdb` at the EDOPro root (often an empty placeholder - skip if 0 bytes)
- every `*.cdb` in `expansions/`
- every `*.delta.cdb` under `repositories/`

From these it builds two maps:
- `id_to_name` - every card ID -> name
- `name_to_official` - name -> all official Konami IDs seen for that name (only IDs < 100,000,000)

### Download waterfall (in order, stops at first success)
1. **Manual override** - `BUILTIN_MANUAL_MAP` in `config.py` plus the user's optional `manual_map.json`. The built-in map pins multi-art suffix cards (e.g. two "Ring of Destruction (Pre-Errata)" variants) to distinct official artworks, since suffix stripping alone would give them all the same image.
2. **Direct ID on YGOProDeck** - tries `https://images.ygoprodeck.com/images/cards/{card_id}.jpg` for **every** card. YGOProDeck hosts official, Rush Duel, and anime/custom cards under the same IDs EDOPro uses (Rush coverage is partial: older sets are complete, the newest sets lag behind — `--recheck-missing` picks them up as they're added).
3. **Name-matched HD** - for GOAT/Pre-Errata suffix cards only. Strips the suffix, finds the base card's official IDs, and tries those on YGOProDeck.
4. **Pre-Errata offset fallback** - if a Pre-Errata suffix matched but the base card was missing from the scanned DBs, try `card_id - 10` on YGOProDeck.
5. **ProjectIgnis backup** - `https://pics.projectignis.org:2096/pics/{id}.jpg` — the official image server EDOPro itself downloads from (URL recovered from nixpkgs' from-source build; the `:2096` port is required). It has everything, including the newest Rush sets YGOProDeck lags on. **It's a small community server: it must stay LAST in the waterfall** so the YGOProDeck CDN absorbs the bulk. Field art has the same shape: `sources["field_backup"]` = `.../pics/field/{id}.png` after YGOProDeck's cropped .jpg. (The old GitHub `ProjectIgnis/Images` repo is deleted — never point at it.)

### Card ID rules
- IDs < 100,000,000 -> official Konami cards (these also count toward the "Official" failure bucket)
- IDs >= 100,000,000 -> Rush Duel (160M range, tracked via the DB filename) and anime/custom cards. All still get the direct YGOProDeck attempt.

### Field Spell playmat art
Field Spells (datas.type has both `0x2` SPELL and `0x80000` FIELD bits — `FIELD_SPELL_TYPE`) also get their cropped playmat artwork downloaded into `pics/field/{id}.jpg` from `https://images.ygoprodeck.com/images/cards_cropped/{id}.jpg`. EDOPro reads `.png` or `.jpg` there. Runs after the card sync, is incremental, has its own failure cache (`failed_fields.json`), is covered by `--repair`, and can be disabled with `--no-field-art` (or `"field_art": false` in config.json).

### GOAT / Pre-Errata artwork: sharpness vs. correct wording
`build_download_candidates()` tries manual overrides, base-card name matches,
and the Pre-Errata offset before the era entry's own CDN image. A successful
CDN request alone does not prove HD quality: Sinister Serpent (Pre-Errata)
exists there at 428x614 while the base card is 813x1185.

The launcher checkbox "HD GOAT / Pre-Errata artwork" defaults on. Turning it
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
50 async workers drain a shared `asyncio.Queue`. Each worker loops until the queue is empty. This keeps a steady number of requests in flight without spawning tens of thousands of coroutines.

### SSL certificates and proxies
PyInstaller bundles do not include system SSL certs automatically. The app uses `certifi` and passes it to `aiohttp.TCPConnector` via `ssl.create_default_context(cafile=certifi.where())`. The build uses `--collect-data certifi` to include the cert bundle. **Do not remove this - it will silently break all downloads.** `_create_http_session()` also uses `trust_env=True`, so standard `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` settings work. `EDOPRO_CA_BUNDLE` can add a private PEM CA bundle for managed networks.

### Optional textures
Texture packs are separate GitHub releases. The app only lists or downloads a pack when its release notes contain explicit `Attribution:` and `License:` lines. Never add texture assets to a release without confirming their creator and redistribution terms.

## Release process
Build from the repository root with the runtime pins in `requirements.txt`, PyInstaller 6.20.0 and Pillow 13.0.0. CI and local builds use the same commands.

```sh
python -m tools.release build
python -m tools.release verify
```

The build freezes `tools/release.spec`, inspects the embedded payload and writes the platform zip to `dist/`. Verification extracts it into a fresh directory, creates a disposable game and config, then runs both offline flags from the extracted program. Each process has a 60-second deadline. Use `verify --repeat 6` for first-launch and five warm GUI timings. On a headless Windows host set `QT_QPA_PLATFORM=offscreen`. Windows CI uses `windows`, macOS uses `cocoa` for each architecture, and Linux runs separately on Xvfb/xcb and headless Weston/Wayland.

Set `APP_FILE` at the top of `.github/workflows/build.yml` to change all executable, bundle and download filenames. Local builds default to `config.APP_NAME`. ReadMe templates and Finder metadata take the display name from that constant. `assets/app-icon.png` is the sole icon source. Replacing it regenerates the window PNG, multi-size ICO and Finder ICNS during the next build.

The release includes Qt Core, Gui, Widgets and Svg, platform plugins from `tools/qt_payload.py`, the audio decoder, CFFI and certifi. PNG is built into QtGui. JPEG, ICO and WebP use image plugins. Studio renders its SVG glyphs directly with QtSvg. The Tk interface is available with `python main.py --legacy-gui` from source. Skin installation prefers the verified per-user pack cache. Only `shared.zip` ships for the Inter game fonts, built-in sleeves and Thonk effects. The Inter licence, attribution, UI font, appearance previews and music catalogue/covers also ship.

The executable must be smaller than **80 MB on Windows, 105 MB on macOS and 120 MB on Linux** (decimal). The packaging audit measured a 71.32 MB Windows backend-plus-widget zip and estimated 83 MB for universal macOS and 96 MB for Linux. The margins cover the full Studio window, SVG, platform libraries and wheel variation. The verifier checks the actual embedded names for the four skin archives and unwanted Qt modules independently of size.

macOS builds need the checksum-verified python.org universal2 interpreter, dual-architecture native dependencies and a 14.4 deployment target. The workflow verifies both slices of the selected Qt binaries before freezing, sets the bundle metadata, signs ad hoc, archives with `ditto` and verifies the extracted signature. The macOS ReadMe is inside the app. Linux uses Ubuntu 22.04 x86_64. Install the Qt host libraries listed in the release workflow before freezing so dependency analysis can resolve the X11 and Wayland clients.

A `v*` tag push whose tag matches `VERSION` builds and publishes all three verified zips in one release job. Pushing a `release-test/**` branch runs the build matrix. To dispatch against a pushed work branch and inspect its results with GitHub CLI:

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

## Release notes
Release notes are auto-generated by GitHub (`generate_release_notes: true` in build.yml) from the commits since the previous tag, so keep commit subjects short and user-readable. The README deliberately has no auto-updating "What's New" panels — the owner removed them; don't re-add them.

## Finding the EDOPro folder
The app locates the folder itself, so the user never has to know where EDOPro lives. `main.detect_edopro_folder()` runs once at startup (skipped entirely when `--edopro-path` was passed — an explicit answer is never second-guessed) and calls `config.find_edopro_folder()`, which checks, in order:

1. the caller's guesses — the remembered `edopro_path` from `config.json`, or the folder the app is sitting in
2. `EDOPRO_FOLDER_NAMES` joined onto each `_search_parents()` entry (home, Games, Desktop, Downloads, Documents, plus `%LOCALAPPDATA%\Programs`, `/Applications`, `~/.local/share`, …) — pure `isdir` checks, instant
3. one level inside each of those parents, for people who renamed the folder

Candidates are **scored, not first-match**: a folder with card databases (2) always beats one that merely looks like EDOPro (1), so a fresh install with no `.cdb` files yet never shadows a real one. The scan is capped by `_SEARCH_TIME_BUDGET_SECONDS` so startup can never appear to hang.

Two folder predicates exist on purpose and must not be merged:
- `folder_has_card_databases()` — **mirrors `main.get_db_files()` exactly.** If that returns nothing, this must be False, or the app would "find" a folder it then can't sync. `tests/test_folder_detection.py` asserts the two agree on every shape (root `cards.cdb`, `expansions/*.cdb`, `repositories/**/*.delta.cdb`, empty dirs, an `expansions/` with no `.cdb`). Keep that test passing whenever either function changes.
- `looks_like_edopro_folder()` — deliberately looser (accepts `EDOPro.exe` alone), because a freshly installed EDOPro that has not downloaded its databases yet is still the right folder.

**The user confirms the folder before anything downloads.** In the window, the OPTIONS screen's top card shows the folder with a **Change…** button, and pressing Start *is* the confirmation (`gui._render_folder` also disables Start when no folder is set). In the console flow, `main.confirm_edopro_folder()` asks "Use this folder?" defaulting to yes, and declining hands over to `prompt_for_edopro_path()`. The console prompt is gated on `cfg.folder_detected and cfg.interactive_prompts and not cfg.quiet`, so the window never double-asks and scripted runs never block.

## Updates
There is no self-updating machinery. `check_for_update()` compares `VERSION` against the latest GitHub tag and shows a notice pointing at the Releases page; the user downloads the new zip and replaces the one file. This replaced a launcher-driven auto-updater that broke the Windows install twice.

## Output files
- `pics/{id}.jpg` - downloaded card images (in the EDOPro folder)
- `pics/field/{id}.jpg` - Field Spell playmat artwork
- `failed_cards.json` / `failed_fields.json` - 14-day caches of definitively-missing art (in the packaged app's per-user data directory)
- `config.json` - user config (beside the script from source; in the packaged app's per-user data directory; override with `--config PATH`)
- `sync-report-*.txt` - optional timestamped reports beside `config.json`
- `manual_map.json` - optional per-card ID overrides (user-created, not tracked in git)
- `manual_map.example.json` - copyable example showing the override format
