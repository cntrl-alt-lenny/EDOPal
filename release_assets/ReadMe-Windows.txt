EDOPro HD Cards Downloader for Windows
==========================

What it does
------------
Downloads the missing HD card artwork for EDOPro (ProjectIgnis), so your
cards look sharp instead of blurry. Rush Duel, anime, GOAT, Pre-Errata and
alternate artworks are all covered, plus the playmat art for Field Spells.

How to use it
-------------
1. Unzip the download (right-click the .zip -> "Extract All").
2. Double-click EDOPro-HD-Sync.exe
3. It finds your EDOPro folder and shows it in the window. If it got the
   wrong one, press "Change folder" and pick the right folder.
4. Choose Full library or My decks, then press Sync HD cards.

That's it. There is nothing to install, and the download contains only this
app and this ReadMe. You can put the app wherever you like; its small settings,
retry caches, and optional reports live in your Windows user profile instead of
beside the executable, so it also works from protected folders.

If Windows SmartScreen warns you, click "More info" then "Run anyway". That
warning appears because the app is not code-signed, not because anything is
wrong with it.

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
- The app takes a second or two to start, with nothing on screen while it
  does. That is normal: it unpacks itself each time so it can stay a single
  file.
- Only missing artwork is downloaded, so repeat runs are quick. Tick
  "Re-download everything" for a full refresh.
- "My decks" is much faster on a fresh install - it grabs
  just the cards in your saved decks.
- "Show coverage" tells you how much artwork you have without downloading.
- The app tells you when a new version is out. Download it from
  https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases and replace
  this file with the new one.

Quick sanity check
------------------
Open Command Prompt in this folder and run:

EDOPro-HD-Sync.exe --health-check

That runs a quick offline check to verify the tool is working correctly.
