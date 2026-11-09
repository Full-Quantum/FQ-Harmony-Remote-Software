"""Same-size picture replacement inside a configuration, with the trailer checksum refreshed.

Replacing a raw picture with another raw picture of the same dimensions changes no section
size and no address, so no relocation is needed. Anything else is refused here.
"""

from __future__ import annotations

from harmony_one.harmony_usb import TRAILER_CHECKSUM_OFFSET, trailer_checksum
from harmony_one.pictures import Picture, decode_picture, find_picture_bank
import struct
from typing import Callable

SCREEN_WIDTH = 176
SCREEN_HEIGHT = 220

Pixels = list[list[tuple[int, int, int]]]


class PictureReplaceError(ValueError):
    pass


def encode_raw_picture(pixels: Pixels) -> bytes:
    """Encode rows of (r, g, b) as a raw (kind 0) picture: RGB565, high byte first."""
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    if not width or any(len(row) != width for row in pixels):
        raise PictureReplaceError("image rows must be non-empty and all the same width")
    body = bytearray()
    for row in pixels:
        for r, g, b in row:
            value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            body += value.to_bytes(2, "big")
    return b"\x00" + struct.pack("<HH", width, height) + bytes(body)


def fit_cover(
    source_width: int,
    source_height: int,
    get_pixel: Callable[[int, int], tuple[int, int, int]],
    width: int = SCREEN_WIDTH,
    height: int = SCREEN_HEIGHT,
) -> Pixels:
    """Scale to cover width x height (nearest neighbour), cropping the overflow evenly."""
    if source_width < 1 or source_height < 1:
        raise PictureReplaceError("image has no pixels")
    scale = max(width / source_width, height / source_height)
    crop_x = (source_width * scale - width) / 2
    crop_y = (source_height * scale - height) / 2
    rows: Pixels = []
    for y in range(height):
        sy = min(source_height - 1, int((y + crop_y) / scale))
        rows.append([get_pixel(min(source_width - 1, int((x + crop_x) / scale)), sy) for x in range(width)])
    return rows


def locate_bank(blob: bytes) -> list[Picture]:
    from harmony_one.config_reader import parse_config

    config = parse_config(blob)
    return find_picture_bank(blob, config.sections[max(config.sections)][0])


def full_screen_pictures(blob: bytes) -> list[tuple[int, Picture]]:
    """Raw 176x220 pictures (slideshow candidates) with their bank index."""
    return [
        (index, picture)
        for index, picture in enumerate(locate_bank(blob))
        if picture.kind == 0 and (picture.width, picture.height) == (SCREEN_WIDTH, SCREEN_HEIGHT)
    ]


def refresh_checksum(blob: bytearray) -> None:
    value = trailer_checksum(bytes(blob))
    position = len(blob) - TRAILER_CHECKSUM_OFFSET
    blob[position : position + 2] = value.to_bytes(2, "little")


def replace_with_encoded(blob: bytes, target: Picture, encoded: bytes) -> bytes:
    """Overwrite a raw picture with an equal-size raw encoding and refresh the checksum."""
    if target.kind != 0:
        raise PictureReplaceError("only raw (uncompressed) pictures can be replaced in place")
    if len(encoded) != target.size or encoded[0] != 0:
        raise PictureReplaceError("replacement picture must have exactly the same dimensions")
    out = bytearray(blob)
    out[target.offset : target.offset + target.size] = encoded
    refresh_checksum(out)
    return bytes(out)


def replace_picture(blob: bytes, target: Picture, donor_blob: bytes, donor: Picture) -> bytes:
    """Copy a raw picture from another configuration over ``target``; both must be raw and equal in size."""
    if donor.kind != 0:
        raise PictureReplaceError("only raw (uncompressed) pictures can be replaced in place")
    if (target.width, target.height, target.size) != (donor.width, donor.height, donor.size):
        raise PictureReplaceError("replacement picture must have exactly the same dimensions")
    return replace_with_encoded(blob, target, donor_blob[donor.offset : donor.offset + donor.size])

