#!/usr/bin/env python3
"""Import parsed Flipper-IRDB IR files into a local JSON catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


class IRImportError(ValueError):
    """Raised when an IR file cannot be imported without losing information."""


REQUIRED_HEADER = {"Filetype": "IR signals file", "Version": "1"}
REQUIRED_SIGNAL_FIELDS = ("name", "type", "protocol", "address", "command")


def parse_ir(text: str) -> list[dict[str, str]]:
    header: dict[str, str] = {}
    signals: list[dict[str, str]] = []
    current: dict[str, str] = {}
    in_signals = False

    def finish_signal(line_number: int) -> None:
        nonlocal current
        if not current:
            return
        missing = [field for field in REQUIRED_SIGNAL_FIELDS if field not in current]
        if missing:
            raise IRImportError(
                f"Signal ending before line {line_number} is missing: {', '.join(missing)}"
            )
        if current["type"] != "parsed":
            raise IRImportError(
                f"Unsupported signal type {current['type']!r} before line {line_number}; "
                "only parsed signals are supported"
            )
        signals.append(current)
        current = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise IRImportError(f"Expected a key/value field on line {line_number}")

        key, value = (part.strip() for part in line.split(":", 1))
        if not key or not value:
            raise IRImportError(f"Empty key or value on line {line_number}")

        if not in_signals:
            if key == "name":
                in_signals = True
            else:
                if key in header:
                    raise IRImportError(f"Duplicate header field {key!r} on line {line_number}")
                header[key] = value
                continue

        if key == "name":
            finish_signal(line_number)
        if key in current:
            raise IRImportError(f"Duplicate signal field {key!r} on line {line_number}")
        if key not in REQUIRED_SIGNAL_FIELDS:
            raise IRImportError(f"Unsupported signal field {key!r} on line {line_number}")
        if key == "type" and value != "parsed":
            raise IRImportError(
                f"Unsupported signal type {value!r} on line {line_number}; "
                "only parsed signals are supported"
            )
        current[key] = value

    finish_signal(len(text.splitlines()) + 1)
    if not signals:
        raise IRImportError("No signals found")
    for key, expected in REQUIRED_HEADER.items():
        if header.get(key) != expected:
            raise IRImportError(
                f"Expected header {key}: {expected}, found {header.get(key)!r}"
            )
    return signals


def import_file(source: Path) -> dict[str, object]:
    signals = parse_ir(source.read_text(encoding="utf-8"))
    return {
        "schemaVersion": 1,
        "device": {
            "name": source.stem.replace("_", " "),
            "category": source.parent.parent.name,
            "brand": source.parent.name,
        },
        "source": source.as_posix(),
        "signals": signals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Flipper .ir input file")
    parser.add_argument("output", type=Path, help="JSON catalog output file")
    args = parser.parse_args()

    catalog = import_file(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Imported {len(catalog['signals'])} signals from {args.input}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
