"""Walk screen programs (Harmony One, arch 12) and collect the text they draw.

Opcode lengths are from the public reference (dannybloe/harmony-explorations, MIT):
screens.md "The screen language". A string is a run of per-configuration glyph codes
ended by zero; a code with bit 7 set is the first half of a two-byte code.
Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_reader import RemoteConfig, raw_slot_for_base_slot

SCREEN_TABLE_BASE_SLOT = 11


@dataclass(frozen=True)
class TextDraw:
    program: int
    address: int  # absolute offset of the glyph codes in the container
    x: int
    y: int
    font: int
    codes: tuple[int, ...]
    reference: bool = False


class ProgramError(ValueError):
    pass


def _u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "little")


def read_string(data: bytes, at: int) -> tuple[tuple[int, ...], int]:
    codes = []
    while data[at] != 0:
        if data[at] & 0x80:
            codes.append((data[at] << 8) | data[at + 1])
            at += 2
        else:
            codes.append(data[at])
            at += 1
    return tuple(codes), at + 1


def walk_program(
    data: bytes, at: int, program: int, base: int, limit: int = 4096, depth: int = 0
) -> list[TextDraw]:
    """Walk one program from `at` until its end opcode; return the text it draws.

    Calls (opcode 22) are followed and return at opcode 23 or the callee's end; jumps
    (opcode 20) are followed. Switches are not followed, only walked past.
    """
    draws: list[TextDraw] = []
    font = 0
    for _ in range(limit):
        opcode = data[at]
        at += 1
        if opcode in (0, 23):
            return draws
        if opcode == 22 and depth < 4:
            target = _u24(data, at) - base
            at += 3
            draws.extend(walk_program(data, target, program, base, limit, depth + 1))
            continue
        if opcode == 20:
            at = _u24(data, at) - base
            continue
        if opcode == 1:
            at += 6
        elif opcode == 2:
            at += 5
        elif opcode == 3:
            at += 9
        elif opcode == 4:
            x, y = data[at], data[at + 1]
            target = _u24(data, at + 2) - base
            at += 5
            if 0 <= target < len(data):
                codes, _ = read_string(data, target)
                draws.append(TextDraw(program, target, x, y, font, codes, True))
        elif opcode == 5:
            x, y = data[at], data[at + 1]
            at += 2
            codes, end = read_string(data, at)
            draws.append(TextDraw(program, at, x, y, font, codes))
            at = end
        elif opcode == 16:
            font = data[at]
            at += 1
        elif opcode == 17:
            at += 3
        elif opcode in (18, 19):
            wide = opcode == 19
            width = 2 if wide else 1
            at += 1
            count = int.from_bytes(data[at : at + width], "little")
            at += width + count * (width + 3)
            count = int.from_bytes(data[at : at + width], "little")
            at += width + count * (2 * width + 3)
        elif opcode == 22:
            at += 3
        else:
            raise ProgramError(f"unknown opcode {opcode} at {at - 1:#x}")
    raise ProgramError("program did not end")


@dataclass(frozen=True)
class ModePage:
    mode: int
    page: int
    program: int  # absolute offset
    draws: tuple[TextDraw, ...]


def read_mode_pages(data: bytes, config: RemoteConfig) -> list[ModePage]:
    """Every page of every mode (base slot 6) with the text its program draws."""
    section = config.sections.get(raw_slot_for_base_slot(6))
    if not section:
        return []
    start = section[0]
    count = _u24(data, start)
    pages = []
    for mode in range(count):
        at = _u24(data, start + 3 + 3 * mode) - config.base_address
        page_count = int.from_bytes(data[at + 4 : at + 6], "little")
        for page in range(page_count):
            record = _u24(data, at + 6 + 3 * page) - config.base_address
            program = _u24(data, record + 4) - config.base_address
            try:
                draws = tuple(walk_program(data, program, mode, config.base_address))
            except (ProgramError, IndexError):
                draws = ()
            pages.append(ModePage(mode, page, program, draws))
    return pages


def read_screen_programs(data: bytes, config: RemoteConfig) -> list[list[TextDraw]]:
    """Text drawn by each program in the base slot 11 table (unwalkable ones are empty)."""
    section = config.sections.get(raw_slot_for_base_slot(SCREEN_TABLE_BASE_SLOT))
    if not section:
        return []
    start = section[0]
    count = int.from_bytes(data[start : start + 2], "little")
    programs = []
    for index in range(count):
        at = _u24(data, start + 2 + 3 * index) - config.base_address
        try:
            programs.append(walk_program(data, at, index, config.base_address))
        except (ProgramError, IndexError):
            programs.append([])
    return programs
