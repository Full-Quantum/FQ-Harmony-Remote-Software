"""Convert the app's local icon previews into Harmony One menu pictures."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from typing import Any

from harmony_one.activity_icons import (
    ICON_SIZE,
    IconError,
    icon_pixels,
    import_rgba_image,
)
from harmony_one.pictures import PictureError, encode_rle_picture, picture_at

REMOTE_ICON_SIZE = (51, 48)


def encode_remote_menu_icon(icon: dict[str, Any]) -> bytes:
    """Fit a validated 32 x 32 RGBA icon into one transparent 51 x 48 picture."""
    rgba = icon_pixels(icon)
    return encode_remote_menu_icon_rgba(rgba, (ICON_SIZE, ICON_SIZE))


def encode_remote_menu_icon_rgba(
    rgba: bytes, source_size: tuple[int, int]
) -> bytes:
    source_width, source_height = source_size
    if not 1 <= source_width <= 256 or not 1 <= source_height <= 256:
        raise IconError("Source image dimensions are invalid.")
    expected_size = source_width * source_height * 4
    if len(rgba) != expected_size:
        raise IconError("Custom icon pixels have an invalid size.")
    if any(rgba[index] not in (0, 255) for index in range(3, len(rgba), 4)):
        raise IconError("Custom icon transparency must be fully opaque or transparent.")

    width, height = REMOTE_ICON_SIZE
    scale = min(width / source_width, height / source_height)
    scaled_width = max(1, round(source_width * scale))
    scaled_height = max(1, round(source_height * scale))
    left = (width - scaled_width) // 2
    top = (height - scaled_height) // 2
    rows: list[list[int | None]] = []
    for y in range(height):
        row: list[int | None] = [None] * width
        if top <= y < top + scaled_height:
            source_y = (y - top) * source_height // scaled_height
            for x in range(left, left + scaled_width):
                source_x = (x - left) * source_width // scaled_width
                offset = (source_y * source_width + source_x) * 4
                if rgba[offset + 3] == 255:
                    red, green, blue = rgba[offset : offset + 3]
                    row[x] = (
                        ((red >> 3) << 11)
                        | ((green >> 2) << 5)
                        | (blue >> 3)
                    )
        rows.append(row)

    result = encode_rle_picture(rows)
    picture = picture_at(result, 0, len(result))
    if picture is None or picture.end != len(result) or (picture.width, picture.height) != REMOTE_ICON_SIZE:
        raise PictureError("Generated Harmony One icon did not validate as a 51 x 48 picture")
    return result


def import_remote_menu_icon(path: Path, master: tk.Misc) -> bytes:
    """Import a bounded PNG/GIF and convert it to the Harmony One menu-icon format."""
    rgba = import_rgba_image(path, master, REMOTE_ICON_SIZE)
    return encode_remote_menu_icon_rgba(rgba, REMOTE_ICON_SIZE)
