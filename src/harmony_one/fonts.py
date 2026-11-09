"""Read-only font-table decoder for a Harmony One configuration (base slot 7).

Format knowledge: public documentation (dannybloe/harmony-explorations, MIT).
A font set is `u8 height, u8 first code, u8 count, u24 glyph[count]` (when count is 0
the count is the second byte and the first code is 1). A glyph is `u8 width` then
byte operations: 0x00 ends, bit 7 set skips that many pixels, below 0x80 introduces
that many two-byte RGB565 pixels (high byte first).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from harmony_one.config_reader import RemoteConfig, raw_slot_for_base_slot

FONT_BASE_SLOT = 7


@dataclass(frozen=True)
class FontSet:
    index: int
    height: int
    first_code: int
    glyph_offsets: tuple[int | None, ...]


def _absolute(config: RemoteConfig, address: int) -> int | None:
    return address - config.base_address if address else None


def read_font_sets(data: bytes, config: RemoteConfig) -> list[FontSet]:
    section = config.sections.get(raw_slot_for_base_slot(FONT_BASE_SLOT))
    if not section:
        return []
    start, end = section
    count = struct.unpack_from("<H", data, start)[0]
    sets: list[FontSet] = []
    for index in range(count):
        entry = start + 2 + 3 * index
        address = int.from_bytes(data[entry : entry + 3], "little")
        position = _absolute(config, address)
        if position is None or not 0 <= position < len(data):
            sets.append(FontSet(index, 0, 0, ()))
            continue
        height, first, glyph_count = data[position], data[position + 1], data[position + 2]
        table = position + 3
        if glyph_count == 0:
            glyph_count, first = data[position + 1], 1
        offsets = []
        for slot in range(glyph_count):
            raw = int.from_bytes(data[table + 3 * slot : table + 3 * slot + 3], "little")
            offsets.append(_absolute(config, raw))
        sets.append(FontSet(index, height, first, tuple(offsets)))
    return sets


def decode_glyph(data: bytes, offset: int, height: int):
    """Return (width, rows) where each row is a list of (r, g, b, 255) or None."""
    width = data[offset]
    position = offset + 1
    pixels: list = []
    limit = width * height
    while len(pixels) < limit:
        control = data[position]
        position += 1
        if control == 0:
            break
        if control & 0x80:
            pixels.extend([None] * (control & 0x7F))
        else:
            for _ in range(control):
                value = (data[position] << 8) | data[position + 1]
                position += 2
                r, g, b = (value >> 11) & 0x1F, (value >> 5) & 0x3F, value & 0x1F
                pixels.append((r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2, 255))
    pixels.extend([None] * (limit - len(pixels)))
    rows = [pixels[row * width : (row + 1) * width] for row in range(height)]
    return width, rows
