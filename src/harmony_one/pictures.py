"""Read-only picture-bank decoder for a Harmony One configuration.

Format knowledge: public documentation (dannybloe/harmony-explorations, MIT).
A picture is `u8 kind, u16 stride, u16 rows` then pixels. A pixel is RGB565 stored
high byte first. Kind 0 is raw; kind 1 is run-length encoded where an undrawn
(transparent) run is `0x80 | n`, a drawn run is `n` then n pixels, `0x80` ends a row
and `0x00` ends the picture.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

PICTURE_CEILING = 256
HEADER_SIZE = 5


class PictureError(ValueError):
    """Raised when picture data cannot be decoded."""


@dataclass(frozen=True)
class Picture:
    offset: int
    kind: int
    width: int
    height: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


def _encoded_size(data: bytes, start: int, limit: int, rows: int, stride: int) -> int | None:
    """Length of a kind 1 picture, or None when the stream is not well formed."""
    position = start + HEADER_SIZE
    breaks = 0
    column = 0
    while position < limit:
        control = data[position]
        position += 1
        if control == 0:
            return position - start if breaks == rows - 1 else None
        if control == 0x80:
            breaks += 1
            column = 0
            if breaks > rows - 1:
                return None
        elif control & 0x80:
            column += control & 0x7F
        else:
            column += control
            position += 2 * control
        if column > stride:
            return None
    return None


def picture_at(data: bytes, offset: int, limit: int) -> Picture | None:
    """Measure one picture at an offset, or return None if none starts there."""
    if offset + HEADER_SIZE > limit:
        return None
    kind = data[offset]
    stride, rows = struct.unpack_from("<HH", data, offset + 1)
    if kind > 1 or not 0 < stride <= PICTURE_CEILING or not 0 < rows <= PICTURE_CEILING:
        return None
    if kind == 0:
        size = HEADER_SIZE + 2 * stride * rows
    else:
        size = _encoded_size(data, offset, limit, rows, stride)
    if size is None or offset + size > limit:
        return None
    return Picture(offset, kind, stride, rows, size)


def walk_bank(data: bytes, start: int, end: int) -> list[Picture] | None:
    """Walk pictures from start; return them only when they land exactly on end."""
    pictures: list[Picture] = []
    position = start
    while position < end:
        picture = picture_at(data, position, end)
        if picture is None:
            return None
        pictures.append(picture)
        position = picture.end
    return pictures if position == end and pictures else None


def find_picture_bank(data: bytes, search_from: int, trailer: int | None = None) -> list[Picture]:
    """Find the picture bank: the earliest walk from search_from that lands on the trailer."""
    end = len(data) - 6 if trailer is None else trailer
    best: list[Picture] = []
    for start in range(search_from, end):
        if data[start] > 1:
            continue
        pictures = walk_bank(data, start, end)
        if pictures and len(pictures) > len(best):
            best = pictures
    return best


def decode_picture(data: bytes, picture: Picture) -> list[list[tuple[int, int, int, int] | None]]:
    """Decode to rows of (r, g, b, 255) pixels, with None for transparent pixels."""
    width, height = picture.width, picture.height
    position = picture.offset + HEADER_SIZE

    def colour(high: int, low: int) -> tuple[int, int, int, int]:
        value = (high << 8) | low
        r = (value >> 11) & 0x1F
        g = (value >> 5) & 0x3F
        b = value & 0x1F
        return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2, 255)

    rows: list[list[tuple[int, int, int, int] | None]] = []
    if picture.kind == 0:
        for _ in range(height):
            row = []
            for _ in range(width):
                row.append(colour(data[position], data[position + 1]))
                position += 2
            rows.append(row)
        return rows

    row: list[tuple[int, int, int, int] | None] = []
    while True:
        control = data[position]
        position += 1
        if control == 0:
            break
        if control == 0x80:
            rows.append(row + [None] * (width - len(row)))
            row = []
        elif control & 0x80:
            row.extend([None] * (control & 0x7F))
        else:
            for _ in range(control):
                row.append(colour(data[position], data[position + 1]))
                position += 2
    rows.append(row + [None] * (width - len(row)))
    if len(rows) != height:
        raise PictureError("Encoded picture row count does not match its header")
    return rows


def encode_rle_picture(pixels: list[list[int | None]]) -> bytes:
    """Encode RGB565 values with transparent pixels as a kind 1 picture."""
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    if not 1 <= width <= PICTURE_CEILING or not 1 <= height <= PICTURE_CEILING:
        raise PictureError("Picture dimensions must be between 1 and 256 pixels.")
    if any(len(row) != width for row in pixels):
        raise PictureError("Picture rows must all have the same width.")
    body = bytearray((1,))
    body.extend(struct.pack("<HH", width, height))
    for row_index, row in enumerate(pixels):
        position = 0
        while position < width:
            transparent = row[position] is None
            end = position + 1
            while end < width and (row[end] is None) == transparent and end - position < 127:
                end += 1
            run = end - position
            if transparent:
                body.append(0x80 | run)
            else:
                body.append(run)
                for pixel in row[position:end]:
                    if pixel is None or not 0 <= pixel <= 0xFFFF:
                        raise PictureError("Picture pixels must be RGB565 values or transparent.")
                    body.extend(pixel.to_bytes(2, "big"))
            position = end
        if row_index < height - 1:
            body.append(0x80)
    body.append(0)
    return bytes(body)


def encode_png(pixels: list[list[tuple[int, int, int, int] | None]]) -> bytes:
    """Encode RGBA pixels as PNG bytes without third-party libraries."""
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for pixel in row:
            raw.extend(pixel if pixel else (0, 0, 0, 0))

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    return png


def write_png(path, pixels: list[list[tuple[int, int, int, int] | None]]) -> None:
    """Write RGBA pixels as a PNG without third-party libraries."""
    with open(path, "wb") as handle:
        handle.write(encode_png(pixels))
