@APP_NAME@ for Windows
Your unofficial companion for EDOPro.

Getting started
---------------
1. Right-click the downloaded ZIP and choose Extract All.
2. Open @APP_FILE@.exe in the extracted folder.
3. Check the EDOPro installation shown at the top of the window. Use
   Change folder to select another copy.
4. Open HD Cards and choose My decks or Full library to sync artwork.
   The sidebar also gives you access to skins, sleeves, sounds, music,
   tournament decks, replays and backups.

Windows may show a SmartScreen warning for the unsigned app. If you trust
the copy from the project's Releases page, choose More info, then Run anyway
if Windows offers it.

Settings and libraries are stored in %LOCALAPPDATA%\@APP_DATA_NAME@.

Updates and help
----------------
Download the next version from the project's Releases page and replace
the app. Your settings and library carry over.
https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases

To check the extracted app offline, open PowerShell in its folder and run:
  & ".\@APP_FILE@.exe" --health-check
