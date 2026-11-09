#!/usr/bin/env python3
"""Export every picture in a Harmony One backup's picture bank as PNG files (read-only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harmony_one.config_reader import parse_config  # noqa: E402
from harmony_one.pictures import decode_picture, find_picture_bank, write_png  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("output", type=Path, help="folder to create PNG files in")
    args = parser.parse_args()

    data = args.backup.read_bytes()
    config = parse_config(data)
    last_section = max(config.sections)
    bank = find_picture_bank(data, config.sections[last_section][0])
    args.output.mkdir(parents=True, exist_ok=True)
    for index, picture in enumerate(bank):
        name = f"{index:03d}_{picture.width}x{picture.height}_k{picture.kind}_{picture.offset}.png"
        write_png(args.output / name, decode_picture(data, picture))
    print(f"{len(bank)} pictures written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
