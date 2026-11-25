# EDOPal v6 — plan

> If you have EDOPro, get EDOPal. Open it, make the game look and sound better, press **Launch EDOPro**.

Fresh rewrite on branch `v6`. Old code is read-only reference under `refs/archive/*` (e.g. `git show refs/archive/dev/steamos:main.py`). Research behind every decision: `_reference/reports/*/REPORT*.md`.

## v6.0 scope

| Page | What it does | Not in v6.0 |
|---|---|---|
| **HD Cards** | One **Sync** button: fetch/verify card faces and field art (YGOPRODeck → Project Ignis fallback). GOAT / pre-errata matching. Notice when a game update adds cards: “12 new cards · 9 need HD art”. Undo last sync. | Manual pins UI, reports, repair wizard |
| **Themes** | Large live preview (Menu / Deck editor / Duel) + row of theme tiles; hover = preview, click = apply. Card-back strip always visible (You / Opponent). Small *Customize* drawer (accent, wallpaper, font). Ships **Abyss, Nebula, Moon**. | Theme maker, generated sleeves, 50-skin library |
| **Music** | One screen, no scrolling: game-cover grid → click previews → assign to **Main menu / Deck building / Duel / Victory / Defeat**. Packs download on demand. | Library manager, playlists |
| **Formats** | Format → events → decks → **Download deck**. **Install banlist** (era whitelist + limits) and **Copy host settings** for Goat, Edison, HAT, etc. Known gameplay gaps shown honestly. | Errata engine changes |
| Header | Game-folder picker, **Launch EDOPro** (correct on Linux/Steam/Proton, macOS, Windows), running-game detection. | |

## Architecture (Python 3.12 + PySide6 Widgets)

```
src/edopal/
  app.py              # entry; registers features explicitly
  ui/                 # shell, sidebar, design tokens (ported from archive studio/theme.py), widgets
  core/               # Qt-free: install discovery, launch, catalog, jobs, settings, safe writes + undo, packs
  features/<id>/      # one folder per page: register(core) -> FeatureSpec(id, label, icon, order, page factory)
  assets/             # logo, icons, fonts, small thumbnails only (<8 MB)
tests/  packaging/  docs/  .github/workflows/
```

- **New feature = one folder + one line in `app.py`.**
- Every write into the game folder goes through `core.storage`: lock → game-closed check → atomic write → undo journal.
- Big media (theme wallpapers, music) lives in a separate **`edopal-packs`** repo as versioned release assets with SHA-256s; the app downloads on demand.
- Packaging: PyInstaller onedir, PySide6-Essentials only. Targets: Windows ~50 MB, macOS ~70 MB, Linux AppImage ~85 MB (v5.5.0 was 180–190 MB).

## Work packages (parallel after the core contracts land)

1. **Shell + core**: sidebar shell, tokens, jobs, settings, install discovery, launch, safe storage.
2. **HD Cards**: catalog, matcher, sync, coverage, new-card notices.
3. **Themes**: theme package format, apply/restore, preview gallery, card backs, polish Abyss/Nebula/Moon per the punch list.
4. **Music**: pack manifest, downloader/cache, preview player, role assignment.
5. **Formats**: Format Library client, deck import, whitelist builder (port archive `tools/build_format_lists.py`), host recipes.
6. **Release + README**: CI matrix, lean builds, update check, README with badges and screenshots.

## Card backs (draft assets in `_reference/reports/cardbacks/`)

- You: **Vortex, logos removed** (`vortex-clean.png`, 2472×3608). Alternative: anime-style Vortex.
- Opponent: **1998 OCG**, with or without the 遊戯王 emblem.
- Export for EDOPro as 177:254 PNGs (e.g. 708×1016) into the managed skin's `textures/cover.png` / `cover2.png`.

## Known limits (stock EDOPro, no engine patches)

- Skins control colours, nine-slice controls, some spacing, textures and global fonts. Deck-builder widget geometry and text alignment are hard-coded in C++, so they're out of reach (the archived engine patches in `_reference/engine/` could be offered upstream later).
- Music roles are folder-based; changes apply on the next game start.
- Historical formats: card pools and banlists are exact; a few rulings differ (e.g. Machina Gearframe in HAT).
