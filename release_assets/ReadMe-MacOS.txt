EDOPro HD Cards Downloader for macOS
========================

How to use it
-------------
1. Unzip the download (double-click the .zip in Finder).
2. Double-click the extracted EDOPro HD Sync app.
3. Choose Full library or My decks, then press Sync HD cards.

The app finds your EDOPro folder on its own and shows it at the top of
the window. Press "Change folder" if it picked the wrong one.

If macOS asks the very first time
---------------------------------
Because the app came from the web, macOS may refuse to open it the first
time. RIGHT-CLICK "EDOPro HD Sync" and choose "Open", then "Open" again.
After that it opens normally with a double-click.

If macOS still refuses, open System Settings > Privacy & Security,
scroll down, and click "Open Anyway" next to EDOPro HD Sync.

Customize EDOPro
----------------
Close the game, open Customize EDOPro, and preview a Monokai skin in the
menu, deck editor and duel field. Select Highlight the turn player to see
a clearer active-player indicator. Pick Vortex or 1998 OCG independently for
each side, or preview the Thonk and Synth effects. Each section has its own
apply and restore controls. Reopen EDOPro to use your selections.

Soundtracks and tournament decks
--------------------------------
Open Music library to preview deck editor or duel music by game. Select
Add audio to import a recording. Manage library lets you bring a personal
soundtrack folder over from another device. If game music is off, close EDOPro
and choose Enable game music. Your volume setting is preserved. Choose
Restore playlist to bring your music back.

Open Tournament decks, choose a format and event, then preview or select a
published list. Add to EDOPro saves the deck in the selected game's deck
folder. Sync deck artwork gets the pictures for those cards. For Goat events,
Use installed GOAT card versions saves an additional historical deck. To host
with it, set Forbidden List to 2005.4 GOAT, Rule to GOAT, and Allowed Cards
to Anything goes. Leave the deck checks enabled.

Helpful notes
-------------
- Pick Full library or My decks on the home screen. Open Sync settings
  for artwork options, repairs and coverage.
- The archive contains one app. Its settings, retry caches, reports and personal
  music library live in ~/Library/Application Support/EDOPro HD Sync.
- The universal app supports Apple Silicon and Intel Macs.
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

  "EDOPro HD Sync.app/Contents/MacOS/EDOPro-HD-Sync" --health-check
