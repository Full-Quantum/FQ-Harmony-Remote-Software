"""Decoded Harmony One activity/device menus for the Home screen preview."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.activities import read_activities, read_devices
from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.pictures import PictureError, decode_picture, encode_png, picture_at
from harmony_one.screen_styles import (
    BUTTON_ROWS,
    BUTTON_X,
    ICON_X,
    MenuPage,
    RemoteStyle,
    ScreenStyleError,
    _find_picture_bank,
    _image_bytes,
    _menu_pages,
    _normalized_text,
    inspect_remote_style,
    load_builtin_styles,
    load_user_styles,
)


@dataclass(frozen=True)
class RemotePreviewRow:
    y: int
    name: str
    button: bytes
    icon: bytes | None


@dataclass(frozen=True)
class RemotePreviewPage:
    role: str
    mode: int
    page: int
    background: bytes
    rows: tuple[RemotePreviewRow, ...]


@dataclass(frozen=True)
class RemotePreview:
    pages: tuple[RemotePreviewPage, ...]
    style_name: str
    style_fingerprint: str


def _named_style(style: RemoteStyle) -> str:
    libraries = [load_builtin_styles()]
    try:
        libraries.append(load_user_styles())
    except ScreenStyleError as error:
        return f"Custom / unrecognized (saved styles unavailable: {error})"
    for library in libraries:
        for saved_style in library.values():
            if saved_style.fingerprint == style.fingerprint:
                return saved_style.name
    return "Custom / unrecognized"


def read_remote_preview(blob: bytes) -> RemotePreview:
    """Decode menu rows and their exact artwork from a validated remote backup."""
    try:
        validate_image(blob)
        config = parse_config(blob)
        bank = _find_picture_bank(blob, config)
        pages = _menu_pages(blob, config, bank)
        activity_names = {
            _normalized_text(item.name): item.name for item in read_activities(blob, config)
        }
        device_names = {
            _normalized_text(item.label): item.label for item in read_devices(blob, config)
        }
        style = inspect_remote_style(blob)
        style_name = _named_style(style)
    except (ConfigError, PictureError, ValueError, IndexError) as error:
        if isinstance(error, ScreenStyleError):
            raise
        raise ScreenStyleError(f"Could not create remote preview: {error}") from error

    preview_pages = tuple(
        _preview_page(blob, page, activity_names if page.role == "activities" else device_names)
        for page in sorted(pages, key=lambda item: (item.role, item.mode, item.page))
    )
    preview_pages = _consistent_device_pages(preview_pages, device_names)
    if not preview_pages:
        raise ScreenStyleError("No recognizable activity or device menu pages were found.")
    return RemotePreview(preview_pages, style_name, style.fingerprint)


def _preview_page(
    blob: bytes, page: MenuPage, names: dict[str, str]
) -> RemotePreviewPage:
    rows = []
    for y in BUTTON_ROWS:
        normalized_name = page.items.get(y)
        button_draw = page.buttons.get(y)
        if normalized_name is None or button_draw is None:
            continue
        icon_draw = page.icons.get(y)
        rows.append(
            RemotePreviewRow(
                y,
                names.get(normalized_name, normalized_name),
                _image_bytes(blob, button_draw),
                _image_bytes(blob, icon_draw) if icon_draw is not None else None,
            )
        )
    return RemotePreviewPage(
        page.role,
        page.mode,
        page.page,
        _image_bytes(blob, page.background),
        tuple(rows),
    )


def _consistent_device_pages(
    pages: tuple[RemotePreviewPage, ...], device_names: dict[str, str]
) -> tuple[RemotePreviewPage, ...]:
    """Keep the mode whose pages cover the greatest number of decoded devices."""
    modes: dict[int, list[RemotePreviewPage]] = {}
    for page in pages:
        if page.role == "devices":
            modes.setdefault(page.mode, []).append(page)
    if not modes:
        return pages

    mode = max(
        modes,
        key=lambda value: (
            len(
                {
                    _normalized_text(row.name)
                    for page in modes[value]
                    for row in page.rows
                    if _normalized_text(row.name) in device_names
                }
            ),
            -value,
        ),
    )
    return tuple(
        page for page in pages if page.role != "devices" or page.mode == mode
    )


def render_remote_preview_page(page: RemotePreviewPage) -> bytes:
    """Render the remote's actual menu background, row artwork, and icons as PNG."""
    background = picture_at(page.background, 0, len(page.background))
    if background is None or (background.width, background.height) != (176, 220):
        raise ScreenStyleError("Remote preview background is not a complete 176 x 220 image.")
    pixels = decode_picture(page.background, background)

    def composite(image: bytes, x: int, y: int) -> None:
        picture = picture_at(image, 0, len(image))
        if picture is None or picture.end != len(image):
            raise ScreenStyleError("Remote preview row artwork is incomplete.")
        if x < 0 or y < 0 or x + picture.width > 176 or y + picture.height > 220:
            raise ScreenStyleError("Remote preview row artwork is outside the screen.")
        for image_y, row in enumerate(decode_picture(image, picture)):
            for image_x, pixel in enumerate(row):
                if pixel is not None:
                    pixels[y + image_y][x + image_x] = pixel

    for row in page.rows:
        composite(row.button, BUTTON_X, row.y)
        if row.icon is not None:
            composite(row.icon, ICON_X, row.y + 1)
    return encode_png(pixels)
