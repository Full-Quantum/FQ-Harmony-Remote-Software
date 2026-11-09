"""Conservative, read-only decoding of a known Harmony One picture cycle."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_reader import ConfigError, parse_config, raw_slot_for_base_slot
from harmony_one.key_bindings import locate_action_lists
from harmony_one.pictures import Picture


class SlideshowCycleError(ValueError):
    """The configuration does not have one recognizable picture cycle."""


@dataclass(frozen=True)
class CycleScreen:
    mode: int
    picture_index: int
    picture: Picture
    timer: int
    seconds: int
    next_mode: int
    final_marker: bool


def _u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 3], "little")


def _order_cycle(screens: dict[int, CycleScreen]) -> list[CycleScreen]:
    cycles: list[list[CycleScreen]] = []
    for terminal in screens.values():
        if not terminal.final_marker:
            continue
        ordered: list[CycleScreen] = []
        seen: set[int] = set()
        mode = terminal.next_mode
        while mode in screens and mode not in seen:
            seen.add(mode)
            screen = screens[mode]
            ordered.append(screen)
            mode = screen.next_mode
        if (
            mode == terminal.next_mode
            and len(ordered) >= 2
            and ordered[-1] == terminal
            and sum(screen.final_marker for screen in ordered) == 1
        ):
            cycles.append(ordered)
    if len(cycles) != 1:
        raise SlideshowCycleError("No unique supported picture cycle was found.")
    return cycles[0]


def read_picture_cycle(blob: bytes, pictures: list[tuple[int, Picture]]) -> list[CycleScreen]:
    """Find a cycle matching the two owner-tested mode and timer layouts.

    The first screen is the target of the final-marked screen, not necessarily
    an ordinary slideshow image. Other layouts are deliberately unsupported.
    """
    try:
        config = parse_config(blob)
        if config.architecture != 12:
            raise SlideshowCycleError("This picture-cycle decoder requires architecture 12.")
        mode_section = config.sections[raw_slot_for_base_slot(6)]
        timer_section = config.sections[raw_slot_for_base_slot(12)]
        actions = locate_action_lists(blob, config)
        start, end = mode_section
        if start + 3 > end:
            raise SlideshowCycleError("The mode table is truncated.")
        count = _u24(blob, start)
        if start + 3 + count * 3 > end:
            raise SlideshowCycleError("The mode pointer table is truncated.")
        timer_start, timer_end = timer_section
        if timer_start >= timer_end:
            raise SlideshowCycleError("The timer table is truncated.")
        timer_count = blob[timer_start]
        if timer_start + 1 + timer_count * 3 > timer_end:
            raise SlideshowCycleError("The timer pointer table is truncated.")

        by_offset = {picture.offset: (index, picture) for index, picture in pictures}
        screens: dict[int, CycleScreen] = {}
        limit = len(blob) - 6
        base = config.base_address
        for mode in range(count):
            pointer = _u24(blob, start + 3 + mode * 3) - base
            if not 0 <= pointer <= limit - 6:
                raise SlideshowCycleError("A mode pointer is outside configuration content.")
            head = _u24(blob, pointer + 1) - base
            if not 0 <= head < pointer:
                raise SlideshowCycleError("A mode record has an invalid back pointer.")
            # Recognize the narrow tagged-list form only; do not guess wide layouts.
            entries = blob[head]
            if not entries:
                continue
            program = head + 1 + entries * 4
            if program + 8 > pointer or blob[program:program + 3] != b"\x02\x00\x00":
                continue
            if blob[program + 6:program + 8] != b"\x17\x00":
                continue
            picture = by_offset.get(_u24(blob, program + 3) - base)
            if picture is None:
                continue
            handlers = [
                (int.from_bytes(blob[pos + 1:pos + 3], "little"), blob[pos + 3])
                for pos in range(head + 1, program, 4)
                if blob[pos] == 6
            ]
            if len(handlers) != 1 or handlers[0][1] != 0x7F or handlers[0][0] >= len(actions):
                continue
            instructions = actions[handlers[0][0]].instructions
            if len(instructions) not in (4, 5):
                continue
            if [(item.opcode, item.operand) for item in instructions[:3]] != [
                (0x07, 0xFFFB), (0x1F, 0xFE02), (0x1F, 0xE805)
            ]:
                continue
            timer_instruction = instructions[3]
            if timer_instruction.opcode != 0x1F or timer_instruction.operand >> 8 != 0xEB:
                continue
            final = len(instructions) == 5
            if final and (instructions[4].opcode != 0x1F or instructions[4].operand >> 8 != 0xF2):
                continue
            timer = timer_instruction.operand & 0xFF
            if timer >= timer_count:
                continue
            record = _u24(blob, timer_start + 1 + timer * 3) - base
            if not 0 <= record <= limit - 7:
                raise SlideshowCycleError("A timer record points outside configuration content.")
            seconds = _u24(blob, record + 1)
            next_mode = int.from_bytes(blob[record + 4:record + 6], "little")
            if blob[record] != 1 or not seconds or blob[record + 6] != 0x7E or next_mode >= count:
                continue
            screens[mode] = CycleScreen(mode, picture[0], picture[1], timer, seconds, next_mode, final)
        return _order_cycle(screens)
    except (ConfigError, KeyError) as error:
        raise SlideshowCycleError(f"Cannot decode picture cycle: {error}") from error
