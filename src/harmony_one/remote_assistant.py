"""Bounded edits to the built-in Harmony One Remote Assistant prompt setting."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config, raw_slot_for_base_slot
from harmony_one.harmony_usb import trailer_checksum
from harmony_one.key_bindings import Instruction, locate_action_lists


class RemoteAssistantError(ValueError):
    """The backup has no uniquely recognizable assistant prompt setting."""


@dataclass(frozen=True)
class RemoteAssistantSetting:
    variable: int
    offset: int
    value: int

    @property
    def enabled(self) -> bool:
        return self.value != 3


def inspect_remote_assistant(blob: bytes) -> RemoteAssistantSetting:
    """Identify the paired Options on/off actions and their isolated saved state."""
    try:
        validate_image(blob)
        config = parse_config(blob)
        candidates: dict[int, set[int]] = {}
        for action in locate_action_lists(blob, config):
            steps = action.instructions
            if (
                len(steps) == 3
                and steps[0] == Instruction(0x75, 0x0FCA)
                and steps[2] == Instruction(0x07, 0xFFF7)
                and steps[1].opcode >= 0x80
                and steps[1].operand in (1, 3)
            ):
                candidates.setdefault(steps[1].opcode - 0x80, set()).add(steps[1].operand)
        variables = [variable for variable, values in candidates.items() if values == {1, 3}]
        if len(variables) != 1:
            raise RemoteAssistantError(
                "No unique built-in Remote Assistant on/off toggle was found. "
                "This backup cannot safely be changed; assistant screens will not be installed."
            )
        variable = variables[0]
        section = config.sections.get(raw_slot_for_base_slot(13))
        if section is None:
            raise RemoteAssistantError("The backup has no state table.")
        start, end = section
        if start + 8 > end:
            raise RemoteAssistantError("The state table header is truncated.")
        count = int.from_bytes(blob[start:start + 2], "little")
        if variable >= count or start + 8 + count * 3 > end:
            raise RemoteAssistantError("The assistant state pointer table is truncated.")
        offsets = [
            int.from_bytes(blob[start + 8 + index * 3:start + 11 + index * 3], "little")
            - config.base_address
            for index in range(count)
        ]
        offset = offsets[variable]
        header_end = 0x0B + 4 * len(config.pointers)
        first_section = min(left for left, _ in config.sections.values())
        in_prefix = header_end <= offset and offset + 7 <= first_section
        in_section = any(
            left <= offset and offset + 7 <= right for left, right in config.sections.values()
        )
        if not (in_prefix or in_section):
            raise RemoteAssistantError("The assistant state record is outside configuration content.")
        value = int.from_bytes(blob[offset:offset + 2], "little")
        if value > 3 or blob[offset + 2:offset + 7] != b"\x03\x00\x00\x00\x00":
            raise RemoteAssistantError("The assistant state record has an unsupported layout.")
        # A state record can be shared or overlap another record even when its own header is valid.
        for index, other in enumerate(offsets):
            if index == variable:
                continue
            if not 0 <= other <= len(blob) - 13:
                raise RemoteAssistantError("A state record points outside the configuration.")
            transitions = int.from_bytes(blob[other + 4:other + 6], "little")
            other_end = other + 7 + transitions * 8
            if other_end > len(blob) - 6:
                raise RemoteAssistantError("A state record is truncated.")
            if other < offset + 7 and offset < other_end:
                raise RemoteAssistantError("The assistant state record overlaps another state record.")
        return RemoteAssistantSetting(variable, offset, value)
    except ConfigError as error:
        raise RemoteAssistantError(f"Cannot read Remote Assistant: {error}") from error


def prepare_remote_assistant_candidate(blob: bytes, enabled: bool) -> bytes:
    """Change only the saved prompt state and checksum, retaining all assistant screens."""
    setting = inspect_remote_assistant(blob)
    if setting.enabled == enabled:
        return blob
    try:
        output = bytearray(blob)
        output[setting.offset:setting.offset + 2] = (1 if enabled else 3).to_bytes(2, "little")
        output[-6:-4] = trailer_checksum(output).to_bytes(2, "little")
        candidate = bytes(output)
        validate_image(candidate)
    except ConfigError as error:
        raise RemoteAssistantError(f"Cannot prepare Remote Assistant change: {error}") from error
    if inspect_remote_assistant(candidate).enabled != enabled:
        raise RemoteAssistantError("The generated assistant setting did not match the request.")
    return candidate
