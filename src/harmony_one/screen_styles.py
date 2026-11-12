"""Transfer Harmony One activity/device list artwork between local backups.

The converter only creates an offline candidate. It does not open USB or write to a remote.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import uuid
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harmony_one.activities import read_activities, read_devices
from harmony_one.app_paths import custom_style_library_path
from harmony_one.config_image import validate_image
from harmony_one.config_reader import (
    ConfigError,
    RemoteConfig,
    parse_config,
    raw_slot_for_base_slot,
)
from harmony_one.fonts import read_font_sets
from harmony_one.harmony_usb import CONFIG_FLASH_LIMIT, trailer_checksum
from harmony_one.pictures import Picture, find_picture_bank, picture_at, walk_bank
from harmony_one.screens import read_mode_pages
from harmony_one.text_decode import decode_text, learn_alphabet, load_bundled_alphabet

SCREEN_SIZE = (176, 220)
BUTTON_SIZE = (164, 50)
ICON_SIZE = (51, 48)
BUTTON_ROWS = (38, 92, 146)
ICON_ROWS = (39, 93, 147)
BUTTON_X = 6
ICON_X = 11
TEXT_TO_BUTTON_ROW = {57: 38, 111: 92, 165: 146}
MAX_SCREEN_INSTRUCTIONS = 200_000


class ScreenStyleError(ValueError):
    """A backup could not be safely classified or transformed."""


def _generic_menu_icon() -> bytes:
    width, height = ICON_SIZE
    pixels: list[list[int | None]] = [[None] * width for _ in range(height)]

    def rectangle(
        x1: int, y1: int, x2: int, y2: int, colour: int
    ) -> None:
        for y in range(y1, y2):
            for x in range(x1, x2):
                pixels[y][x] = colour

    for y in (7, 20, 33):
        rectangle(7, y, 14, y + 7, 0x3DFF)
        rectangle(20, y + 2, 44, y + 5, 0xFFFF)

    body = bytearray((1,))
    body.extend(struct.pack("<HH", width, height))
    for row_index, row in enumerate(pixels):
        position = 0
        while position < width:
            transparent = row[position] is None
            end = position + 1
            while (
                end < width
                and (row[end] is None) == transparent
                and end - position < 127
            ):
                end += 1
            run = end - position
            if transparent:
                body.append(0x80 | run)
            else:
                body.append(run)
                for colour in row[position:end]:
                    if colour is None:
                        raise ScreenStyleError("Generic menu icon run is malformed")
                    body.extend(colour.to_bytes(2, "big"))
            position = end
        if row_index < height - 1:
            body.append(0x80)
    body.append(0)
    return bytes(body)


GENERIC_MENU_ICON = _generic_menu_icon()


@dataclass(frozen=True)
class PictureDraw:
    instruction_offset: int
    pointer_offset: int
    address: int
    x: int
    y: int
    picture: Picture | None = None


@dataclass(frozen=True)
class MenuPage:
    role: str
    mode: int
    page: int
    background: PictureDraw
    buttons: dict[int, PictureDraw]
    icons: dict[int, PictureDraw]
    items: dict[int, str]
    labels: dict[int, str]


@dataclass(frozen=True)
class MenuArtwork:
    background: bytes
    buttons: dict[int, bytes]
    last_page_buttons: dict[int, bytes]


@dataclass(frozen=True)
class RemoteStyle:
    activity_menu: MenuArtwork
    device_menu: MenuArtwork
    fingerprint: str
    name: str = ""


@dataclass(frozen=True)
class StyleTransferResult:
    candidate: bytes
    changed_pages: int
    changed_references: int
    appended_pictures: int
    style_fingerprint: str


@dataclass(frozen=True)
class MenuIconOccurrence:
    role: str
    mode: int
    page: int
    row: int
    item_name: str
    picture_data: bytes
    button_data: bytes

    @property
    def description(self) -> str:
        title = "Activity" if self.role == "activities" else "Device"
        return f"{title}: {self.item_name} — mode {self.mode}, page {self.page + 1}, row {self.row}"


@dataclass(frozen=True)
class MenuIconCandidate:
    candidate: bytes
    occurrence: MenuIconOccurrence
    appended_picture: bool


def _u24(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 3 > len(data):
        raise ScreenStyleError("Screen program contains a truncated 24-bit address")
    return int.from_bytes(data[offset : offset + 3], "little")


def _read_inline_string_end(data: bytes, start: int) -> int:
    position = start
    while position < len(data):
        byte = data[position]
        if byte == 0:
            return position + 1
        position += 2 if byte & 0x80 else 1
    raise ScreenStyleError("Screen program contains an unterminated glyph string")


def _switch_targets(data: bytes, start: int, wide: bool) -> tuple[list[int], int]:
    width = 2 if wide else 1
    position = start + 1
    if position + width > len(data):
        raise ScreenStyleError("Screen switch has a truncated state variable")
    targets: list[int] = []
    for entry_width in (width + 3, 2 * width + 3):
        if position + width > len(data):
            raise ScreenStyleError("Screen switch has a truncated entry count")
        count = int.from_bytes(data[position : position + width], "little")
        position += width
        end = position + count * entry_width
        if end > len(data):
            raise ScreenStyleError("Screen switch entries extend beyond the configuration")
        for index in range(count):
            target_offset = position + index * entry_width + entry_width - 3
            targets.append(_u24(data, target_offset))
        position = end
    return targets, position


def read_picture_draws(
    data: bytes, config: RemoteConfig, root_address: int
) -> tuple[PictureDraw, ...]:
    """Return every opcode-2 picture draw reachable from one architecture-12 page program."""
    if config.architecture != 12:
        raise ScreenStyleError("Display-style transfer currently supports architecture 12 only")

    draws: list[PictureDraw] = []
    visited_programs: set[int] = set()
    instruction_count = 0

    def walk(address: int, depth: int) -> None:
        nonlocal instruction_count
        if depth > 64:
            raise ScreenStyleError("Screen program call depth exceeds the safe limit")
        if address in visited_programs:
            return
        visited_programs.add(address)
        position = address - config.base_address
        visited_instructions: set[int] = set()

        while 0 <= position < len(data):
            if position in visited_instructions:
                raise ScreenStyleError("Screen program contains an unbounded control-flow loop")
            visited_instructions.add(position)
            instruction_count += 1
            if instruction_count > MAX_SCREEN_INSTRUCTIONS:
                raise ScreenStyleError("Screen program exceeds the instruction safety limit")

            instruction_offset = position
            opcode = data[position]
            position += 1
            if opcode in (0, 23):
                return
            if opcode == 2:
                if position + 5 > len(data):
                    raise ScreenStyleError("Picture draw instruction is truncated")
                x, y = data[position], data[position + 1]
                pointer_offset = position + 2
                picture_address = _u24(data, pointer_offset)
                draws.append(
                    PictureDraw(
                        instruction_offset,
                        pointer_offset,
                        picture_address,
                        x,
                        y,
                    )
                )
                position += 5
                continue
            if opcode == 22:
                target = _u24(data, position)
                position += 3
                walk(target, depth + 1)
                continue
            if opcode == 20:
                walk(_u24(data, position), depth + 1)
                return
            if opcode in (18, 19):
                targets, _ = _switch_targets(data, position, opcode == 19)
                for target in targets:
                    walk(target, depth + 1)
                return
            if opcode == 5:
                if position + 2 > len(data):
                    raise ScreenStyleError("Inline text instruction is truncated")
                position = _read_inline_string_end(data, position + 2)
                continue

            fixed_lengths = {1: 6, 3: 9, 4: 5, 16: 1, 17: 3, 21: 4}
            operand_length = fixed_lengths.get(opcode)
            if operand_length is None:
                raise ScreenStyleError(
                    f"Unsupported screen opcode 0x{opcode:02x} at 0x{instruction_offset:x}"
                )
            if position + operand_length > len(data):
                raise ScreenStyleError(
                    f"Screen opcode 0x{opcode:02x} is truncated at 0x{instruction_offset:x}"
                )
            position += operand_length

        raise ScreenStyleError(f"Screen program at 0x{address:x} left the configuration")

    walk(root_address, 0)
    return tuple(draws)


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _picture_index(address: int, pictures: dict[int, Picture]) -> Picture:
    picture = pictures.get(address)
    if picture is None:
        raise ScreenStyleError(f"Screen refers to unknown picture address 0x{address:x}")
    return picture


def _draw_picture(draw: PictureDraw) -> Picture:
    if draw.picture is None:
        raise ScreenStyleError("Picture reference has not been resolved to a bank entry")
    return draw.picture


def _find_picture_bank(blob: bytes, config: RemoteConfig) -> list[Picture]:
    if not config.sections:
        raise ScreenStyleError("Configuration has no sections to locate its picture bank")
    bank = find_picture_bank(blob, config.sections[max(config.sections)][0])
    if not bank or bank[-1].end != len(blob) - 6:
        raise ScreenStyleError("The picture bank does not end at the validated trailer")
    return bank


def _menu_pages(
    blob: bytes, config: RemoteConfig, bank: list[Picture] | None = None
) -> list[MenuPage]:
    try:
        bank = bank if bank is not None else _find_picture_bank(blob, config)
        picture_by_address = {
            config.base_address + picture.offset: picture for picture in bank
        }
        activities = {_normalized_text(item.name) for item in read_activities(blob, config)}
        devices = {_normalized_text(item.label) for item in read_devices(blob, config)}
        alphabet = learn_alphabet(blob, config, load_bundled_alphabet())
        fonts = read_font_sets(blob, config)
        screen_pages = read_mode_pages(blob, config)
    except (ConfigError, ValueError, IndexError) as error:
        raise ScreenStyleError(f"Could not inspect menu screens: {error}") from error

    found: list[MenuPage] = []
    for screen_page in screen_pages:
        row_text_parts: dict[int, list[tuple[int, str]]] = {}
        for draw in screen_page.draws:
            baseline, button_row = min(
                (abs(draw.y - y), row)
                for y, row in TEXT_TO_BUTTON_ROW.items()
            )
            if baseline <= 12:
                row_text_parts.setdefault(button_row, []).append(
                    (draw.y, decode_text(blob, config, draw, alphabet, fonts))
                )
        row_texts = {
            row: _normalized_text(
                " ".join(
                    dict.fromkeys(
                        text
                        for _, text in sorted(parts)
                    )
                )
            )
            for row, parts in row_text_parts.items()
        }
        activity_rows = {
            row for row, value in row_texts.items() if value in activities
        }
        device_rows = {
            row for row, value in row_texts.items() if value in devices
        }
        activity_matches = len(activity_rows)
        device_matches = len(device_rows)
        if activity_matches == device_matches:
            continue
        role = "activities" if activity_matches > device_matches else "devices"
        role_rows = activity_rows if role == "activities" else device_rows
        role_items = {
            row: value for row, value in row_texts.items() if row in role_rows
        }
        row_labels = row_texts
        root_address = config.base_address + screen_page.program
        raw_draws = read_picture_draws(blob, config, root_address)

        resolved: list[PictureDraw] = []
        for draw in raw_draws:
            picture = _picture_index(draw.address, picture_by_address)
            resolved.append(
                PictureDraw(
                    draw.instruction_offset,
                    draw.pointer_offset,
                    draw.address,
                    draw.x,
                    draw.y,
                    picture,
                )
            )
        backgrounds = [
            draw
            for draw in resolved
            if (_draw_picture(draw).width, _draw_picture(draw).height) == SCREEN_SIZE
            and (draw.x, draw.y) == (0, 0)
        ]
        button_draws = [
            draw
            for draw in resolved
            if (_draw_picture(draw).width, _draw_picture(draw).height) == BUTTON_SIZE
            and draw.x == BUTTON_X
            and draw.y in BUTTON_ROWS
        ]
        icon_draws = [
            draw
            for draw in resolved
            if (_draw_picture(draw).width, _draw_picture(draw).height) == ICON_SIZE
            and draw.x == ICON_X
            and draw.y in ICON_ROWS
        ]
        buttons = {draw.y: draw for draw in button_draws}
        icons = {draw.y - 1: draw for draw in icon_draws}
        if (
            len(backgrounds) != 1
            or not buttons
            or not role_rows.issubset(buttons)
        ):
            continue
        found.append(
            MenuPage(
                role,
                screen_page.mode,
                screen_page.page,
                backgrounds[0],
                buttons,
                icons,
                role_items,
                row_labels,
            )
        )
    return found


def _image_bytes(blob: bytes, draw: PictureDraw) -> bytes:
    picture = _draw_picture(draw)
    return blob[picture.offset : picture.end]


def list_menu_icon_occurrences(blob: bytes) -> tuple[MenuIconOccurrence, ...]:
    """Return decoded activity/device menu rows that have individually editable icons."""
    try:
        validate_image(blob)
        config = parse_config(blob)
        bank = _find_picture_bank(blob, config)
        pages = _menu_pages(blob, config, bank)
        activities = {
            _normalized_text(item.name): item.name
            for item in read_activities(blob, config)
        }
        devices = {
            _normalized_text(item.label): item.label
            for item in read_devices(blob, config)
        }
    except (ConfigError, ValueError, IndexError) as error:
        raise ScreenStyleError(f"Could not inspect menu icons: {error}") from error

    occurrences = []
    for page in pages:
        names = activities if page.role == "activities" else devices
        for row, normalized_name in page.items.items():
            draw = page.icons.get(row)
            item_name = names.get(normalized_name)
            if draw is not None and item_name is not None:
                occurrences.append(
                    MenuIconOccurrence(
                        page.role,
                        page.mode,
                        page.page,
                        row,
                        item_name,
                        _image_bytes(blob, draw),
                        _image_bytes(blob, page.buttons[row]),
                    )
                )
    return tuple(occurrences)


def list_menu_icon_choices(blob: bytes) -> tuple[bytes, ...]:
    """Return distinct 51 x 48 images available in a validated backup's picture bank."""
    try:
        validate_image(blob)
        config = parse_config(blob)
        bank = _find_picture_bank(blob, config)
    except (ConfigError, ValueError, IndexError) as error:
        raise ScreenStyleError(f"Could not inspect backup icon images: {error}") from error
    choices: list[bytes] = []
    seen: set[bytes] = set()
    for picture in bank:
        if (picture.width, picture.height) != ICON_SIZE:
            continue
        image = blob[picture.offset : picture.end]
        if image not in seen:
            choices.append(image)
            seen.add(image)
    return tuple(choices)


def _majority_image(images: list[bytes], role: str, position: str) -> bytes:
    counts = Counter(images)
    if not counts:
        raise ScreenStyleError(f"No {position} artwork was found for the {role} menu")
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        raise ScreenStyleError(
            f"The {role} menu has conflicting {position} artwork; refusing to guess"
        )
    if ranked[0][1] * 2 <= len(images):
        raise ScreenStyleError(
            f"The {role} menu has no consistent {position} artwork; refusing to guess"
        )
    return ranked[0][0]


def _artwork_for_role(blob: bytes, pages: list[MenuPage], role: str) -> MenuArtwork:
    role_pages = [page for page in pages if page.role == role]
    if not role_pages:
        raise ScreenStyleError(f"No recognizable {role} menu pages were found")
    background = _majority_image(
        [_image_bytes(blob, page.background) for page in role_pages], role, "background"
    )
    buttons: dict[int, bytes] = {}
    last_page_buttons: dict[int, bytes] = {}
    for row in BUTTON_ROWS:
        row_images = [
            _image_bytes(blob, page.buttons[row])
            for page in role_pages
            if row in page.buttons
        ]
        if row_images:
            buttons[row] = _majority_image(row_images, role, f"row-{row} button")
    last_pages: dict[int, MenuPage] = {}
    for page in role_pages:
        if page.mode not in last_pages or page.page > last_pages[page.mode].page:
            last_pages[page.mode] = page
    for row in BUTTON_ROWS:
        last_page_images = [
            _image_bytes(blob, page.buttons[row])
            for page in last_pages.values()
            if row in page.buttons
        ]
        if not last_page_images:
            continue
        last_page_image = _majority_image(
            last_page_images, role, f"last-page row-{row} button"
        )
        if row in buttons and last_page_image != buttons[row]:
            last_page_buttons[row] = last_page_image
    return MenuArtwork(background, buttons, last_page_buttons)


def _style_fingerprint(activity: MenuArtwork, device: MenuArtwork) -> str:
    digest = hashlib.sha256()
    for role, artwork in (("activities", activity), ("devices", device)):
        digest.update(role.encode("ascii"))
        digest.update(artwork.background)
        for row in BUTTON_ROWS:
            digest.update(bytes((row,)))
            digest.update(artwork.buttons[row])
            if row in artwork.last_page_buttons:
                digest.update(b"last")
                digest.update(artwork.last_page_buttons[row])
    return digest.hexdigest()


def inspect_remote_style(blob: bytes) -> RemoteStyle:
    """Extract consistent activity/device menu art from a raw backup."""
    try:
        validate_image(blob)
        config = parse_config(blob)
    except (ConfigError, ValueError) as error:
        raise ScreenStyleError(f"Not a validated Harmony One backup: {error}") from error
    pages = _menu_pages(blob, config, _find_picture_bank(blob, config))
    return _style_from_pages(blob, pages)


def create_artwork_style(activity: MenuArtwork, device: MenuArtwork, name: str) -> RemoteStyle:
    """Validate generated artwork using the same checks as saved style presets."""
    activity = _role_from_preset(_role_to_preset(activity), "Activities")
    device = _role_from_preset(_role_to_preset(device), "Devices")
    return RemoteStyle(activity, device, _style_fingerprint(activity, device), name)


def _decode_style_picture(value: Any, expected_size: tuple[int, int]) -> bytes:
    if not isinstance(value, str):
        raise ScreenStyleError("Built-in style picture data must be a base64 string")
    try:
        encoded = base64.b64decode(value, validate=True)
        picture_data = zlib.decompress(encoded)
    except (ValueError, zlib.error) as error:
        raise ScreenStyleError(f"Built-in style picture is corrupt: {error}") from error
    picture = picture_at(picture_data, 0, len(picture_data))
    if (
        picture is None
        or picture.end != len(picture_data)
        or (picture.width, picture.height) != expected_size
    ):
        raise ScreenStyleError("Built-in style picture has an invalid size or encoding")
    return picture_data


def _encode_style_picture(picture_data: bytes) -> str:
    return base64.b64encode(zlib.compress(picture_data, level=9)).decode("ascii")


def _role_to_preset(artwork: MenuArtwork) -> dict[str, Any]:
    return {
        "background": _encode_style_picture(artwork.background),
        "buttons": {
            str(row): _encode_style_picture(artwork.buttons[row])
            for row in BUTTON_ROWS
        },
        "last_page_buttons": {
            str(row): _encode_style_picture(picture)
            for row, picture in artwork.last_page_buttons.items()
        },
    }


def _role_from_preset(value: Any, role: str) -> MenuArtwork:
    if not isinstance(value, dict):
        raise ScreenStyleError(f"Built-in {role} style data must be an object")
    background = _decode_style_picture(value.get("background"), SCREEN_SIZE)
    raw_buttons = value.get("buttons")
    raw_last_page_buttons = value.get("last_page_buttons", {})
    if not isinstance(raw_buttons, dict) or not isinstance(raw_last_page_buttons, dict):
        raise ScreenStyleError(f"Built-in {role} buttons must be objects")
    buttons: dict[int, bytes] = {}
    last_page_buttons: dict[int, bytes] = {}
    for row in BUTTON_ROWS:
        key = str(row)
        if key not in raw_buttons:
            raise ScreenStyleError(f"Built-in {role} style is missing row {row}")
        buttons[row] = _decode_style_picture(raw_buttons[key], BUTTON_SIZE)
        if key in raw_last_page_buttons:
            last_page_buttons[row] = _decode_style_picture(
                raw_last_page_buttons[key], BUTTON_SIZE
            )
    if any(not str(row).isdigit() or int(row) not in BUTTON_ROWS for row in raw_last_page_buttons):
        raise ScreenStyleError(f"Built-in {role} style has an unknown last-page row")
    return MenuArtwork(background, buttons, last_page_buttons)


def _styles_from_payload(
    payload: Any, description: str, *, allow_empty: bool = False
) -> dict[str, RemoteStyle]:
    if not isinstance(payload, dict) or not isinstance(payload.get("styles"), list):
        raise ScreenStyleError(f"{description} must contain a styles list")
    styles: dict[str, RemoteStyle] = {}
    for entry in payload["styles"]:
        if not isinstance(entry, dict):
            raise ScreenStyleError(f"{description} entries must be objects")
        style_id, name = entry.get("id"), entry.get("name")
        if not isinstance(style_id, str) or not style_id or not isinstance(name, str) or not name:
            raise ScreenStyleError(f"{description} styles need non-empty ids and names")
        if style_id in styles:
            raise ScreenStyleError(f"Duplicate style id {style_id!r} in {description}")
        activity_menu = _role_from_preset(entry.get("activities"), "Activities")
        device_menu = _role_from_preset(entry.get("devices"), "Devices")
        fingerprint = _style_fingerprint(activity_menu, device_menu)
        if entry.get("fingerprint") != fingerprint:
            raise ScreenStyleError(f"Style {style_id!r} in {description} has a bad fingerprint")
        styles[style_id] = RemoteStyle(activity_menu, device_menu, fingerprint, name)
    if not styles and not allow_empty:
        raise ScreenStyleError(f"No styles are available in {description}")
    return styles


def _style_library_path() -> Path:
    return custom_style_library_path()


def _read_style_file(path: Path, description: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "styles": []}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ScreenStyleError(f"Could not load {description} from {path}: {error}") from error


def load_user_styles(path: Path | None = None) -> dict[str, RemoteStyle]:
    """Load artwork-only styles saved by the user on this computer."""
    path = path or _style_library_path()
    payload = _read_style_file(path, "custom display styles")
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ScreenStyleError(f"Custom display-style file at {path} has an unsupported schema")
    return _styles_from_payload(
        payload, "custom display-style file", allow_empty=True
    )


def save_user_style(name: str, style: RemoteStyle, path: Path | None = None) -> tuple[str, RemoteStyle]:
    """Persist a named, artwork-only style in the local user style library."""
    name = name.strip()
    if not name:
        raise ScreenStyleError("A custom style name cannot be empty")
    path = path or _style_library_path()
    existing = load_user_styles(path)
    all_styles = list(load_builtin_styles().values()) + list(existing.values())
    if any(saved.name.casefold() == name.casefold() for saved in all_styles):
        raise ScreenStyleError(f"A style named {name!r} already exists")
    style_id = f"custom-{uuid.uuid4().hex}"
    saved_style = RemoteStyle(
        style.activity_menu,
        style.device_menu,
        style.fingerprint,
        name,
    )
    entries = [
        {
            "id": saved_id,
            "name": saved.name,
            "fingerprint": saved.fingerprint,
            "activities": _role_to_preset(saved.activity_menu),
            "devices": _role_to_preset(saved.device_menu),
        }
        for saved_id, saved in existing.items()
    ]
    entries.append(
        {
            "id": style_id,
            "name": name,
            "fingerprint": style.fingerprint,
            "activities": _role_to_preset(style.activity_menu),
            "devices": _role_to_preset(style.device_menu),
        }
    )
    payload = {"schema_version": 1, "styles": entries}
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        detail = str(error)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                detail += f"\nTemporary style file cleanup failed: {cleanup_error}"
        raise ScreenStyleError(f"Could not save custom style to {path}: {detail}") from error
    return style_id, saved_style


def load_builtin_styles(path: Path | None = None) -> dict[str, RemoteStyle]:
    """Load the artwork-only built-in style library."""
    if path is None:
        frozen_root = getattr(sys, "_MEIPASS", None)
        root = Path(frozen_root) / "data" if frozen_root else Path(__file__).parent / "data"
        path = root / "screen_styles.json"
    payload = _read_style_file(path, "built-in display styles")
    return _styles_from_payload(payload, "built-in display-style file")


def _preset_artwork(style: RemoteStyle) -> dict[str, MenuArtwork]:
    return {"activities": style.activity_menu, "devices": style.device_menu}


def _style_from_pages(blob: bytes, pages: list[MenuPage]) -> RemoteStyle:
    activity_art = _artwork_for_role(blob, pages, "activities")
    device_art = _artwork_for_role(blob, pages, "devices")
    return RemoteStyle(
        activity_art,
        device_art,
        _style_fingerprint(activity_art, device_art),
    )


def _pages_match_style(
    blob: bytes, pages: list[MenuPage], artwork_by_role: dict[str, MenuArtwork]
) -> bool:
    last_page_by_mode: dict[tuple[str, int], int] = {}
    for page in pages:
        key = (page.role, page.mode)
        last_page_by_mode[key] = max(last_page_by_mode.get(key, -1), page.page)
    for page in pages:
        artwork = artwork_by_role[page.role]
        if _image_bytes(blob, page.background) != artwork.background:
            return False
        is_last_page = page.page == last_page_by_mode[(page.role, page.mode)]
        for row, draw in page.buttons.items():
            if row not in artwork.buttons:
                return False
            expected = (
                artwork.last_page_buttons.get(row, artwork.buttons[row])
                if is_last_page
                else artwork.buttons[row]
            )
            if _image_bytes(blob, draw) != expected:
                return False
        if any(
            _image_bytes(blob, draw) != GENERIC_MENU_ICON
            for draw in page.icons.values()
        ):
            return False
    return True


def _append_picture(
    picture_data: bytes,
    target_blob: bytes,
    target_config: RemoteConfig,
    known_pictures: dict[bytes, int],
    additions: dict[bytes, int],
) -> int:
    known = known_pictures.get(picture_data)
    if known is not None:
        return known
    addition = additions.get(picture_data)
    if addition is not None:
        return addition
    offset = len(target_blob) - 6 + sum(len(value) for value in additions)
    if offset + len(picture_data) > CONFIG_FLASH_LIMIT - target_config.base_address:
        raise ScreenStyleError("Style artwork would exceed the Harmony One configuration limit")
    address = target_config.base_address + offset
    additions[picture_data] = address
    return address


def _create_style_candidate_from_artwork(
    target_blob: bytes,
    style_artwork: dict[str, MenuArtwork],
    fingerprint: str,
) -> StyleTransferResult:
    """Apply menu artwork to a backup and return a validated offline candidate."""
    try:
        validate_image(target_blob)
        target_config = parse_config(target_blob)
    except (ConfigError, ValueError) as error:
        raise ScreenStyleError(f"Style transfer requires a validated Harmony One backup: {error}") from error

    target_bank = _find_picture_bank(target_blob, target_config)
    target_pages = _menu_pages(target_blob, target_config, target_bank)
    if not any(page.role == "activities" for page in target_pages):
        raise ScreenStyleError("Target backup has no recognizable Activities list pages")
    if not any(page.role == "devices" for page in target_pages):
        raise ScreenStyleError("Target backup has no recognizable Devices list pages")
    if _pages_match_style(target_blob, target_pages, style_artwork):
        return StyleTransferResult(target_blob, 0, 0, 0, fingerprint)
    target_devices = read_devices(target_blob, target_config)
    target_activities = read_activities(target_blob, target_config)

    known_pictures = {
        target_blob[picture.offset : picture.end]:
        target_config.base_address + picture.offset
        for picture in target_bank
    }

    all_page_draws: dict[tuple[int, int], tuple[PictureDraw, ...]] = {}
    for screen_page in read_mode_pages(target_blob, target_config):
        root_address = target_config.base_address + screen_page.program
        all_page_draws[(screen_page.mode, screen_page.page)] = read_picture_draws(
            target_blob, target_config, root_address
        )

    roles_per_mode: dict[int, set[str]] = {}
    for page in target_pages:
        roles_per_mode.setdefault(page.mode, set()).add(page.role)
    if any(len(roles) != 1 for roles in roles_per_mode.values()):
        raise ScreenStyleError("A screen mode was classified as both an Activities and Devices list")
    role_by_mode = {mode: next(iter(roles)) for mode, roles in roles_per_mode.items()}
    owners: dict[int, set[tuple[int, int]]] = {}
    for page_key, draws in all_page_draws.items():
        for draw in draws:
            owners.setdefault(draw.instruction_offset, set()).add(page_key)

    screen_table = target_config.sections.get(raw_slot_for_base_slot(11))
    if screen_table is not None:
        start = screen_table[0]
        program_count = int.from_bytes(target_blob[start : start + 2], "little")
        if start + 2 + 3 * program_count > screen_table[1]:
            raise ScreenStyleError("Screen-program table extends beyond its section")
        for index in range(program_count):
            root_address = _u24(target_blob, start + 2 + 3 * index)
            for draw in read_picture_draws(target_blob, target_config, root_address):
                owners.setdefault(draw.instruction_offset, set()).add((-1, index))

    additions: dict[bytes, int] = {}
    pointer_patches: dict[int, tuple[int, bytes]] = {}
    last_page_by_mode: dict[tuple[str, int], int] = {}
    for page in target_pages:
        key = (page.role, page.mode)
        last_page_by_mode[key] = max(last_page_by_mode.get(key, -1), page.page)

    changed_pages: set[tuple[int, int]] = set()
    for page in target_pages:
        artwork = style_artwork[page.role]
        if any(row not in artwork.buttons for row in page.buttons):
            raise ScreenStyleError(
                f"The selected style has no button artwork for a {page.role} menu row"
            )
        is_last_page = page.page == last_page_by_mode[(page.role, page.mode)]
        replacements = [(page.background, artwork.background)] + [
            (
                draw,
                artwork.last_page_buttons.get(row, artwork.buttons[row])
                if is_last_page
                else artwork.buttons[row],
            )
            for row, draw in page.buttons.items()
        ] + [(draw, GENERIC_MENU_ICON) for draw in page.icons.values()]
        page_changed = False
        for draw, image in replacements:
            if _image_bytes(target_blob, draw) == image:
                continue
            draw_owners = owners.get(draw.instruction_offset, set())
            unrelated_owners = {
                owner
                for owner in draw_owners
                if owner[0] < 0 or owner[0] not in role_by_mode
            }
            if unrelated_owners:
                raise ScreenStyleError(
                    "A menu drawing instruction is shared with another screen; "
                    "refusing to alter unrelated screens"
                )
            address = _append_picture(
                image,
                target_blob,
                target_config,
                known_pictures,
                additions,
            )
            replacement = address.to_bytes(3, "little")
            patch = (draw.address, replacement)
            previous = pointer_patches.get(draw.pointer_offset)
            if previous is not None and previous != patch:
                raise ScreenStyleError(
                    "A shared menu drawing instruction needs conflicting artwork; refusing to guess"
                )
            pointer_patches[draw.pointer_offset] = patch
            changed_pages.update(
                owner for owner in draw_owners if owner[0] >= 0
            )
            page_changed = True
        if page_changed:
            changed_pages.add((page.mode, page.page))

    if not pointer_patches and not additions:
        return StyleTransferResult(
            target_blob,
            0,
            0,
            0,
            fingerprint,
        )

    output = bytearray(target_blob[:-6])
    output.extend(b"".join(additions))
    output.extend(target_blob[-6:])
    new_end_address = target_config.base_address + len(output) - 4
    if new_end_address >= CONFIG_FLASH_LIMIT:
        raise ScreenStyleError("Style candidate exceeds the Harmony One flash address limit")
    struct.pack_into("<I", output, 4, new_end_address)

    for pointer_offset, (expected_address, replacement) in pointer_patches.items():
        if _u24(output, pointer_offset) != expected_address:
            raise ScreenStyleError("Screen program changed while preparing the style candidate")
        output[pointer_offset : pointer_offset + 3] = replacement
    output[-6:-4] = trailer_checksum(bytes(output)).to_bytes(2, "little")
    candidate = bytes(output)

    try:
        validate_image(candidate)
        candidate_config = parse_config(candidate)
        candidate_bank = walk_bank(
            candidate, target_bank[0].offset, len(candidate) - 6
        )
        if not candidate_bank:
            raise ScreenStyleError("Generated style picture bank failed its boundary check")
    except (ConfigError, ValueError) as error:
        raise ScreenStyleError(f"Generated style candidate failed validation: {error}") from error
    candidate_pages = _menu_pages(candidate, candidate_config, candidate_bank)
    if not _pages_match_style(candidate, candidate_pages, style_artwork):
        raise ScreenStyleError("Generated candidate does not match the selected display style")
    if (
        read_devices(candidate, candidate_config) != target_devices
        or read_activities(candidate, candidate_config) != target_activities
    ):
        raise ScreenStyleError("Generated candidate changed device or activity data")

    return StyleTransferResult(
        candidate,
        len(changed_pages),
        len(pointer_patches),
        len(additions),
        fingerprint,
    )


def create_style_candidate_from_style(
    target_blob: bytes, style: RemoteStyle
) -> StyleTransferResult:
    """Apply one built-in or inspected style to a validated target backup."""
    return _create_style_candidate_from_artwork(
        target_blob, _preset_artwork(style), style.fingerprint
    )


def create_style_candidate(target_blob: bytes, style_blob: bytes) -> StyleTransferResult:
    """Apply artwork from a donor backup (or a built-in style source backup)."""
    try:
        validate_image(target_blob)
        target_config = parse_config(target_blob)
        validate_image(style_blob)
        style_config = parse_config(style_blob)
    except (ConfigError, ValueError) as error:
        raise ScreenStyleError(
            f"Style transfer requires validated Harmony One backups: {error}"
        ) from error
    if (target_config.architecture, target_config.skin, target_config.base_address) != (
        style_config.architecture,
        style_config.skin,
        style_config.base_address,
    ):
        raise ScreenStyleError(
            "Target and style-source backups must use the same Harmony One architecture and skin"
        )
    return create_style_candidate_from_style(target_blob, inspect_remote_style(style_blob))


def create_menu_icon_candidate(
    target_blob: bytes,
    occurrence: MenuIconOccurrence,
    replacement_picture: bytes,
) -> MenuIconCandidate:
    """Replace one decoded menu-row icon while preserving all loaded style artwork."""
    picture = picture_at(replacement_picture, 0, len(replacement_picture))
    if (
        picture is None
        or picture.end != len(replacement_picture)
        or (picture.width, picture.height) != ICON_SIZE
    ):
        raise ScreenStyleError("A menu icon must be one complete 51 x 48 picture")
    try:
        validate_image(target_blob)
        config = parse_config(target_blob)
        bank = _find_picture_bank(target_blob, config)
        pages = _menu_pages(target_blob, config, bank)
        original_occurrences = list_menu_icon_occurrences(target_blob)
    except (ConfigError, ValueError, IndexError) as error:
        raise ScreenStyleError(f"Could not prepare a menu icon edit: {error}") from error

    page = next(
        (
            page
            for page in pages
            if (page.role, page.mode, page.page)
            == (occurrence.role, occurrence.mode, occurrence.page)
        ),
        None,
    )
    if page is None or page.items.get(occurrence.row) != _normalized_text(occurrence.item_name):
        raise ScreenStyleError("The selected menu item no longer exists in this backup")
    draw = page.icons.get(occurrence.row)
    button = page.buttons.get(occurrence.row)
    if (
        draw is None
        or button is None
        or _image_bytes(target_blob, draw) != occurrence.picture_data
        or _image_bytes(target_blob, button) != occurrence.button_data
    ):
        raise ScreenStyleError("The selected menu icon changed; reload the backup and try again")
    root_address = config.base_address + next(
        screen_page.program
        for screen_page in read_mode_pages(target_blob, config)
        if (screen_page.mode, screen_page.page) == (occurrence.mode, occurrence.page)
    )
    row_draws = [
        screen_draw
        for screen_draw in read_picture_draws(target_blob, config, root_address)
        if (screen_draw.x, screen_draw.y) == (ICON_X, occurrence.row + 1)
    ]
    if len(row_draws) != 1 or row_draws[0].pointer_offset != draw.pointer_offset:
        raise ScreenStyleError(
            "This menu button has multiple icon drawing paths; refusing an ambiguous edit."
        )
    target_key = (occurrence.role, occurrence.mode, occurrence.page, occurrence.row)
    if replacement_picture == occurrence.picture_data:
        return MenuIconCandidate(target_blob, occurrence, False)

    owners: dict[int, set[tuple[int, int]]] = defaultdict(set)
    screen_pages = read_mode_pages(target_blob, config)
    for screen_page in screen_pages:
        root_address = config.base_address + screen_page.program
        for screen_draw in read_picture_draws(target_blob, config, root_address):
            owners[screen_draw.instruction_offset].add((screen_page.mode, screen_page.page))
    screen_table = config.sections.get(raw_slot_for_base_slot(11))
    if screen_table is not None:
        start, end = screen_table
        program_count = int.from_bytes(target_blob[start : start + 2], "little")
        if start + 2 + 3 * program_count > end:
            raise ScreenStyleError("Screen-program table extends beyond its section")
        for index in range(program_count):
            root_address = _u24(target_blob, start + 2 + 3 * index)
            for screen_draw in read_picture_draws(target_blob, config, root_address):
                owners[screen_draw.instruction_offset].add((-1, index))
    if owners.get(draw.instruction_offset) != {(occurrence.mode, occurrence.page)}:
        raise ScreenStyleError(
            "This menu icon drawing is shared with another screen occurrence; "
            "it cannot be edited independently."
        )

    known_pictures = {
        target_blob[entry.offset : entry.end]: config.base_address + entry.offset
        for entry in bank
    }
    additions: dict[bytes, int] = {}
    address = _append_picture(
        replacement_picture,
        target_blob,
        config,
        known_pictures,
        additions,
    )
    output = bytearray(target_blob[:-6])
    output.extend(b"".join(additions))
    output.extend(target_blob[-6:])
    new_end_address = config.base_address + len(output) - 4
    if new_end_address >= CONFIG_FLASH_LIMIT:
        raise ScreenStyleError("Menu icon candidate exceeds the Harmony One flash limit")
    struct.pack_into("<I", output, 4, new_end_address)
    if _u24(output, draw.pointer_offset) != draw.address:
        raise ScreenStyleError("Menu screen changed while preparing the icon candidate")
    output[draw.pointer_offset : draw.pointer_offset + 3] = address.to_bytes(3, "little")
    output[-6:-4] = trailer_checksum(bytes(output)).to_bytes(2, "little")
    candidate = bytes(output)

    try:
        validate_image(candidate)
        candidate_config = parse_config(candidate)
        candidate_bank = walk_bank(candidate, bank[0].offset, len(candidate) - 6)
        if not candidate_bank:
            raise ScreenStyleError("Generated menu icon bank failed its boundary check")
        candidate_pages = _menu_pages(candidate, candidate_config, candidate_bank)
        candidate_occurrences = list_menu_icon_occurrences(candidate)
    except (ConfigError, ValueError, IndexError) as error:
        raise ScreenStyleError(f"Generated menu icon candidate failed validation: {error}") from error

    before_occurrences = {
        (item.role, item.mode, item.page, item.row): item for item in original_occurrences
    }
    after_occurrences = {
        (item.role, item.mode, item.page, item.row): item for item in candidate_occurrences
    }
    if before_occurrences.keys() != after_occurrences.keys():
        raise ScreenStyleError("Generated icon candidate changed the recognized menu rows")
    for key, before in before_occurrences.items():
        expected = replacement_picture if key == target_key else before.picture_data
        if after_occurrences[key].picture_data != expected:
            raise ScreenStyleError("Generated icon candidate changed an unintended menu row")

    before_pages = {
        (item.role, item.mode, item.page): item for item in pages
    }
    after_pages = {
        (item.role, item.mode, item.page): item for item in candidate_pages
    }
    if before_pages.keys() != after_pages.keys():
        raise ScreenStyleError("Generated icon candidate changed the recognized display pages")
    for key, before in before_pages.items():
        after = after_pages[key]
        if _image_bytes(target_blob, before.background) != _image_bytes(candidate, after.background):
            raise ScreenStyleError("Generated icon candidate changed a menu background")
        if before.buttons.keys() != after.buttons.keys() or any(
            _image_bytes(target_blob, before.buttons[row])
            != _image_bytes(candidate, after.buttons[row])
            for row in before.buttons
        ):
            raise ScreenStyleError("Generated icon candidate changed the selected remote style")
    if (
        read_devices(candidate, candidate_config) != read_devices(target_blob, config)
        or read_activities(candidate, candidate_config) != read_activities(target_blob, config)
    ):
        raise ScreenStyleError("Generated icon candidate changed device or activity data")

    updated = next(
        item
        for item in candidate_occurrences
        if (item.role, item.mode, item.page, item.row) == target_key
    )
    return MenuIconCandidate(candidate, updated, bool(additions))
