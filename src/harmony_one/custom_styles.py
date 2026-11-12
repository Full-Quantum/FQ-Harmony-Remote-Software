"""Render custom menu artwork without changing remote fonts or screen programs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from harmony_one.pictures import encode_rle_picture
from harmony_one.screen_styles import (
    BUTTON_ROWS, BUTTON_SIZE, SCREEN_SIZE, MenuArtwork, RemoteStyle,
    ScreenStyleError, create_artwork_style,
)
from harmony_one.slideshow import encode_raw_picture


@dataclass(frozen=True)
class MenuDesign:
    background: str = "Solid colour"
    colour: str = "#101820"
    second_colour: str = "#305080"
    gradient_direction: str = "Vertical"
    button_colour: str = "#263c56"
    corners: str = "Rounded"
    border: bool = True
    border_width: int = 1
    border_colour: str = "#80b8e8"


def _rgb(colour: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
        raise ScreenStyleError("Choose colours in #RRGGBB format.")
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _rgb565(colour: tuple[int, int, int]) -> int:
    red, green, blue = colour
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def render_menu_artwork(design: MenuDesign, image_rgba: bytes | None = None) -> MenuArtwork:
    """Build 176x220 background and 164x50 rows, preserving rounded transparency."""
    if design.background not in ("Solid colour", "Two-colour gradient", "Image"):
        raise ScreenStyleError("Choose a solid colour, two-colour gradient, or image background.")
    if design.gradient_direction not in ("Vertical", "Horizontal"):
        raise ScreenStyleError("Choose a vertical or horizontal gradient.")
    if design.corners not in ("Rounded", "Sharp"):
        raise ScreenStyleError("Choose rounded or sharp button corners.")
    if type(design.border_width) is not int or not 0 <= design.border_width <= 24:
        raise ScreenStyleError("Button border thickness must be a whole number from 0 to 24 pixels.")
    first, second = _rgb(design.colour), _rgb(design.second_colour)
    fill, border = _rgb565(_rgb(design.button_colour)), _rgb565(_rgb(design.border_colour))
    width, height = SCREEN_SIZE
    if design.background == "Image":
        if image_rgba is None or len(image_rgba) != width * height * 4:
            raise ScreenStyleError("Upload a background image before previewing or saving this style.")
    background = []
    for y in range(height):
        row = []
        for x in range(width):
            if design.background == "Two-colour gradient":
                step, limit = (y, height - 1) if design.gradient_direction == "Vertical" else (x, width - 1)
                rgb = tuple((a * (limit - step) + b * step) // limit for a, b in zip(first, second))
                row.append((rgb[0], rgb[1], rgb[2]))
            elif design.background == "Image" and image_rgba is not None:
                offset = (y * width + x) * 4
                red, green, blue, alpha = image_rgba[offset:offset + 4]
                row.append(tuple(
                    (channel * alpha + base * (255 - alpha)) // 255
                    for channel, base in zip((red, green, blue), first)
                ))
            else:
                row.append(first)
        background.append(row)
    button_width, button_height = BUTTON_SIZE
    radius = 10 if design.corners == "Rounded" else 0
    thickness = design.border_width if design.border else 0

    def inside(x: int, y: int, inset: int) -> bool:
        left, top = inset, inset
        right, bottom = button_width - inset, button_height - inset
        if not (left <= x < right and top <= y < bottom):
            return False
        r = max(0, radius - inset)
        if not r:
            return True
        centre_x = min(max(x + 0.5, left + r), right - r)
        centre_y = min(max(y + 0.5, top + r), bottom - r)
        return (x + 0.5 - centre_x) ** 2 + (y + 0.5 - centre_y) ** 2 <= r * r

    button = encode_rle_picture([
        [
            None if not inside(x, y, 0)
            else border if thickness and not inside(x, y, thickness)
            else fill
            for x in range(button_width)
        ]
        for y in range(button_height)
    ])
    return MenuArtwork(
        encode_raw_picture(background), {row: button for row in BUTTON_ROWS}, {}
    )


def build_custom_style(
    name: str,
    activities: MenuDesign,
    devices: MenuDesign,
    activity_image: bytes | None = None,
    device_image: bytes | None = None,
) -> RemoteStyle:
    if not name.strip():
        raise ScreenStyleError("Enter a name for your custom style.")
    return create_artwork_style(
        render_menu_artwork(activities, activity_image),
        render_menu_artwork(devices, device_image),
        name.strip(),
    )
