# Theme engine

Import the public interface from `themes`. The page can keep an immutable `Theme` in memory and use `dataclasses.replace(theme, accent="#74CFC2")` for slider updates. Initialize Qt on the main thread. Schedule library I/O, photo decoding, full-resolution export, game-state reads and installation work on a worker. Keep the decoded photo and scaling preference with the page's preview state.

## Python interface

```python
ensure_gui() -> QGuiApplication                    # Reuse the existing application or create one.
Theme.from_json(data: bytes) -> Theme             # Parse and validate a versioned UTF-8 document.
theme.to_json() -> bytes                          # Serialize an editable definition.
derive(theme) -> Palette                          # Resolve colours, readable labels and both LP gradients.
widget_image(theme, name) -> QImage               # Get the source bitmap of a named widget state.
skin_xml(theme) -> bytes                          # Build the game's UTF-8 XML.
wallpaper(theme, size=(2560,1600), *, image=None, scale_background=True) -> QImage
render_preview(theme, scene, size=(960,600), *, opponent_turn=False, image=None, scale_background=True) -> QImage
render_skin(theme, *, image=None, scale_background=True) -> dict[str, bytes]  # Paths relative to the skin folder.

ThemeLibrary(directory=None)                      # Defaults to config.user_data_dir()/themes.
library.entries() -> dict[str, Theme]             # Map saved IDs to definitions.
library.load(theme_id) -> Theme                   # Read one saved definition.
library.save(theme, theme_id=None) -> str         # Supply an ID to edit, omit it to duplicate.
library.add_image(path) -> str                    # Validate and copy a local PNG/JPEG, returning its SHA-256.
library.image(theme) -> QImage | None             # Resolve and decode a saved photo for preview/install.
library.export_file(theme_id, path) -> None       # Write one self-contained share file.
library.import_file(path) -> str                  # Validate a share and assign a fresh local ID.

scale_background(game) -> bool                    # Read the game's stretch/centred-crop preference.
install_state(game, theme_id) -> InstallState     # Read folder, active, can_undo and recovery_pending.
install_theme(game, theme_id, theme, *, image=None) -> str  # Install or edit, then activate.
switch_back(game, theme_id) -> None               # Select the predecessor, restoring owned font keys.
undo_theme(game, theme_id) -> None                # Reverse one edit, or the first install.
remove_theme(game, theme_id) -> None              # Remove verified owned files and restore selection if active.
recover(game) -> None                             # Finish the installation's pending operation.
```

Filesystem arguments are `pathlib.Path` objects. A theme ID is 32 lowercase hexadecimal characters. `PRESETS` contains 16 editable definitions. `SCENES` contains `menu`, `deck` and `duel`. `WIDGETS` maps the 15 state names to their XML paths, source sizes and top/left/bottom/right border widths.

Pass `library.image(theme)` to image-based preview and install calls. For an inherited background, the page can supply a previously loaded game scene image to `render_preview`. Without one, the schematic uses the theme background swatch. The schematic uses Inter for its labels. The game keeps its existing font configuration unless the player chooses Inter.

## Definition version 1

| JSON fields | Type and meaning |
| --- | --- |
| `version`, `name` | Required version integer `1` and a printable display name of 1–64 characters. Path separators, drive prefixes, trailing dots and reserved device names are rejected. |
| `background`, `surface`, `accent` | Three required `#RRGGBB` colours. |
| `text`, `muted`, `highlight`, `positive`, `danger`, `your_lp`, `opponent_lp` | Optional `#RRGGBB` overrides. Omitting a value or supplying `null` uses the derived colour. Text and surfaces undergo contrast correction. |
| `highlight_turn` | Boolean, default `true`. |
| `roundness`, `depth` | Integers from 0–8 and 0–100. Defaults are 5 and 50. Each corner fits its protected slice. |
| `wallpaper` | `gradient`, `vignette`, `geometry`, `aurora` (default), `image` or `inherit`. `STYLES` lists the four procedural choices. |
| `image` | Required for `image` wallpaper. Holds the library asset's 64-character lowercase SHA-256. Otherwise omit it or use `null`. |
| `font` | `inherit` (default) or `inter`. |

A complete procedural example follows.

```json
{
  "version": 1,
  "name": "Harbour Lights",
  "background": "#101D26",
  "surface": "#1C3440",
  "accent": "#55D6BE",
  "text": "#EFF6F2",
  "muted": "#AEC5C2",
  "highlight": "#EFABCF",
  "positive": "#A9DC76",
  "danger": "#FF6188",
  "your_lp": "#55D6BE",
  "opponent_lp": "#E9A66C",
  "highlight_turn": true,
  "roundness": 6,
  "depth": 60,
  "wallpaper": "aurora",
  "image": null,
  "font": "inherit"
}
```

Procedural and inherited themes share as plain JSON. Image themes share as a stored ZIP containing exactly `theme.json` and `wallpaper`. Both layouts can use the extension `.edotheme`. Import bounds the JSON at 4 KiB, the original image at 20 MiB, decoded pixels at 24 million and each side at 16,384 pixels. Import reads named members into memory and writes validated content using locally generated IDs.

## Game operations

Close EDOPro before applying changes. One OS lock at the installation root covers generated skins, legacy appearance operations and the music-setting writer. A lock conflict raises `OSError` so the caller can retry. Validation and ownership conflicts raise `ValueError`. Filesystem faults propagate as `OSError`.

The folder starts at `skin/Studio-<id>` and gets a suffix if that name is occupied. The display name lives in the XML. A private marker identifies the folder. The game's `config/.edopro-studio-themes/` stores staged content, the pending operation and the latest undo per theme across app configurations. The library stays in the player's profile. Optional Inter files and their licence live inside the owned skin folder.

All mutating operations first finish pending work. Recovery accepts the recorded before or after hash of each asset. An unexpected hash stops the operation and identifies the file. The player can preserve their external edit separately and restore the expected bytes before retrying. Activation changes individual owned keys only when the expected value still matches. Undo and removal preserve extra player files. Switching back uses a single atomic settings update and also works when the player has edited a generated file.

