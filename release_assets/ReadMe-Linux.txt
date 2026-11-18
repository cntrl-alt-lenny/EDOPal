@APP_NAME@ for Linux
========================
Your unofficial companion for EDOPro.

What it does
------------
Downloads the missing HD card artwork for EDOPro (ProjectIgnis), so your
cards look sharp instead of blurry. Rush Duel, anime, GOAT, Pre-Errata and
alternate artworks are all covered, plus the playmat art for Field Spells.

How to use it
-------------
1. Unzip the download.
2. Double-click @APP_FILE@ (and choose "Run"), or run ./@APP_FILE@
   from a terminal.
3. It finds your ProjectIgnis folder and shows it in the window. If it got
   the wrong one, press "Change folder" and pick the right folder.
4. Choose Full library or My decks, then press Sync HD cards.

That's it. There is nothing to install, and the download contains only this
app and this ReadMe. You can put the app wherever you like; its small settings,
retry caches, and optional reports live in your normal per-user config folder.

If your file manager will not run it, mark it executable first:
  chmod +x @APP_FILE@

Skin downloads
--------------
Full-size skins are separate content packs. A downloaded skin stays in your
per-user pack cache. Appearance previews, built-in sleeves and duel sound
effects are included in the app.

Helpful notes
-------------
- The app may take a few seconds to start. That is normal: it unpacks itself
  each time so it can stay a single file.
- Only missing artwork is downloaded, so repeat runs are quick. Tick
  "Re-download everything" for a full refresh.
- "My decks" is much faster on a fresh install - it grabs
  just the cards in your saved decks.
- "Show coverage" tells you how much artwork you have without downloading.
- The app tells you when a new version is out. Download it from
  https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases and replace
  this file with the new one.
- Built on Ubuntu 22.04 for x86_64. Use an X11 or Wayland desktop with
  graphics drivers and libxcb-cursor.so.0 (libxcb-cursor0 on Ubuntu).
  On Steam Deck, unzip in Desktop Mode. To try Game Mode, add the executable
  to Steam as a non-Steam game.

Quick sanity check
------------------
From a terminal in this folder you can also run:

./@APP_FILE@ --health-check

That runs a quick offline check to verify the tool is working correctly.
