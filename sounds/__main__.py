"""Read-only inspection of an installed EDOPro sound directory."""

import argparse
from pathlib import Path

from .archive import SoundLibrary, load_pack
from .sets import bundled_sets, scan_installation
from .settings import sound_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path, metavar="INSTALLATION")
    parser.add_argument(
        "--pack", action="append", type=Path, default=[], help="Include a local pack"
    )
    parser.add_argument("--library", type=Path, help="Read sets from this personal library folder")
    args = parser.parse_args()
    try:
        root = args.report.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Choose an EDOPro installation folder.")
        rows = scan_installation(root)
        settings = sound_settings(root)
        saved = SoundLibrary(args.library).list()
        known = [*bundled_sets(), *(load_pack(path) for path in args.pack)]
        known.extend(item.sound_set for item in saved if item.sound_set)
        enabled = {True: "enabled", False: "disabled", None: "unknown"}[settings.enabled]
        volume = f"{settings.volume}%" if settings.volume is not None else "unknown"
        print(f"Installation: {root}")
        print(
            f"Sound effects: {enabled} | Volume: {volume} | Backend: {settings.backend or 'unknown'}"
        )
        print(f"Card chants: {sum(row.key.startswith('chant/') for row in rows)}")
        for item in saved:
            if item.error:
                print(f"Could not read personal set {item.id}: {item.error}")
        print("Event\tInstalled file\tFormat\tDuration\tBytes\tContent matches")
        for row in rows:
            if row.path is None:
                print(f"{row.key}\t-\t-\t-\t-\t-")
                continue
            format = row.path.suffix[1:].upper()
            clip = row.clip
            matches = [
                pack.name
                for pack in known
                if (candidate := pack.assignments.get(row.key)) is not None
                and clip is not None
                and clip.sha256 == candidate.sha256
            ]
            size = row.size if row.size is not None else "-"
            duration = f"{row.duration:.3f} s" if row.duration is not None else "-"
            print(
                f"{row.key}\t{row.path.relative_to(root).as_posix()}\t{format}\t{duration}\t"
                f"{size}\t{', '.join(matches) or '-'}"
            )
            for path in row.paths[1:]:
                print(f"  Extra file: {path.relative_to(root).as_posix()}")
            if row.error:
                print(f"  {row.error}")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
