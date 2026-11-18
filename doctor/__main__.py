"""Run the doctor and print a privacy-filtered report."""

import argparse
from pathlib import Path

from config import APP_NAME

from . import inspect_installation, support_summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check a local EDOPro installation without changing it."
    )
    parser.add_argument("installation", type=Path)
    parser.add_argument(
        "--config", type=Path, help=f"Read {APP_NAME} state beside this config file."
    )
    args = parser.parse_args()
    report = inspect_installation(args.installation, config_path=args.config)
    print(support_summary(report))


if __name__ == "__main__":
    main()
