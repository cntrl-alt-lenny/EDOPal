# Picking up EDOPal development

Read this first, then `CONTRIBUTING.md` and `studio/README.md`.

## Where things stand (18 November 2026)

- All current work is on the branch **`release-test/edopal`**. `main` is the old app and has not been touched. Nothing has been released.
- EDOPal is a PySide6 (Qt) companion app for EDOPro: one window, a sidebar, thirteen sections plus Settings.
- On Windows the full test suite passes (1,251 tests). On GitHub's Linux runner with Python 3.12 it also passes.

## Set up on a new machine (Linux / Steam Deck Desktop Mode)

```sh
git clone https://github.com/cntrl-alt-lenny/edopro-hd-cards.git
cd edopro-hd-cards
git checkout release-test/edopal
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install ruff==0.15.15 pytest==9.2.0 pytest-qt==4.5.0 pillow==13.0.0
```

Use Python 3.12 if you can (see the open issues).

Run the app:

```sh
python main.py --gui
```

Run the checks CI runs:

```sh
ruff check .
ruff format --check .
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Render screenshots of every section (offline, against a synthetic game folder):

```sh
QT_QPA_PLATFORM=offscreen python tools/studio_screenshots.py --out build/shots --size 1280x800
```

## Open issues, most important first

1. **Never tried in the real game.** Nobody has yet checked a Formats list in EDOPro's deck editor, a sleeve or skin in a duel, or the app on a Steam Deck. Do this before anything else.
2. **Linux + Python 3.11: the test run aborts about 38 seconds in**, three CI runs in a row. Python 3.12 on the same runner passes. Cause unknown.
3. **macOS:** 6 failing tests on the last completed CI run, and the job that screenshots the app on macOS fails. Nobody has seen the app on a Mac.
4. **GitHub's Windows runner:** 3 failures that do not happen on a real Windows PC — two skin-restore tests in `tests/test_appearance.py`, and the legacy Tk gallery failing to load an image.
5. **Known bugs from an independent review, not yet fixed:**
   - `extras.py`: after a failed turn-highlight toggle followed by applying a second skin, Restore can leave the pack's file in place of the player's own.
   - `card_databases.py`: a zero-byte `cards.cdb` makes a fresh install look ready; skip empty files in `iter_db_files`.
   - `extras.py`: the bundled "Synth music" option writes duel music without taking the playlist lock.
   - `music/digest_cache.py`: when the library root is not named `soundtracks` the cache falls back to the real per-user folder, so some `tests/music` runs write there.
6. **Not independently reviewed:** whether an artwork write can leave the game folder through a linked `pics` directory. The author's own tests cover a Windows junction.
7. **README and `docs/screenshots/`** predate the logo, the new galleries, Formats, Practice and Game display. `docs/screenshots/README.md` has the one-step recipe to regenerate them.
8. **Backup:** a read-only destination is refused correctly, but the page shows the file name under "Technical details" rather than in the main message.
9. Packaged start-up time has not been re-measured since pages became lazily built.

## Decisions that belong to the owner

- App icon colourway (gold on charcoal is installed).
- Merging to `main` and publishing a release.
- Publishing downloadable content packs (`tools/build_content_release.py`, see `docs/content-packs.md`); four skins' wallpaper rights are unverified and stay withheld.
- macOS minimum version is 14.4 because of the Qt version.

## Conventions worth knowing

- CI lints with **ruff 0.15.15**; a newer ruff accepts things it rejects.
- The app's name comes from `config.APP_NAME`; never hard-code it. The data folder keeps its historical name on purpose.
- Anything that writes into the game folder goes through the installation lock, checks whether the game is running (which can answer "can't tell"), writes atomically and records an undo point. Read `CONTRIBUTING.md` before adding a writer.
- Python's cyclic garbage collector is kept on the GUI thread (`studio/gc_guard.py`); worker threads must not destroy Qt objects.
- The bundled format lists in `assets/formats/` are hashed byte-for-byte; rebuild them with `tools/build_format_lists.py`, never edit by hand.
