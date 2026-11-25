# Shared feature widgets

Import these controls from `edopal.ui.widgets`. The shell and preview runner apply `edopal.ui.theme` to the application. Construct widgets on the GUI thread.

| Widget | Usage |
| --- | --- |
| `Hero(kicker, title, subtitle="", *, art=None)` | Page heading with a serif title and optional right-side artwork. Pass a local path or `QPixmap`. Opaque art fills its space, while art with an alpha channel fits inside it. Use `set_art(image)` to change the image. |
| `Panel(*, padding=theme.LG)` | Charcoal surface. Add children to `panel.body`, a `QVBoxLayout`. |
| `PrimaryButton(text, *, icon=None)` | Gold action button with an optional bundled icon name. Connect the standard `clicked` signal. |
| `QuietButton(text, *, icon=None)` | Secondary action with the same API and keyboard behavior. |
| `Tile(title, caption="", *, image=None)` | Checkable artwork button. Pass a thumbnail path or `QPixmap`, or call `set_image` when the thumbnail is ready. The `previewed` signal fires on pointer entry and keyboard focus. Use `clicked` for activation and `setChecked(True)` to reflect the current selection. |
| `TileRow(tiles=())` | Places the supplied tiles in a single row of equal-width columns. Use `add_tile(tile)` to append. |
| `TileGrid(tiles=(), *, columns=3)` | Fills a fixed number of columns in row order. Supply the tiles for the current page. Both containers expose an exclusive `QButtonGroup` as `group`, with zero-based IDs in insertion order. |
| `Notice(text="", kind="info")` | Wrapped inline message. Call `set_message(text, kind)` with `info`, `success`, `warning`, or `error`. An empty message hides the notice. |
| `ProgressBlock(text="")` | Progress label, percentage and bar. Pass `set_progress` as a job’s `on_progress` callback. Zero or unknown totals use a busy indicator. |

Every widget also accepts a `parent` keyword. Arrow keys move focus between tiles for preview. Enter or Space activates the focused tile. Tile artwork is center-cropped. The complete tile title and caption remain available to assistive technology and on hover when space is tight.

```python
from edopal.ui.widgets import Panel, Tile, TileRow

panel = Panel()
abyss = Tile("Abyss", "Deep-sea blues", image=thumbnail_path)
abyss.previewed.connect(preview_abyss)
abyss.clicked.connect(apply_abyss)
panel.body.addWidget(TileRow([abyss, nebula, moon]))
abyss.setChecked(True)
```

Existing Qt widgets can set `role` directly, for example `button.setProperty("role", "primary")` or `label.setProperty("role", "goldKicker")`. For a tinted icon use `theme.icon("folder", theme.GOLD)`. Bundled names are `hdcards`, `themes`, `music`, `formats`, `settings`, `launch`, `folder`, `check`, `warning` and `undo`.
