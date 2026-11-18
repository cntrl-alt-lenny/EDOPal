# Building pages for EDOPro Studio

`main.py --gui` and the packaged application's normal launch open Studio. The console backend and its flags remain available. Use `--legacy-gui` to compare a section with the original Tk window. Studio requires the pinned PySide6-Essentials dependency.

## Run a local example

From the repository root, the following PowerShell commands create a disposable game with a real 128-card SQLite database, a deck and some existing images. Use a new demo directory for each clean installation.

```powershell
$py = 'python'
& $py -c "from pathlib import Path; from tools.studio_screenshots import create_demo; create_demo(Path('build/demo').resolve())"
& $py main.py --gui --config build/demo/config.json --edopro-path build/demo/Games/ProjectIgnis
```

The source app uses real downloads when you press **Sync HD cards**. **Show coverage** only measures the local files. The screenshot tool below simulates downloads. Geometry lives in `config.studio.ini` beside the supplied config. Sync reports, retry caches and preferences continue to use the backend's config location.

## Add or replace a page

The shell creates each class in `studio.shell.PAGES` once, in sidebar order. Replace the matching placeholder class in `studio/pages/` and keep its ID. For a new section, register its class in that tuple and add an original 24-unit SVG path in `studio/icons.py`. The scrolling rail starts groups at `cards` (Customise), `decks` (Decks) and `replays` (Tools). Settings stays pinned below the scroll area. The reserved pages are `mydecks`, `replays`, `backup` and `health`. Replace their classes in the matching page files without changing their IDs. Up and Down move focus through all groups, and Enter selects the focused section.

```python
from PySide6.QtWidgets import QVBoxLayout

from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.widgets import Button, SectionHeader, margins


class SkinsPage(Page):
    id, title, icon = "skins", "Skins", "skins"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        body = QVBoxLayout(self)
        margins(body, t.XXL)
        body.addWidget(SectionHeader(self.title, "Preview skins for your installation."))
        self.choose = Button("Change folder", "secondary", "folder")
        self.choose.clicked.connect(context.installations_requested.emit)
        body.addWidget(self.choose)
        body.addStretch()

    def installation_changed(self, installation: Installation) -> None:
        self.choose.setText("Change folder" if installation.path else "Choose folder")
```

- `shown()` runs on entry. Use it to resume previews or refresh stale data.
- `hidden() -> bool` runs before navigation or opening the folder chooser. Stop previews here. Return `False` to keep the page visible during work that owns the installation.
- `request_close() -> bool` runs before shutdown. Cards requests cooperative cancellation and permits closing. The shell waits for the pool to finish before saving geometry and closing.
- `escape() -> bool` handles an inline view's Back or Cancel action. Return `True` when handled.
- `installation_changed(installation)` runs on every page after selection. An empty `installation.path` means no game. A selected game can still be waiting for databases. `installation.ready` gates artwork jobs.
- `ctx.navigate("cards")` returns whether navigation succeeded. `ctx.notify(message)` displays a shell notice. `ctx.confirm(title, message)` asks a yes/no question on the UI thread.
- `ctx.sync_deck_files(game_path, tuple(ydk_paths))` hands a tournament import to HD Cards. A true return means a job was scheduled. The worker validates the installation and deck paths before calling the backend. Invalid input produces an inline error. The handoff preserves the next normal run's scope and options.

## Run background work

Use `ctx.tasks.start(work)` for blocking work. Capture paths and choices on the UI thread before starting. The worker receives a `WorkerContext`, which provides `progress(value)` and a per-job `cancel` event. Create widgets and `QPixmap` objects on the UI thread.

```python
from studio.tasks import WorkerContext

game = self.ctx.installation.path


def read_names(worker: WorkerContext) -> list[str]:
    from pathlib import Path

    names = []
    for deck in (Path(game) / "deck").rglob("*.ydk"):
        if worker.cancel.is_set():
            break
        names.append(deck.stem)
    return sorted(names)


job = self.ctx.tasks.start(read_names)
job.result.connect(self.show_names)
job.error.connect(lambda exc: self.ctx.notify(str(exc)))
```

`Job.progress`, `result`, `error`, `cancelled` and `finished` are delivered on the UI thread. Connect immediately after starting the job. `job.cancel()` requests cancellation. A cooperating worker can return its partial result. The runner emits the result or error, then `cancelled` if requested, then `finished`. Use `finished` for shared cleanup. For installation-changing jobs, call `ctx.set_busy(True)` before starting and release it on completion. Gate the page's own actions and navigation while busy. Read-only galleries can capture a selection generation to ignore results from a previous folder.

The artwork-specific entry is `ctx.tasks.sync(job_config, ctx.run_coro)`. It installs the existing Rich-compatible progress adapter, notice and coverage sinks, folder request and cancellation event on the isolated job config. It runs asyncio inside the worker. Rapid count and description updates are combined before reaching Qt. Each phase keeps its final count. Both desktop interfaces use `artwork_flow.py` for scoped-job configuration, deck validation, first-run recommendations and result models.

## Checking the game before a write

Capture the selected game path on the UI thread. Inside the worker call
`ctx.require_game_closed(game)` immediately before changing its files. This
checks the executable path for that installation using the music engine. Both
an open game and an unsuccessful process check raise `studio.context.GameOpenError`.
Other installations can stay open. Read-only previews and coverage do not
require this check.

Use a `GameClosedBlock` from `studio.widgets` inside the page. For
`GameOpenError`, release the busy state and call
`block.show_reason(str(error))`. Connect `block.retry.clicked` to the
original action so Retry performs a fresh check on a worker. Preserve the
action's options, including any imported deck list. Drop the retry when the
installation changes. During a write call `ctx.set_busy(True)`, veto
`hidden()` and release ownership on every completion path.

Settings and the installation bar share the inline picker. Emit
`ctx.installations_requested` to rescan or browse, or
`ctx.select_installation_requested.emit(path)` to validate a saved folder.
HD Cards defaults are saved atomically in the active config. The small
Studio state file beside it remembers known folders, per-game last runs,
the available release and window geometry. A full coverage check remains
available for large installations where opening every image automatically
would be expensive.

HD Cards keeps one restore point per game under `artwork-restore/` beside the
config. A sync records only the files it changes, before publishing each
change. A later sync that changes files replaces that game's restore point.
Undo verifies the saved and installed bytes, restores replaced images and
theme files, and removes new ones. An interrupted restore can be retried.
The classic window and CLI retain their original download behaviour.

## Components

Import these from `studio.widgets`. Layout dimensions and colours come from `studio.theme`.

| Component | One-line example |
| --- | --- |
| Primary, secondary and quiet buttons | `Button("Sync HD cards", "primary", "download")`, `Button("Cancel")`, `Button("Back", "quiet", "back")` |
| Exclusive segmented choices | `SegmentedControl([("Full library", "All indexed cards", "library"), ("My decks", "Saved decks", "deck")])` |
| Toggle with label and hint | `ToggleSwitch("Save a report", "Write the results beside your config.", True)` |
| Panel with a vertical body layout | `panel = Panel(t.XL)` |
| Section heading | `SectionHeader("Sync settings", "Choose artwork and maintenance options.")` |
| Search field | `SearchField("Search sleeves")` |
| Action tile | `ActionTile("Show coverage", "See what’s on disk", "coverage")` |
| Progress with cancel | `progress = ProgressBlock()` then `progress.begin("Reading databases…")` and `progress.advance_to(done, total)` |
| Illustrated empty state | `EmptyState("Skins", "Skin previews will live here.", "skins", "Appearance")` |
| Illustrated banner | `Hero("Audio", "Music library", "Choose your duel soundtrack.", "music")` |
| Inline notice | `notice.show_message("Folder selected.")` on a `Notice()` |
| Lazy image gallery | `grid = ImageTileGrid(ctx.tasks)` then `grid.set_tiles([Tile("Vortex", image_path)])` |
| Colour swatch | `SwatchButton("Accent", QColor(t.GOLD))` emits `colour_changed(QColor)` |
| Text and icons | `label("Library tools", "heading")`, `glyph("folder", t.ICON_BIG)` |
| Path with full tooltip | `path = ElidedLabel()` then `path.set_value(full_path)` |

The gallery uses Qt's virtualized item view, four pending image reads at most and a bounded thumbnail cache. Replacing its entries invalidates outstanding results. Connect the view's `activated` signal to keyboard or double-click selection.

## Tokens

| Family | Example |
| --- | --- |
| Surfaces and outlines | `t.CANVAS`, `t.RAIL`, `t.PANEL`, `t.RAISED`, `t.EDGE` |
| Text and semantic colours | `glyph("check", t.ICON, t.GREEN)` uses the same palette as `INK`, `TEXT`, `MUTED`, `GOLD` and `RED` |
| Interaction colours | `t.ACTIVE`, `t.HOVER`, `t.PRESSED`, `t.PALE`, `t.ON_GOLD` |
| Spacing, from 4 to 48 logical pixels | `layout.setSpacing(t.MD)` or `margins(layout, t.XXL)` |
| Radius and line weight | `painter.drawRoundedRect(rect, t.R_PANEL, t.R_PANEL)` or use `R_SMALL`, `HAIR`, `FOCUS` |
| Type | `t.font(t.HEADING, bold=True)` or choose a shared label role such as `display`, `heading`, `subheading`, `body`, `muted`, `goldKicker` |
| Control and image sizes | `button.setMinimumHeight(t.BUTTON_H)`, `QSize(*t.THUMB_SIZE)` |
| Layout and breakpoints | `width >= t.WIDE_CONTENT` changes the Cards tools to a second column |
| Timing | `animation.setDuration(t.MOTION_MS)` and `timer.start(t.NOTICE_MS)` |

`theme.apply(app)` registers the bundled Inter face, sets the Fusion palette and installs `theme.stylesheet()`. Display headings use Georgia or the platform serif fallback. Button roles carry keyboard focus, hover, pressed and disabled styles. The printed-card motif combines the repository's archive banner and vortex artwork with vector foil lines.

## Screenshots and page tests

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTEST_QT_API = 'pyside6'
$env:TEMP = $env:TMP = [IO.Path]::GetFullPath('build/tmp')
[IO.Directory]::CreateDirectory($env:TEMP) | Out-Null
& $py tools/studio_screenshots.py --out build/studio-shots --size 1280x800 --size 1600x1000
& $py -m pytest -q tests/studio --basetemp build/tests-studio
& $py -m pytest -q --basetemp build/tests-all
& $py -m ruff check .
```

The renderer creates its game under the clone, injects a simulated download coroutine and uses the real coverage pipeline. It suppresses startup discovery and update requests. The output directory contains one folder per size.

Use `make_window` in `tests/studio/` to build the real shell against a synthetic installation and optional fake coroutine. Activate normal controls and wait for observable state with `qtbot.waitUntil` or `waitSignal`.

```python
def test_open_skins(make_window):
    window = make_window()
    window.nav["skins"].click()
    assert window.current.id == "skins"
    assert window.current.ctx.installation.ready
```

The entry tests exercise source, frozen and legacy dispatch. The Cards tests cover progress, partial cancellation, errors, per-run options and the deck handoff. The widget tests scroll a 700-item gallery, including a jump to the last row.
