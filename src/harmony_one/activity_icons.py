"""Local activity icon choices and bounded image import; not a remote encoder."""

from __future__ import annotations

import base64
import binascii
import struct
import tkinter as tk
from pathlib import Path
from typing import Any


ICON_SIZE = 32
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_DIMENSION = 1024
BUILTIN_ICONS = {
    "tv": "TV",
    "movie": "Movie",
    "music": "Music",
    "game": "Game",
}


class IconError(ValueError):
    """Raised when an icon is malformed or cannot be imported."""


def validate_icon(icon: Any) -> None:
    if not isinstance(icon, dict):
        raise IconError("Activity icon must be an object")
    if icon.get("kind") == "builtin" and set(icon) == {"kind", "name"}:
        if isinstance(icon.get("name"), str) and icon["name"] in BUILTIN_ICONS:
            return
    elif icon.get("kind") == "custom" and set(icon) == {"kind", "rgba"}:
        encoded = icon.get("rgba")
        if isinstance(encoded, str) and len(encoded) == 5464:
            try:
                pixels = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                pass
            else:
                if len(pixels) == ICON_SIZE * ICON_SIZE * 4 and all(
                    pixels[i] in (0, 255) for i in range(3, len(pixels), 4)
                ):
                    return
    raise IconError("Invalid activity icon")


def builtin_pixels(name: str) -> bytes:
    if name not in BUILTIN_ICONS:
        raise IconError("Unknown built-in icon")
    pixels = bytearray(ICON_SIZE * ICON_SIZE * 4)

    def rectangle(x1: int, y1: int, x2: int, y2: int, colour: tuple[int, int, int, int]) -> None:
        for y in range(y1, y2):
            for x in range(x1, x2):
                offset = (y * ICON_SIZE + x) * 4
                pixels[offset:offset + 4] = bytes(colour)

    blue = (31, 112, 163, 255)
    light = (186, 230, 250, 255)
    if name == "tv":
        rectangle(2, 4, 30, 24, blue)
        rectangle(5, 7, 27, 21, light)
        rectangle(14, 24, 18, 28, blue)
        rectangle(9, 28, 23, 30, blue)
    elif name == "movie":
        rectangle(3, 5, 29, 27, blue)
        rectangle(8, 8, 24, 24, light)
        for y in (8, 13, 18, 23):
            rectangle(4, y, 7, y + 2, light)
            rectangle(25, y, 28, y + 2, light)
    elif name == "music":
        rectangle(19, 5, 23, 24, blue)
        rectangle(12, 4, 23, 8, blue)
        rectangle(8, 21, 20, 27, blue)
        rectangle(7, 22, 21, 26, blue)
    else:
        rectangle(4, 10, 28, 24, blue)
        rectangle(2, 14, 30, 21, blue)
        rectangle(9, 13, 12, 21, light)
        rectangle(6, 16, 15, 18, light)
        rectangle(21, 14, 24, 17, light)
        rectangle(24, 18, 27, 21, light)
    return bytes(pixels)


def icon_pixels(icon: dict[str, Any]) -> bytes:
    validate_icon(icon)
    if icon["kind"] == "builtin":
        return builtin_pixels(icon["name"])
    return base64.b64decode(icon["rgba"], validate=True)


def import_icon(path: Path, master: tk.Misc) -> dict[str, str]:
    try:
        with path.open("rb") as file:
            data = file.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise IconError(f"Could not read image: {error}") from error
    if len(data) > MAX_FILE_BYTES:
        raise IconError("Image file exceeds the 2 MiB limit.")
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        image_format = "png"
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        image_format = "gif"
    else:
        raise IconError("Choose a valid PNG or GIF image.")
    if not 1 <= width <= MAX_SOURCE_DIMENSION or not 1 <= height <= MAX_SOURCE_DIMENSION:
        raise IconError("Image dimensions must be between 1 and 1024 pixels on each side.")
    try:
        image = tk.PhotoImage(master=master, data=base64.b64encode(data), format=image_format)
    except tk.TclError as error:
        raise IconError(f"Could not decode image: {error}") from error
    if (image.width(), image.height()) != (width, height):
        raise IconError("Image dimensions do not match its header.")

    scale = min(ICON_SIZE / width, ICON_SIZE / height)
    scaled_width = max(1, round(width * scale))
    scaled_height = max(1, round(height * scale))
    left = (ICON_SIZE - scaled_width) // 2
    top = (ICON_SIZE - scaled_height) // 2
    pixels = bytearray(ICON_SIZE * ICON_SIZE * 4)
    for y in range(scaled_height):
        source_y = min(height - 1, y * height // scaled_height)
        for x in range(scaled_width):
            source_x = min(width - 1, x * width // scaled_width)
            if image.transparency_get(source_x, source_y):
                continue
            rgb = image.get(source_x, source_y)
            offset = ((top + y) * ICON_SIZE + left + x) * 4
            pixels[offset:offset + 4] = bytes((*rgb, 255))
    return {"kind": "custom", "rgba": base64.b64encode(pixels).decode("ascii")}


def make_preview(master: tk.Misc, icon: dict[str, Any]) -> tk.PhotoImage:
    pixels = icon_pixels(icon)
    image = tk.PhotoImage(master=master, width=ICON_SIZE, height=ICON_SIZE)
    rows = []
    for y in range(ICON_SIZE):
        colours = []
        for x in range(ICON_SIZE):
            offset = (y * ICON_SIZE + x) * 4
            colours.append("#%02x%02x%02x" % tuple(pixels[offset:offset + 3]))
        rows.append("{" + " ".join(colours) + "}")
    image.put(" ".join(rows))
    for y in range(ICON_SIZE):
        for x in range(ICON_SIZE):
            if not pixels[(y * ICON_SIZE + x) * 4 + 3]:
                image.transparency_set(x, y, True)
    return image
