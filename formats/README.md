# Format Library lists

The application reads `assets/formats/manifest.json`, `recipes.json` and `lists.zip` offline. The manifest identifies each catalogue entry by its Format Library ID. The date in a deck-editor name is the snapshot month. The banlist may have taken effect earlier.

## Rebuild a release snapshot

Use the project's Python environment, a writable copy of the response cache and a read-only EDOPro snapshot containing its databases, card scripts and stock GOAT list. Set `$cache` and `$game` to those paths and run the build from the repository root.

```powershell
& $py tools/build_format_lists.py --cache $cache --game $game --build-date 2026-11-18 --offline
```

Omit `--offline` to acquire missing inputs. Cached responses are keyed by the SHA-256 of the exact URL. Preserve the cache with the release inputs. A cold crawl can pick up later edits to the public archive. The manifest records the build date, response digests, ordered database fingerprints, stock file digest, available script identities and the reviewed TCG history fingerprint. Use `--out` to compare a new build with the existing one before replacing it.

Reviewed printing decisions for dated TCG snapshots are pinned in `tools/format_inputs/tcg-history.json`. To refresh them with the research project's reviewed chronology, run `$py -m tools.format_history --retro PATH --cache PATH`. The main build checks the selected IDs against the supplied databases and installed scripts. Goat takes its historical selections from the stock whitelist. OCG snapshots use current printings pending a regional chronology.

## Interpreting the evidence

- `regional_pool` counts Format Library identities returned by the regional date search. `pool` counts the matched identities retained after exclusions, including forbidden cards. `limits` counts explicit simulator printings with limits 0, 1, 2 and 3 respectively. Several artworks can represent one card.
- The build parses each finished list with the deck engine. For Goat it also compares effective limits, including artwork inheritance, for every installed ID. The EDOPro hash preserves the client’s duplicate-row behaviour when checking the stock reference.
- The deck sample uses up to two recent events with published lists and up to two top decks at each. Each sample records the event, deck, slots, printing substitutions and rejected slots. Copy limits apply across Main, Extra and Side together. The build retains the event assignment and records the deck’s own format tag when the source disagrees.
- The host recipe describes the separate duel settings in EDOPro 41.0.2. Its historical fidelity is independent of the deck sample.

## Installation API

`Catalogue.load()` reads the bundled files. `catalogue.select(region, era, query)` filters the rows. `host_steps(row, recipe)` and `copy_settings(row, recipe)` render the room settings.

`catalogue.bundled_list(format_id: int) -> decks.banlists.Banlist` reads and verifies one archived list in memory for `decks.legality.preflight`. It needs no game folder. Its source path identifies the member inside `lists.zip`. An unknown ID raises `KeyError` and an unsupported format raises `ValueError` with the catalogue reason.

Use `formats.installation.statuses(game, rows)` to inspect an installation. `change(game, catalogue, rows)` adds or updates its selected lists. Pass `remove=True` to remove them, a threading event for `cancel` and a progress callback accepting `(processed, total, format_name)`. Run these operations on a worker.

A stable ID in the filename allows a later release to rename its deck-editor entry. Each file carries its format ID and a digest covering its contents. Edits, occupied names and redirected folders are protected. A completed operation returns the changed and kept names and its prior file states. To reverse it, pass its affected rows and `undo=result.previous` to `change`. Undo is bound to the installation and checks the expected current bytes. A caller can supply a `Changes()` receipt to retain the completed portion of an interrupted operation. Studio keeps the last receipt in memory until the next change, folder switch or exit.

