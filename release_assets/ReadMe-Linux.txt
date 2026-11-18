@APP_NAME@ for Linux
Your unofficial companion for EDOPro.

Getting started
---------------
1. Extract the downloaded ZIP.
2. Open @APP_FILE@ in the extracted folder, or run "./@APP_FILE@".
3. Check the EDOPro installation shown at the top of the window. Use
   Change folder to select another copy.
4. Open HD Cards and choose My decks or Full library to sync artwork.
   The sidebar also gives you access to skins, sleeves, sounds, music,
   tournament decks, replays and backups.

This x86-64 build targets Ubuntu 22.04 and newer compatible systems. Use an
X11 or Wayland desktop with graphics drivers and libxcb-cursor.so.0. If the
extraction tool dropped the executable bit, run chmod +x "@APP_FILE@".
On Steam Deck, extract and run the app in Desktop Mode.

Settings and libraries use the @APP_DATA_NAME@ folder under XDG_CONFIG_HOME
or ~/.config.

Updates and help
----------------
Download the next version from the project's Releases page and replace
the app. Your settings and library carry over.
https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases

To check the extracted app offline, open a terminal in its folder and run:
  "./@APP_FILE@" --health-check
