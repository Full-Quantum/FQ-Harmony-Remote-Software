"""Read-only explorer for a Harmony One GSPM configuration backup (.EZHex).

Prints the section offset table and, optionally, named strings in a byte range.
This is a research aid for the clean-room rebuild; it never writes to the file
or to the remote.

Usage: python tools/inspect_backup.py BACKUP [START END]
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path


def section_offsets(data: bytes) -> list[int]:
    offsets: list[int] = []
    index = 3
    while index * 4 + 4 <= len(data):
        value = struct.unpack_from("<I", data, index * 4)[0]
        if value == 0:
            break
        offsets.append(value)
        index += 1
    return offsets


def main() -> int:
    data = Path(sys.argv[1]).read_bytes()
    print(f"size={len(data)} magic={data[:4]!r}")
    print("header words:", [hex(w) for w in struct.unpack_from("<12I", data, 0)])
    offsets = section_offsets(data)
    print(f"{len(offsets)} section offsets")
    bounds = offsets + [len(data)]
    for number, start in enumerate(offsets):
        end = bounds[number + 1]
        print(f"  section {number:2d}: 0x{start:06x}..0x{end:06x}  {end - start:8d} bytes")
    if len(sys.argv) > 3:
        low, high = int(sys.argv[2], 0), int(sys.argv[3], 0)
        for match in re.finditer(rb"[A-Za-z][A-Za-z0-9_/&.\- ]{3,80}", data[low:high]):
            print(f"{low + match.start():8d}: {match.group().decode()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
