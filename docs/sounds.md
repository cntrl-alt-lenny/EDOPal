# Duel sounds engine

## Page API

Import these names from `sounds`. Pass `pathlib.Path` for files and installation directories and the active app config path to transaction calls. Run discovery, import, export, install, and settings operations on the page's worker. Audition opens and closes the device on its own worker.

| API | Behaviour |
| --- | --- |
| `EVENTS`, `CHANT_TYPES`, `event_for(key)`, `chant_key(kind, passcode)` | Ordered root catalogue, supported chant families, and event metadata. A chant key looks like `chant/summon/24550676`. |
| `scan_installation(game)`, `installed_set(rows)` | Read the current cue for every root slot and any installed chants. Each row has the selected path, alternatives, clip, size, duration and any validation error. `installed_set` copies playable cues into a snapshot named **What you have now**. Missing and unreadable cues are omitted. |
| `bundled_sets()` | Returns Thonk (24 cues, Kenney CC0) and Synth (10 cues). Thonk expands ZIP aliases. Synth audio credits are undocumented. |
| `SoundSet(name, assignments={}, credits=Credits())` | An immutable mapping of event key to `Clip` or `None`. `None` silences that slot. An omitted key leaves it alone. Set and clip credits each carry author, license, and attribution. |
| `load_clip(path, credits=...)`, `set.pick(key)`, `set.with_assignment(key, clip)`, `set.without_assignment(key)` | Validate a player's file, copy an existing choice with its inherited credits, assign audio or silence, or clear a choice. Changes return a new set. |
| `export_set(set, path)`, `read_set(path)`, `load_pack(path, kind="sound-effects")` | Export a portable `.edosounds` ZIP, open one without installing it, or map a local downloaded pack ZIP to a set. |
| `SoundLibrary(directory=None)` | Defaults to `config.user_data_dir()/sounds/`. `save(set, set_id=None)` and `import_set(path)` return a persistent ID. `load(id)` reads a set. `list()` returns `SavedSet` rows with IDs and either a set or an error. |
| `apply_set(game, config_path, set, events=None)` | Apply all assigned cues or an iterable of selected keys. An assigned file replaces its alternate formats. An explicit silence removes every supported format for the slot. |
| `undo(game, config_path)`, `restore(game, config_path)` | Revert the latest change or return managed slots to their original files. Restoring originals is itself undoable. `can_undo` and `can_restore` indicate recorded work and do not check for drift. |
| `sound_settings(game)`, `set_sound_enabled(game, enabled)` | Read enabled, volume (0–100), and configured backend. The optional write uses the same process check, lock, and byte-preserving replace as music. It flips an existing 0/1 switch with the game closed. |
| `AuditionPlayer()` | `preview(set, key)` and `sampler(set, events=None)` return a `Future[None]` that completes after playback and carries errors. The sampler plays up to six cues for up to two seconds each. A new preview replaces the old one. Poll `playing` or the future from the UI and call `stop()` on navigation and `close()` when disposing the player. These calls signal the worker and return immediately. |

The effects transaction uses the existing `extras-backups/<installation-id>/undo-effects` directory, content backups, and game-side lock. Apply, undo, and restore stop before overwriting managed files whose contents or presence changed outside the journal. For every changed event, the journal tracks all supported paths, even alternatives that were absent. An interrupted write rolls back before a new operation. Undoing an interrupted apply rolls back that apply and leaves the prior undo available.

Restore covers each slot from its first recorded change for the selected installation and config directory. If an older gallery journal exists, its latest recorded originals become the recoverable starting point. Older revisions and formerly unjournaled installs do not carry enough history to identify the first original. The legacy gallery continues to install the bundled WAVs and keep existing alternate formats in place.

## Event catalogue

Every root slot supports WAV, MP3, Ogg Vorbis and FLAC. The client checks the formats in that order and selects the first path that exists. This choice is made before decoding. With all formats absent, a fresh client startup skips the cue. A zero-byte file is selected as a file and then fails to decode. Restart EDOPro after changing cues so it reloads its file list. [EDOPro 41.0.2 sound manager](https://github.com/edo9300/edopro/blob/41.0.2/gframe/sound_manager.cpp#L112-L199) · [Format order](https://github.com/edo9300/edopro/blob/41.0.2/gframe/sound_backend.h#L20-L25) · [Decoder failure path](https://github.com/edo9300/edopro/blob/41.0.2/gframe/SoundBackends/miniaudio/sound_miniaudio.cpp#L133-L170)

| Stem | Name | Group | When it plays |
| --- | --- | --- | --- |
| `summon` | Normal Summon | Summoning | A monster is Normal Summoned. |
| `specialsummon` | Special Summon | Summoning | A monster is Special Summoned. |
| `flip` | Flip Summon | Summoning | A monster is Flip Summoned. |
| `token` | Token | Summoning | The client maps this slot. A trigger was not verified. |
| `attack` | Attack | Battle | A monster declares an attack. |
| `directattack` | Direct attack | Battle | The client maps this slot. The inspected attack handler plays Attack even on a direct attack. |
| `damage` | Damage | Life Points | A player takes damage or pays Life Points. |
| `gainlp` | Recover Life Points | Life Points | A player gains Life Points. |
| `activate` | Activate | Cards | A card or effect is activated. |
| `set` | Set | Cards | A card is Set on the field. |
| `reveal` | Reveal | Cards | The client maps this slot. A trigger was not verified. |
| `equip` | Equip | Cards | A card is equipped to another card. |
| `destroyed` | Destroy | Cards | A card is destroyed. |
| `banished` | Banish | Cards | A card is banished. |
| `draw` | Draw | Cards | A player draws cards. |
| `shuffle` | Shuffle | Cards | Cards are shuffled. |
| `addcounter` | Add counter | Cards | Counters are added to a card. |
| `removecounter` | Remove counter | Cards | Counters are removed from a card. |
| `coinflip` | Coin flip | Duel flow | A coin is flipped. |
| `diceroll` | Dice roll | Duel flow | Dice are rolled. |
| `nextturn` | Next turn | Duel flow | A new turn begins. |
| `phase` | Phase change | Duel flow | The duel enters a new phase. |
| `playerenter` | Player joins | Interface | A player joins the room. |
| `chatmessage` | Chat message | Interface | A room chat message arrives. |
| `activate/<passcode>` | Activate chant | Chants | A card-specific clip can replace Activate. |
| `attack/<passcode>` | Attack chant | Chants | A card-specific clip can replace Attack. |
| `summon/<passcode>` | Summon chant | Chants | A card-specific clip can replace the Normal, Special, and Flip Summon cues. |

Chants use the same four formats, but the client scans filenames in case-insensitive sort order. EDOPal writes one file per passcode in each supported folder. It reports recognizable nonstandard names such as `0012345 custom.wav` and refuses to change that chant slot until the filename is canonical. Removing a chant lets the corresponding general cue play. Removing a general cue still permits a configured chant. The installed guide documents 21 root cues and the three chant families. The tagged source and Thonk both include Token, Direct attack, and Reveal in addition. [Duel event handlers](https://github.com/edo9300/edopro/blob/41.0.2/gframe/duelclient.cpp#L3230-L3355) · [Attack handler](https://github.com/edo9300/edopro/blob/41.0.2/gframe/duelclient.cpp#L3744-L3774) · [Chat handler](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game.cpp#L2942-L3005)

## Sound-effects pack format

A portable set and a downloaded content pack use the same ZIP layout. Use the pack kind `sound-effects` in the downloader's catalogue and pass the resulting local path to `load_pack`.

```text
manifest.json
audio/draw.wav
audio/chant/summon/24550676.ogg
```

```json
{
  "kind": "sound-effects",
  "version": 1,
  "name": "My duel sounds",
  "credits": {
    "author": "Your name",
    "license": "CC0 1.0",
    "attribution": "Player recordings"
  },
  "events": {
    "draw": {"format": "wav"},
    "attack": null,
    "chant/summon/24550676": {"format": "ogg"}
  }
}
```

The UTF-8 manifest requires `kind`, `version`, `name`, and `events`. A file entry requires its lowercase `format` and can include its own `credits`. Clip credits are inherited from the set when absent. Use canonical event keys, forward slashes, lowercase member names, and decimal card passcodes from 1 to 4,294,967,295 without leading zeros. `null` means silence and has no ZIP member. Use ordinary stored or deflated ZIP members with no explicit directory entries or archive comment. The reader validates member names, rejects duplicates, links and unexpected data, and decodes from memory without extracting files.

Each clip is limited to 4 MiB and 10 seconds, mono or stereo, at 8–192 kHz. Use WAV (RIFF or RF64), MP3, Ogg Vorbis, or FLAC. EDOPal checks both container and decodable samples. Archives allow 256 assignments, a 64 KiB manifest, and 32 MiB of audio before compression.

## Read-only report

```powershell
python -m sounds --report 'C:\Games\ProjectIgnis'
```

The report lists the client-selected files, extra formats, validation errors, and SHA-256 content matches for corresponding events in the bundled and personal sets. Pass `--pack PATH` to include another local pack or `--library PATH` to inspect a different personal library. The report reads settings and audio and creates no state files.
