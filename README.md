# EDOPal

**Your unofficial companion for EDOPro.**

Sync HD card art, customize your game, and manage decks and replays in one resizable desktop window. EDOPal was formerly EDOPro HD Sync / EDOPro HD Cards Downloader.

**[Download EDOPal](https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases/latest)** · [Screenshot gallery](docs/gallery.md) · [Documentation](docs/index.md)

<!-- Hero screenshot placeholder: docs/screenshots/edopal-overview.png
Show the EDOPal sidebar, selected demo installation and HD Cards with My decks selected.
Render the remaining shots in the order listed in docs/gallery.md. -->

## Download and first run

You’ll need an existing [EDOPro installation](https://projectignis.github.io/). If it’s new, open EDOPro once and let it finish downloading its card databases. Then get the ZIP for your platform from [GitHub Releases](https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases/latest).

| Platform | Release ZIP | Open after extracting |
| --- | --- | --- |
| Windows | `EDOPal-Windows-v<VERSION>.zip` | Right-click the ZIP, choose **Extract All**, then double-click `EDOPal.exe`. |
| macOS 14.4 or later | `EDOPal-macOS-v<VERSION>.zip` | Double-click the ZIP in Finder, then open `EDOPal.app`. The universal build targets Apple Silicon and Intel. |
| Linux x86-64 | `EDOPal-Linux-v<VERSION>.zip` | Open `EDOPal` from your file manager, or run `./EDOPal` in the extracted folder. The build targets glibc 2.35 or newer. |
| Steam Deck | The Linux ZIP | Choose **Steam → Power → Switch to Desktop**, then extract and open the Linux app. Game Mode is untested. [Valve’s Desktop Mode guide](https://help.steampowered.com/en/faqs/view/671A-4453-E8D2-323C) |

EDOPal runs directly from the extracted app. Windows and Linux include a short ReadMe. The macOS ZIP contains one Finder app.

- **Windows SmartScreen** may warn about an unfamiliar download. If you trust the copy you downloaded from this project’s Releases page and Windows offers the option, choose **More info → Run anyway**. [Microsoft’s SmartScreen guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- **macOS Gatekeeper** may block the first launch. If the warning is about an unidentified developer and you trust the download, try opening the app once, then choose **System Settings → Privacy & Security → Open Anyway** if offered. [Apple’s Gatekeeper guidance](https://support.apple.com/en-us/102445)
- **Linux or Steam Deck** may need the execute permission restored after extraction. Run `chmod +x EDOPal` in the extracted folder, then run `./EDOPal`.

Check the game folder at the top of the window. Use **Change folder** if you have a different copy. In **HD Cards**, choose **My decks** to start with the cards you play, or **Full library** to cover the installed databases. Close EDOPro before applying appearance or audio changes, then reopen it to load the changes.

## What you can do

Some of the features below have working engines and sidebar pages still in development. The [gallery](docs/gallery.md) marks the pending captures.

| Section | What it does |
| --- | --- |
| **HD Cards** | Download missing card images and Field Spell playmat art. Check coverage, repair broken files, or choose the original artwork for GOAT and Pre-Errata wording where available. Resolution and availability vary by card. |
| **Skins** | Start from 16 editable presets. Choose colours, generate a wallpaper or bring a photo, preview the menu, deck editor and duel field as you edit, and share your theme as an `.edotheme` file. [Theme reference](themes/README.md) |
| **Sleeves** | Design a card back from 24 patterns and 16 palettes, use cropped artwork for an installed card when available, or bring your own image. Choose your sleeve and your opponent’s independently. |
| **Sounds** | Audition cues, apply a set, or mix, match and silence individual slots. The engine covers 24 duel and interface sound slots, plus card-specific chants. [Sound reference](docs/sounds.md) |
| **Music** | Browse a catalogue of 69 games, import tracks or a folder of your own recordings, and build playlists for seven moments from the main menu to victory or defeat. The playable tracks depend on your installed and imported audio. |
| **Tournament decks** | Browse public events and standings by format, preview lists, save copies to EDOPro and sync their artwork. For Goat events you can also save a separate copy using installed historical cards. Review any unresolved cards before playing. [Format Library](https://formatlibrary.com/events) |
| **My decks** | Search your saved decks by card, check them against an installed banlist, compare versions and preview or copy `ydke://` sharing codes. Legality checks use your installed card data and the chosen list. |
| **Replays** | Find recordings by player, label, notes or tags. Save a copy of a rolling last replay and export a replay with its notes. |
| **Backup & transfer** | Make a local ZIP of decks and replays, with optional categories, for a backup or a move between installations such as a PC and a Steam Deck. Preview conflicts before restoring. A physical PC ↔ Steam Deck transfer is untested. [Backup reference](docs/backup.md) |
| **Health check** | Check local databases, banlists, storage, skin files and audio. Copy a support summary with the findings and no personal details. [Health check reference](docs/doctor-replays.md) |

### On-demand content packs

The pack engine keeps optional downloads separate so the app can stay small. The catalogue records each pack’s author, licence, attribution and size. Previews can load separately. When you choose a downloadable pack, the engine verifies the file and keeps a local copy for reuse. Music, covers, wallpapers and sleeves can be added to their local libraries. The bundled catalogue starts empty, and available packs depend on what has been published with redistribution permission. [Content pack guide](docs/content-packs.md)

## FAQ and troubleshooting

**The folder is right, but I can’t sync.** Open EDOPro and let its databases finish updating. Return to EDOPal and select the folder again if needed.

**Some cards are missing or blurry.** **Show coverage** reports what’s on disk. Use **Repair broken images first** in **Sync settings** for damaged files. The artwork hosts can have different versions of a card or no image for it. The command-line flag `--recheck-missing` retries cards that providers previously reported missing.

**Music previews here but is silent in EDOPro.** Check the scene and game volume. If music is disabled, close the game and enable it. EDOPal’s music switch preserves the volume setting.

**How do I update?** Download the new platform ZIP and open the extracted app in place of your old companion. Update any shortcut to the old executable. The packaged app still uses the `EDOPro HD Sync` per-user data folder for your settings and library.

**Can I undo changes?** Skins, sleeves, sounds, playlists and backup restores have their own recovery options. Imported decks and preserved replays are separate files you can remove. Keep a copy of existing artwork or textures you plan to refresh, repair or prune. Those operations have no built-in undo. [What EDOPal writes and how to undo it](docs/safety.md)

## Privacy and network use

| Host | Why EDOPal contacts it |
| --- | --- |
| `images.ygoprodeck.com` and `pics.projectignis.org:2096` | Card and Field Spell artwork. Sleeve art requests use the YGOProDeck image host. |
| `api.github.com`, `github.com` and GitHub’s release asset hosts | Update notices, pack catalogue and downloads, and the older curated texture downloads. |
| `formatlibrary.com` and `cdn.formatlibrary.com` | Public tournament formats, event searches, standings, deck previews, thumbnails and downloads. |

EDOPal has no account or telemetry. It keeps your files, settings and support summary on your device. The services receive ordinary connection details such as your IP address, plus the requested public IDs or tournament search text. A deck-only art sync requests the individual card IDs it needs. Configured sources and download redirects can add hosts. Opening a cover credit or source link uses your browser. [Full network inventory and data locations](docs/privacy.md)

## Help and development

For a bug, include the Health check’s **support summary** if available. [Open an issue](https://github.com/cntrl-alt-lenny/edopro-hd-cards/issues/new/choose)

See [Contributing](CONTRIBUTING.md) for source setup, tests and screenshots, and the [documentation index](docs/index.md) for engine references.

<details>
<summary>Command-line reference</summary>

Run `python main.py --help` for help from source. A normal source launch runs the console sync. A normal packaged launch opens EDOPal.

| Flag | What it does |
| --- | --- |
| `-h` / `--help` | Show argument help. |
| `--gui` / `--no-gui` | Open the Qt window or run in the console. |
| `--legacy-gui` | Open the original Tk window during the migration. Mutually exclusive with `--gui` and `--no-gui`. |
| `--edopro-path PATH` | Select a game folder and skip auto-detection. |
| `--config PATH` | Use a specific config file. |
| `--generate-config` | Write a default config file and exit. |
| `--dry-run` | Preview the artwork queue without downloading. |
| `--my-decks` | Sync cards from your EDOPro deck folder. |
| `--deck PATH` | Sync cards from a `.ydk` file. Repeat for several decks. |
| `--decks-folder PATH` | Sync cards from the `.ydk` files in a folder. |
| `--stats` | Show artwork coverage and disk usage without downloading. |
| `--force` / `--no-force` | Enable or disable refreshing existing images. |
| `--repair` | Fetch missing or damaged images. |
| `--field-art` / `--no-field-art` | Enable or disable Field Spell playmat art. On by default. |
| `--original-text` / `--no-original-text` | Prefer original GOAT and Pre-Errata artwork or use the default sharper matching policy. |
| `--recheck-missing` | Retry artwork that’s in the missing-image cache. |
| `--prune` | Delete orphaned JPEGs in `pics/` after a full-index sync. Back up existing images first. |
| `--textures` / `--no-textures` | Enable or disable legacy curated texture downloads into `textures/`. |
| `--textures-pack NAME` | Choose a legacy curated pack. |
| `--save-report` / `--no-save-report` | Enable or disable a timestamped sync report beside the config file. |
| `--quiet` | Use minimal console output. |
| `--concurrency N` | Set the maximum simultaneous downloads. Default 50. |
| `--max-retries N` | Set retries per download. Default 3. |
| `--timeout N` | Set the artwork HTTP timeout in seconds. Default 30. |
| `--health-check` | Run quick offline checks of the companion and exit. For the detailed game report from source, run `python -m doctor PATH` with your game folder. |
| `--no-pause` | Skip the exit prompt in an interactive Windows packaged console session. |

</details>

## Credits and licences

- NiiMiyo created the original downloader that inspired this project. [EDOPro-Hd-Downloader](https://github.com/NiiMiyo/EDOPro-Hd-Downloader)
- YGOProDeck and Project Ignis serve card images. Format Library supplies public tournament data and deck lists. [YGOProDeck](https://ygoprodeck.com/) · [Project Ignis](https://projectignis.github.io/) · [Format Library](https://formatlibrary.com/events)
- The music catalogue records editions and sources for game covers, including the LaunchBox Games Database. [Music catalogue](assets/music/catalog.json) · [LaunchBox Games Database](https://gamesdb.launchbox-app.com/)
- Thonk draws on Kenney’s CC0 audio. Inter is distributed under the SIL Open Font License. Pack credits and third-party assets carry their own terms. [Appearance credits](assets/appearance/README.md) · [Inter licence](assets/window/fonts/Inter-OFL.txt) · [Kenney Audio](https://kenney.nl/assets/category:Audio)
- EDOPal’s source code uses the [MIT License](LICENSE).

EDOPal is an independent fan-made companion for EDOPro, a Project Ignis project. EDOPal has no affiliation with or endorsement from Project Ignis, Konami or Shueisha. Yu-Gi-Oh! and related marks belong to their respective rights holders.
