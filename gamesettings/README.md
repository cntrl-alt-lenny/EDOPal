# Game display

The desktop editor targets Project Ignis EDOPro **41.0.2**. It reads the selected game's `config/system.conf`. The client rewrites this file on normal exit. The editor works offline and previews the individual settings that the next launch will use.

## Settings and evidence

The ranges below are the editor's choices. The game can store values outside them. Opening the editor preserves those values until the player chooses a change.

| Setting | Editor values | Evidence |
| --- | --- | --- |
| UI Scale (`dpi_scale`) | 100–200%. Desktop default 100%. | The game reads a floating-point multiplier. Its settings screen converts the entered percentage to a multiplier. [Scale handling](https://github.com/edo9300/edopro/blob/41.0.2/gframe/event_handler.cpp#L1873-L1886) |
| Text font and size (`textfont`) | An installed font and 9–24 pixels before UI scaling. A currently selected embedded font can keep its face and change size. | The parser splits the path at the first space and reads an unsigned byte for size. The game scales the font and uses it for interface and card-description text. [Parser](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game_config.cpp#L111-L142), [game fonts and card information](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game.cpp), [embedded font handling](https://github.com/edo9300/edopro/blob/41.0.2/gframe/CGUITTFont/CGUITTFont.cpp) |
| Fullscreen (F11) (`fullscreen`) | Off or on. | The configuration declares a boolean and the display event toggles fullscreen. [Declarations](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game_config.inl), [fullscreen handling](https://github.com/edo9300/edopro/blob/41.0.2/gframe/event_handler.cpp) |
| Graphics renderer (`driver_type`) | Game default, OpenGL, Direct3D 9, Direct3D 9 on 12, OpenGL ES 1 or 2. | The parser recognises renderer names conditionally at build time. Direct3D requires Windows. The ES paths require the appropriate Irrlicht build and are excluded on macOS. Actual driver support varies. [Renderer parser](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game_config.cpp#L87-L109) |
| VSync (`vsync`) | None or Full. Existing Half, Third and Fourth settings are readable. | The game populates different intervals depending on the renderer. The editor offers the two shared settings. [VSync choices](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game.cpp#L3540-L3551) |
| FPS limit if VSync off (`maxFPS`) | 30–240 frames per second. Existing 0 and −1 display as uncapped. | The client parses an integer and applies a positive cap when VSync is off. [FPS parser](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game_config.cpp#L150-L156), [render loop](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game.cpp) |
| MSAA (`antialias`) | Off, 2×, 4× or 8×. | The setting is an unsigned byte. The device requests edge smoothing and can fall back when the driver lacks the requested level. [Device creation](https://github.com/edo9300/edopro/blob/41.0.2/gframe/utils_gui.cpp) |
| Show FPS (`showFPS`) and Draw Field Spell arts (`draw_field_spell`) | Off or on. | The corresponding checkboxes control the visible frame counter and field artwork. [Event handlers](https://github.com/edo9300/edopro/blob/41.0.2/gframe/event_handler.cpp#L1981-L1990) |

The installed client guide describes MSAA fallback and lists renderer availability by platform under Advanced configuration. The installed English labels are strings 1445, 2060, 2068, 2070 and 2073–2075. Strings 12114–12118 name the VSync intervals.

**Window size** is a readout. Desktop startup uses 1024×640 multiplied by UI scale before applying native placement. A recognised Windows placement supplies an outer rectangle. macOS stores frame dimensions in points. Linux returns an empty placement string. Players can adjust UI scale, select fullscreen or drag a window edge in EDOPro. The editor preserves `windowStruct`, which can also contain the window position and maximised state. [Window creation and placement](https://github.com/edo9300/edopro/blob/41.0.2/gframe/utils_gui.cpp), [macOS frame storage](https://github.com/edo9300/edopro/blob/41.0.2/gframe/osx_menu.mm)

There is no independent card-description size in the pinned configuration. `numfont` is a separate face with game-defined sizes for numbers. `fallbackFonts` is a quoted chain for missing glyphs. The editor preserves both. Skin, server, identity, language, audio, duel, input and engine-tuning settings are outside the profile. [Complete configuration schema](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game_config.inl)

## Starting profiles

| Profile | Proposed edits | Reason |
| --- | --- | --- |
| Steam Deck (1280×800) | 125% UI scale, fullscreen on | The scaled 1024×640 base matches the Deck's 1280×800 display. Fullscreen also frees the space used by window borders. |
| Laptop / 1080p | 125% UI scale, full VSync | Modest enlargement with refresh synchronisation. |
| Large desktop / 4K | 200% UI scale | Double the size of the interface on a high-density monitor. |
| Low-power (smoother, plainer) | VSync off, 30 fps, MSAA off | Allow the frame cap to take effect and reduce graphics work. |

Profiles leave renderer and font choices to the player. Saving a custom profile keeps its selected values locally. Saving from Current settings includes values the editor can reproduce. Choosing a profile only prepares a draft.

## Engine and recovery

Call `DisplaySettings(game, config_directory).inspect()` on a worker, use `preview(snapshot.values, proposed)` for review, then call `commit(snapshot, proposed)`. For recovery use `recovery(snapshot, "undo" | "restore")` and the same mode on `commit`. Capture a fresh snapshot after a write.

A write checks that the selected game is closed, takes the shared appearance lock, verifies the reviewed keys, stages the replacement beside the original and journals the changed values. It checks the process and file again immediately before atomic replacement and reads the result back. Missing assignments can be inserted and removed. With duplicates, the editor changes the final matching assignment. A conflicting later edit stops an undo. Restore original previews the first recorded value of each touched key against the current file.

Recovery records live under `game-display/installations/` beside the active app config. Device profiles use a separate hashed device directory. Neither record contains a copy of the complete game configuration. The player can recover from a startup problem by closing the game and choosing **Settings → Game display → Undo last change**.
