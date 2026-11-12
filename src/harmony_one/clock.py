"""Prepare clock-setting candidates for Harmony One configurations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from harmony_one.config_image import BytePatch, validate_image
from harmony_one.config_reader import (
    ConfigError,
    RemoteConfig,
    parse_config,
    raw_slot_for_base_slot,
)
from harmony_one.harmony_usb import CONFIG_FLASH_BASE, trailer_checksum

CLOCK_FIELDS = 7
CLOCK_STATE_MAXIMA = (59, 59, 23, 30, 6, 11)
CLOCK_COOKIE = b"\xdf\xad"
CLOCK_END = b"\xbf\xef"
CLOCK_RECORD_LENGTH = 14
STATE_TABLE_HEADER = 8
STATE_RECORD_HEADER = 7
STATE_TRANSITION_LENGTH = 8


class ClockError(ValueError):
    """A configuration does not contain a safely editable Harmony One clock."""


@dataclass(frozen=True)
class ClockCandidate:
    image: bytes
    local_time: datetime
    changed_bytes: int


def _clock_fields(when: datetime) -> bytes:
    if when.tzinfo is not None and when.utcoffset() is not None:
        raise ClockError("Choose a local wall-clock time without a timezone offset.")
    if not 2000 <= when.year <= 2255:
        raise ClockError("The Harmony One clock supports years 2000 through 2255.")
    year_offset = when.year - 2000
    month = when.month - 1
    if month == 1:
        max_day_index = 28 if (year_offset & 3) == 0 else 27
    else:
        max_day_index = 29 if month in (3, 5, 8, 10) else 30
    if when.day - 1 > max_day_index:
        raise ClockError("That date cannot be represented by the Harmony One clock.")
    return bytes(
        (
            when.second,
            when.minute,
            when.hour,
            when.day - 1,
            (when.weekday() + 1) % 7,
            month,
            year_offset,
        )
    )


def _section_range(config: RemoteConfig, base_slot: int, label: str) -> tuple[int, int]:
    result = config.sections.get(raw_slot_for_base_slot(base_slot))
    if result is None:
        raise ClockError(f"The configuration has no base slot {base_slot} {label}.")
    return result


def _state_record_offsets(blob: bytes, config: RemoteConfig) -> list[int]:
    start, end = _section_range(config, 13, "state table")
    if start + STATE_TABLE_HEADER > end:
        raise ClockError("The state table header is truncated.")
    count = int.from_bytes(blob[start : start + 2], "little")
    table_end = start + STATE_TABLE_HEADER + count * 3
    if count < CLOCK_FIELDS or table_end > end:
        raise ClockError("The state table does not contain seven clock records.")

    section_ranges = tuple(config.sections.values())
    if not section_ranges:
        raise ClockError("The configuration has no sections.")
    first_section = min(section_start for section_start, _ in section_ranges)
    header_end = 0x0B + 4 * len(config.pointers)
    offsets: list[int] = []
    for index in range(CLOCK_FIELDS):
        pointer_offset = start + STATE_TABLE_HEADER + index * 3
        address = int.from_bytes(blob[pointer_offset : pointer_offset + 3], "little")
        offset = address - config.base_address
        if address == 0 or not 0 <= offset < len(blob):
            raise ClockError(f"Clock state record {index} points outside the configuration.")
        if offset + STATE_RECORD_HEADER > len(blob):
            raise ClockError(f"Clock state record {index} is truncated.")
        transitions = int.from_bytes(blob[offset + 4 : offset + 6], "little")
        record_end = offset + STATE_RECORD_HEADER + transitions * STATE_TRANSITION_LENGTH
        in_section = any(
            section_start <= offset and record_end <= section_end
            for section_start, section_end in section_ranges
        )
        in_prefix = header_end <= offset and record_end <= first_section
        if record_end > len(blob) or not (in_section or in_prefix):
            raise ClockError(f"Clock state record {index} has an invalid extent.")
        offsets.append(offset)
    if len(set(offsets)) != CLOCK_FIELDS:
        raise ClockError("The clock state records are not distinct.")
    extents = sorted(
        (
            offset,
            offset
            + STATE_RECORD_HEADER
            + int.from_bytes(blob[offset + 4 : offset + 6], "little")
            * STATE_TRANSITION_LENGTH,
        )
        for offset in offsets
    )
    if any(
        left_end > right_start
        for (_, left_end), (right_start, _) in zip(extents, extents[1:])
    ):
        raise ClockError("Clock state records overlap.")
    return offsets


def _apply_clock_patches(
    blob: bytes,
    patches: list[BytePatch],
    state_offsets: list[int],
    config: RemoteConfig,
) -> bytes:
    section_ranges = tuple(config.sections.values())
    authorized_state_ranges = tuple((offset, offset + 4) for offset in state_offsets)
    ordered = sorted(patches, key=lambda patch: patch.offset)
    previous_end = -1
    for patch in ordered:
        if not patch.expected or len(patch.expected) != len(patch.replacement):
            raise ClockError("Clock edits must be nonempty and preserve configuration length.")
        end = patch.offset + len(patch.expected)
        in_section = any(start <= patch.offset < end <= stop for start, stop in section_ranges)
        in_clock_state = any(
            start <= patch.offset < end <= stop for start, stop in authorized_state_ranges
        )
        if not (in_section or in_clock_state):
            raise ClockError(f"Clock edit {patch.description!r} is outside an owned record.")
        if patch.offset < previous_end:
            raise ClockError("Clock edits overlap.")
        if blob[patch.offset:end] != patch.expected:
            raise ClockError(f"Source bytes changed for {patch.description!r}.")
        previous_end = end
    result = bytearray(blob)
    for patch in ordered:
        result[patch.offset : patch.offset + len(patch.expected)] = patch.replacement
    result[-6:-4] = trailer_checksum(result).to_bytes(2, "little")
    candidate = bytes(result)
    try:
        validate_image(candidate)
    except (ConfigError, ValueError) as error:
        raise ClockError(f"Generated clock candidate failed validation: {error}") from error
    return candidate


def prepare_clock_candidate(blob: bytes, when: datetime) -> ClockCandidate:
    """Stamp the configuration and its seven startup clock records with local time."""
    try:
        validate_image(blob)
        config = parse_config(blob)
    except (ConfigError, ValueError) as error:
        raise ClockError(f"Not a validated Harmony One configuration: {error}") from error
    if config.architecture != 12 or config.base_address != CONFIG_FLASH_BASE:
        raise ClockError("Clock setting currently supports Harmony One architecture 12 only.")

    fields = _clock_fields(when)
    timestamp_start, timestamp_end = _section_range(config, 3, "clock record")
    if timestamp_end - timestamp_start < CLOCK_RECORD_LENGTH:
        raise ClockError("The configuration clock record is truncated.")
    timestamp = blob[timestamp_start : timestamp_start + CLOCK_RECORD_LENGTH]
    if (
        timestamp[:2] != CLOCK_COOKIE
        or timestamp[9:11] != CLOCK_END
        or timestamp[11:] != bytes(3)
    ):
        raise ClockError("The base slot 3 clock record has an unexpected layout.")
    state_offsets = _state_record_offsets(blob, config)
    state_values = [
        int.from_bytes(blob[offset : offset + 2], "little")
        for offset in state_offsets
    ]
    if any(value > 0xFF for value in state_values) or timestamp[2:9] != bytes(state_values):
        raise ClockError("The timestamp and state-table clock records disagree.")

    patches = [
        BytePatch(
            timestamp_start + 2,
            timestamp[2:9],
            fields,
            "set saved clock timestamp",
        )
    ]
    for index, (offset, value) in enumerate(zip(state_offsets, fields)):
        current_value = int.from_bytes(blob[offset : offset + 2], "little")
        current_max = int.from_bytes(blob[offset + 2 : offset + 4], "little")
        expected_max = CLOCK_STATE_MAXIMA[index] if index < len(CLOCK_STATE_MAXIMA) else None
        if expected_max is not None and current_max != expected_max and not (
            index == 3 and current_max == expected_max + 1
        ):
            raise ClockError(
                f"Clock state record {index} has an unexpected maximum ({current_max})."
            )
        if index == 6 and current_max != current_value + 1:
            raise ClockError("The clock year record has an unexpected maximum.")

        replacement = value.to_bytes(2, "little")
        expected = blob[offset : offset + 2]
        if expected != replacement:
            patches.append(
                BytePatch(offset, expected, replacement, f"set clock state {index}")
            )
        if index == 3 and current_max != expected_max:
            patches.append(
                BytePatch(
                    offset + 2,
                    blob[offset + 2 : offset + 4],
                    expected_max.to_bytes(2, "little"),
                    "normalize clock day maximum",
                )
            )
        elif index == 6:
            new_max = value + 1
            if current_max != new_max:
                patches.append(
                    BytePatch(
                        offset + 2,
                        blob[offset + 2 : offset + 4],
                        new_max.to_bytes(2, "little"),
                        "update clock year maximum",
                    )
                )

    candidate = _apply_clock_patches(blob, patches, state_offsets, config)
    return ClockCandidate(
        candidate,
        when.replace(microsecond=0),
        sum(left != right for left, right in zip(blob, candidate)),
    )
