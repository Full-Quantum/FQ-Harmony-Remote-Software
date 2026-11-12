"""Conservative decoding and fixed-size edits of known Harmony One picture cycles."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config, raw_slot_for_base_slot
from harmony_one.key_bindings import locate_action_lists
from harmony_one.pictures import Picture
from harmony_one.slideshow import full_screen_pictures, refresh_checksum, replace_with_encoded

DEFAULT_SLIDE_SECONDS = 3
MAX_SLIDE_SECONDS = 0xFFFFFF


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


def _edit_cycle_timers(
    blob: bytes, durations: dict[int, int], transitions: dict[int, int] | None = None,
) -> bytes:
    """Patch isolated seven-byte cycle timers, never action lists or screen programs."""
    transitions = transitions or {}
    try:
        validate_image(blob)
        config = parse_config(blob)
        cycle = read_picture_cycle(blob, full_screen_pictures(blob))
        screens = {screen.mode: screen for screen in cycle}
        if not (durations.keys() | transitions.keys()) <= screens.keys():
            raise SlideshowCycleError("The requested screen is not in the current slideshow.")
        if not set(transitions.values()) <= screens.keys():
            raise SlideshowCycleError("A slideshow transition must stay inside the current cycle.")
        for seconds in durations.values():
            if type(seconds) is not int or not 1 <= seconds <= MAX_SLIDE_SECONDS:
                raise SlideshowCycleError(
                    f"Image time must be a whole number from 1 to {MAX_SLIDE_SECONDS} seconds."
                )
        start, _ = config.sections[raw_slot_for_base_slot(12)]
        offsets = [
            _u24(blob, start + 1 + index * 3) - config.base_address
            for index in range(blob[start])
        ]
        header_end = 0x0B + 4 * len(config.pointers)
        if any(not header_end <= offset <= len(blob) - 13 for offset in offsets):
            raise SlideshowCycleError("A timer record points outside configuration content.")
        actions = locate_action_lists(blob, config)
        output = bytearray(blob)
        for mode in durations.keys() | transitions.keys():
            screen = screens[mode]
            offset = offsets[screen.timer]
            if any(
                index != screen.timer and other < offset + 7 and offset < other + 7
                for index, other in enumerate(offsets)
            ):
                raise SlideshowCycleError("A slideshow timer is shared or overlaps another timer.")
            if sum(other.timer == screen.timer for other in cycle) != 1:
                raise SlideshowCycleError("Slideshow screens share a timer; independent times are unsafe.")
            references = sum(
                instruction.opcode == 0x1F and instruction.operand == (0xEB00 | screen.timer)
                for action in actions for instruction in action.instructions
            )
            if references != 1:
                raise SlideshowCycleError("A slideshow timer is also referenced by another action.")
            if (
                blob[offset] != 1 or blob[offset + 6] != 0x7E
                or _u24(blob, offset + 1) != screen.seconds
                or int.from_bytes(blob[offset + 4:offset + 6], "little") != screen.next_mode
            ):
                raise SlideshowCycleError("The slideshow timer record has an unsupported layout.")
            if mode in durations:
                output[offset + 1:offset + 4] = durations[mode].to_bytes(3, "little")
            if mode in transitions:
                output[offset + 4:offset + 6] = transitions[mode].to_bytes(2, "little")
        refresh_checksum(output)
        candidate = bytes(output)
        validate_image(candidate)
        updated = read_picture_cycle(candidate, full_screen_pictures(candidate))
        for screen in updated:
            original = screens[screen.mode]
            if (
                screen.seconds != durations.get(screen.mode, original.seconds)
                or screen.next_mode != transitions.get(screen.mode, original.next_mode)
            ):
                raise SlideshowCycleError("The slideshow timers did not match the requested changes.")
        return candidate
    except (ConfigError, KeyError) as error:
        raise SlideshowCycleError(f"Cannot edit slideshow timers: {error}") from error


def set_cycle_seconds(blob: bytes, slot: int, seconds: int) -> bytes:
    """Set one playback slot's whole-second duration without changing any image."""
    cycle = read_picture_cycle(blob, full_screen_pictures(blob))
    if type(slot) is not int or not 0 <= slot < len(cycle):
        raise SlideshowCycleError("This slot is not supported by the loaded slideshow.")
    candidate = _edit_cycle_timers(blob, {cycle[slot].mode: seconds})
    updated = read_picture_cycle(candidate, full_screen_pictures(candidate))
    if [screen.mode for screen in updated] != [screen.mode for screen in cycle]:
        raise SlideshowCycleError("Changing an image time unexpectedly changed playback order.")
    return candidate


def restore_default_cycle(blob: bytes, images: list[bytes]) -> bytes:
    """Restore four default images/times, bypassing extra screens in a six-image cycle."""
    cycle = read_picture_cycle(blob, full_screen_pictures(blob))
    if len(cycle) not in (4, 6) or len(images) != 4:
        raise SlideshowCycleError("Restoring defaults requires a recognized four- or six-image cycle.")
    if len({screen.picture.offset for screen in cycle}) != len(cycle):
        raise SlideshowCycleError("Cycle screens share a picture; default images cannot be independent.")
    # Keep the terminal screen's existing final action; only bypass the two extra timers.
    retained = cycle[:3] + [cycle[-1]]
    candidate = _edit_cycle_timers(
        blob, {screen.mode: DEFAULT_SLIDE_SECONDS for screen in retained},
        {screen.mode: retained[(index + 1) % 4].mode for index, screen in enumerate(retained)},
    )
    for screen, image in zip(retained, images):
        if (
            len(image) != screen.picture.size
            or image[:5] != b"\x00\xb0\x00\xdc\x00"
        ):
            raise SlideshowCycleError("Default slideshow images must be raw 176 x 220 pictures.")
        candidate = replace_with_encoded(candidate, screen.picture, image)
    validate_image(candidate)
    updated = read_picture_cycle(candidate, full_screen_pictures(candidate))
    if (
        [screen.mode for screen in updated] != [screen.mode for screen in retained]
        or [screen.seconds for screen in updated] != [DEFAULT_SLIDE_SECONDS] * 4
        or any(candidate[screen.picture.offset:screen.picture.end] != image
               for screen, image in zip(updated, images))
    ):
        raise SlideshowCycleError("The restored slideshow did not match the four-image default.")
    return candidate
