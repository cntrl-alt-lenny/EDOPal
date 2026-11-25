# EDOPal — notes for coding agents

EDOPal is the EDOPro companion app: HD card art, themes, card backs and icons, music, sounds and Format Library formats, plus plugins such as the Deck Tracker. PySide6 Widgets, Python 3.13. Read `PLAN.md` for scope and architecture, `docs/packs.md` for the pack format.

## Run, test, lint
- Launch the latest checkout: `tools/run-dev.sh` (SteamOS hops into the `ai-apps` distrobox where `.venv` works). `EDOPal.desktop` at the repo root runs the same script.
- Tests: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -p no:cacheprovider` (use `python -m pytest`, not the pytest binary).
- Lint: `.venv/bin/ruff check src tests` (line length 125).
- Page previews: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m edopal.dev.preview <feature|shell> --out x.png --size 1920x1080 [--state …]`.

## Rules
- Work on branch `v6`; `main` still holds v5.5.0 until v6 ships.
- Never write to a real game folder outside `core.storage` (journaled, undoable). Packs only via `core.packs`; slow work via `core.jobs`.
- New feature = one folder in `src/edopal/features/<id>/` exposing `register(core) -> FeatureSpec` + one line in `app.py`. Plugins live in `src/edopal/plugins/<id>/`.
- Big media lives in the `cntrl-alt-lenny/edopal-packs` repo (checked out at `packs/`, gitignored here).
- `_reference/` (gitignored, local only) holds research reports, design mockup rounds (`_reference/design/<round>/`) and worker briefs/reports (`_reference/build*/`).

## Owner taste (summary)
Pixel-perfect alignment and centring, Title Case labels, neutral grey + gold `#ebc987`, Outfit headings + Inter body, compact info-dense layouts, real imagery only. No empty boxes, side bands, placeholder icons, credits blocks or giant hero titles. Review screenshots at 1920×1080 first, then 1280×800, and show design options before rebuilding a page. Everything must be usable from the app UI, never via a console command.
