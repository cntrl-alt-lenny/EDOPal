# Build the Format Library pack

Run from the EDOPal repository checkout. The release build works offline. It opens the supplied game's CDBs with SQLite's read-only, immutable URI options. Keep the game at the version used for the release while building.

```sh
PYTHONPATH=src .venv/bin/python -m edopal.features.formats.builder \
  --game /home/deck/Games/ProjectIgnis --out packs/formats \
  --build-date 2026-11-25 --update-summary

QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/formats/test_builder.py -q
```

On another platform, set `PYTHONPATH` to `src` and use the project's Python interpreter. Choose a fresh output directory to compare a candidate pack. Use `--update-summary` when preparing the corresponding app release. It writes the small Python table used to show coverage, room hashes and installation status before the pack is fetched.

## Source inputs

`inputs/regional.json.gz` pins the factual fields used by the build. It contains one sorted regional card catalogue for each of TCG and OCG, the expanded regional banlists and their effective dates, per-snapshot pool counts, reviewed sample identity counts, and SHA-256 for each input response. The release covers all 128 Format Library profiles. It includes 125 dated whitelists, two stock references and the engine limitation for Genesys. A separate build-time HTTP cache can retain the complete raw responses. The user-facing app fetches tournament data on demand.

To refresh the pin, choose a fresh response cache and output file. The collector requests at most 100 cards per page, joins restrictions by FL ID, checks unique identities, and verifies each pool against the archived regional count in `inputs/counts.json`. Use `--refresh-counts` to ask FL for new counts when reviewing source corrections. The collector pauses between HTTP requests. A rate-limit response stops collection and reports the server's retry interval. Restart with the same cache once the interval has passed. Add `--offline` to replay already-cached responses.

```sh
PYTHONPATH=src .venv/bin/python -m edopal.features.formats.builder.sources \
  --cache /tmp/edopal-format-responses --out /tmp/regional.json.gz

PYTHONPATH=src .venv/bin/python -m edopal.features.formats.builder \
  --game /home/deck/Games/ProjectIgnis --out /tmp/candidate-formats \
  --build-date 2026-11-25 --source /tmp/regional.json.gz
```

`inputs/tcg-history.json` preserves the reviewed TCG chronology from commit `a6c6601769b89ead05280b8c654a2afa80fa0338` on `refs/archive/dev/steamos`. Its `source_sha256` identifies the reviewed errata records. Refresh its per-date decisions with the archived `tools/format_history.py` when those records change. The builder resolves every alternative against the supplied game database and checks for the matching installed script.

The regional restrictions include reviewed source discrepancies. Blaster, Dragon Ruler of Infernos has both a Forbidden and a Limited record in Format Library's June and September 2023 TCG responses. Both contemporaneous Project Ignis lists permit one copy. Sixth Sense is Forbidden in the September 2013 OCG list. The pack's provenance records these decisions and sources. [June 2023 TCG](https://github.com/ProjectIgnis/LFLists/blob/0e9f88d57d5ecabbeb7bbd20e63ce1ef6aa8df7a/0TCG.lflist.conf) · [September 2023 TCG](https://github.com/ProjectIgnis/LFLists/blob/71cb58b590f00cb9931496e364c3453ef6dc52cc/0TCG.lflist.conf) · [September 2013 OCG](https://www.yugioh-card.com/japan/event/limitregulation/?list=201309)

## Deck validation

Every format with an available public sample received a fresh check against the supplied game. This release covers 103 of 128 formats with 128 freshly checked decks and 8,796 card slots. Of those decks, 124 passed and four failed, with five rejected slots. Twenty-four playable formats had no sample in the recorded searches. Genesys has an unsupported status.

| Current source | Profiles | Decks | How the build uses the record |
| --- | ---: | ---: | --- |
| Featured event decks in `regional.json.gz` | 8 | 30 | Resolve each FL identity to an installed historical printing. Check section sizes and the copy cap across all three sections. |
| Published tournament lists in `tournament-samples.json` | 93 | 93 | Read each published YDK and preview printing counts. Prefer a previously rejected sample when choosing a deck to recheck. |
| Public deck search in `published-samples.json` | 2 with samples | 5 | Recheck three Kashtira lists against its corrected whitelist and two Advanced user lists against the stock TCG list. Preserve search results for every unsampled format. |

The November 18 audit in `inputs/archive-audit.json` adds evidence only when the room hash and the SHA-256 of all canonical passcode/limit rows match the rebuilt historical list. Within a format, a current result replaces an older result for the same deck ID. A withdrawn or mismatched event/deck association drops the obsolete sample. The rollup retains 220 additional archived decks, all passing. Across the deduplicated records, 344 of 348 decks passed, covering 24,230 slots. Each profile exposes current and archived counts in `validation.fresh_counts` and `validation.archived_counts`. Rejected slots identify the card, section and reason. Section-size failures appear in the individual deck's `deck_errors`.

The regional collector above refreshes the featured 30 event decks. To refresh the other two evidence pins from the project checkout, use a fresh cache directory for each release and replace the date with the day the samples were captured.

```sh
PYTHONPATH=src .venv/bin/python -m edopal.features.formats.builder.validation \
  --cache /tmp/edopal-validation-responses --date 2026-11-25 --also Kashtira

PYTHONPATH=src .venv/bin/python -m edopal.features.formats.builder.validation \
  --cache /tmp/edopal-validation-responses --date 2026-11-25 --tournament
```

Repeat `--also NAME` for other reviewed changes to historical limits. Both capture commands accept `--offline` for a replay of their raw responses. Rebuild the pack after capturing both sets. A test failure caused by a malformed response requires a review of the pin. A legally invalid published deck remains visible in the validation results.

## Release checks

- Each whitelist lists decimal passcodes and integer limits. Unlisted cards default to forbidden, with EDOPro's adjacent artwork alias exception. The builder verifies combined alias-family copy limits across the Main, Extra and Side Decks of the published samples. The checks exercise card identities after local printing resolution. Tournament rulings and duel behaviour require separate testing.
- Goat is the stock Project Ignis list. The builder compares its effective limits against the regional reconstruction for every installed card ID. Retaining the original file, including its duplicate printing row, preserves compatibility with stock GOAT rooms.
- HAT uses the July 12 North American WCQ snapshot. The event excluded the cards debuting in Space-Time Showdown and retained previously legal cards reprinted in that set. Release checks cover Michael, Shogi Rook, the banned and restricted cards, and historical Ryko and Red-Eyes Darkness Metal Dragon. The manifest describes the known Machina Gearframe interaction. [Format Library HAT](https://formatlibrary.com/formats/HAT) · [Space-Time Showdown](https://www.yugioh-card.com/en/products/past_products/superstarter2014/) · [WCQ FAQ excerpt](https://ygorganization.com/north-american-wcq-faq-has-been-posted)
- `pool` counts retained card identities, including forbidden cards. `printings` counts distinct explicit passcodes written to the list. `historical` counts historical substitutions. `fallback` records the numbers of ambiguous chronology decisions and unavailable historical versions. A fallback uses an available current printing.
- Each dated profile in `manifest.json` supplies the EDOPro room hash, SHA-256, profile date, list effective date and host recipe. Its `files` map covers every other pack file. `provenance.json` pins response and database hashes, script identities, historical decisions, exclusions and sample deck IDs. The catalogue thumbnail in `packs/thumbs/formats.webp` shows Black Luster Soldier, Stardust Dragon and Artifact Moralltach from the existing game artwork.

Advanced and Traditional reference the list files under the selected game’s `repositories/lflists/`. Their pool counts describe TCG-supported card identities in the build database. Genesys has no playable whitelist because EDOPro cannot enforce the points budget. OCG profiles use OCG release dates and banlists, with modern printing scripts. Their `fallback.unassessed` count identifies the pool lacking a reviewed regional errata chronology.
