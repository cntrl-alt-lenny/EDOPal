@APP_NAME@ for macOS
========================
Your unofficial companion for EDOPro.
Requires macOS 14.4 or newer on Apple Silicon or Intel.

How to use it
-------------
1. Unzip the download (double-click the .zip in Finder).
2. Double-click the extracted @APP_FILE@ app.
3. Choose Full library or My decks, then press Sync HD cards.

The app finds your EDOPro folder on its own and shows it at the top of
the window. Press "Change folder" if it picked the wrong one.

If macOS asks the very first time
---------------------------------
Because the app came from the web, macOS may refuse to open it the first
time. RIGHT-CLICK "@APP_FILE@" and choose "Open", then "Open" again.
After that it opens normally with a double-click.

If macOS still refuses, open System Settings > Privacy & Security,
scroll down, and click "Open Anyway" next to @APP_FILE@.

Skin downloads
--------------
Full-size skins are separate content packs. A downloaded skin stays in your
per-user pack cache. Appearance previews, built-in sleeves and duel sound
effects are included in the app.

Helpful notes
-------------
- Pick Full library or My decks on the home screen. Open Sync settings
  for artwork options, repairs and coverage.
- The archive contains one app. Its settings, retry caches, reports and personal
  music library live in ~/Library/Application Support/EDOPro HD Sync.
- Only missing artwork is downloaded, so repeat runs are fast. Tick
  "Re-download everything" in the window for a full refresh.
- "Show coverage" tells you how much artwork you have without downloading.
- Rush Duel, anime, GOAT, Pre-Errata, and alternate artworks are all
  covered, plus the playmat art for Field Spells.
- When a newer version is out, the app tells you - download it from the
  Releases page and replace this one.

Quick sanity check
------------------
Advanced users can run a quick offline self-test in Terminal:

  "@APP_FILE@.app/Contents/MacOS/@APP_FILE@" --health-check
