"""Follow a key press to the infrared codes it sends (read-only).

Chain: base slot 9 entry -> tagged list -> press binding (opcode 0x7F, operand =
index into base slot 10) -> action list -> 0x7D operands (infrared group, record).
Layouts are from the public reference (dannybloe/harmony-explorations, MIT).
"""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_reader import ConfigError, RemoteConfig, raw_slot_for_base_slot

OP_SEND = 0x7D
OP_CALL = 0x7F
OP_DELAY = 0x7C
EVENT_PRESS = 0x80
EVENT_MASK = 0xC0
SCAN_MASK = 0x3F


@dataclass(frozen=True)
class Instruction:
    opcode: int
    operand: int


@dataclass(frozen=True)
class Binding:
    tag: int
    operand: int
    opcode: int

    @property
    def is_press(self) -> bool:
        return self.tag & EVENT_MASK == EVENT_PRESS

    @property
    def scan_code(self) -> int:
        return self.tag & SCAN_MASK


@dataclass(frozen=True)
class KeyMap:
    index: int
    bindings: tuple[Binding, ...]


@dataclass(frozen=True)
class LocatedActionList:
    index: int
    offset: int
    instructions: tuple[Instruction, ...]


def locate_action_lists(data: bytes, config: RemoteConfig) -> list[LocatedActionList]:
    """Read action lists with their source offsets, rejecting truncated pointers and bodies.

    Offsets may be shared or overlap; callers must check all references and
    ownership before considering any edit.
    """
    section = config.sections.get(raw_slot_for_base_slot(10))
    if section is None:
        return []
    start, end = section
    if start + 2 > end:
        raise ConfigError("Action-list table is truncated")
    count = int.from_bytes(data[start:start + 2], "little")
    if start + 2 + count * 3 > end:
        raise ConfigError("Action-list pointer table is truncated")
    lists = []
    for index in range(count):
        offset = _u24(data, start + 2 + index * 3) - config.base_address
        if not 0 <= offset < len(data) - 6:
            raise ConfigError(f"Action-list {index} points outside configuration content")
        length = data[offset]
        if offset + 1 + length * 3 > len(data) - 6:
            raise ConfigError(f"Action-list {index} is truncated")
        instructions = tuple(
            Instruction(
                data[offset + 3 + n * 3],
                int.from_bytes(data[offset + 1 + n * 3:offset + 3 + n * 3], "little"),
            )
            for n in range(length)
        )
        lists.append(LocatedActionList(index, offset, instructions))
    return lists


def _u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "little")


def _slot_start(config: RemoteConfig, base_slot: int) -> int | None:
    section = config.sections.get(raw_slot_for_base_slot(base_slot))
    return section[0] if section else None


def read_action_lists(data: bytes, config: RemoteConfig) -> list[tuple[Instruction, ...]]:
    start = _slot_start(config, 10)
    if start is None:
        return []
    count = int.from_bytes(data[start : start + 2], "little")
    lists = []
    for index in range(count):
        at = _u24(data, start + 2 + 3 * index) - config.base_address
        length = data[at]
        lists.append(
            tuple(
                Instruction(data[at + 3 + 3 * n], int.from_bytes(data[at + 1 + 3 * n : at + 3 + 3 * n], "little"))
                for n in range(length)
            )
        )
    return lists


def read_tagged_list(data: bytes, at: int) -> tuple[Binding, ...]:
    if data[at] != 0:
        count, offset, stride, tag_at = data[at], at + 1, 4, 0
    else:
        count, offset, stride, tag_at = data[at + 1], at + 2, 5, 1
    bindings = []
    for n in range(count):
        entry = offset + stride * n
        bindings.append(
            Binding(
                data[entry + tag_at],
                int.from_bytes(data[entry + tag_at + 1 : entry + tag_at + 3], "little"),
                data[entry + tag_at + 3],
            )
        )
    return tuple(bindings)


def read_key_maps(data: bytes, config: RemoteConfig) -> list[KeyMap]:
    start = _slot_start(config, 9)
    if start is None:
        return []
    maps = []
    for index in range(data[start]):
        at = _u24(data, start + 1 + 3 * index) - config.base_address
        maps.append(KeyMap(index, read_tagged_list(data, at)))
    return maps


def codes_sent(
    binding: Binding, action_lists: list[tuple[Instruction, ...]]
) -> list[tuple[int, int]]:
    """(infrared group, record index) pairs a binding sends, in order; empty if none."""
    if binding.opcode != OP_CALL or binding.operand >= len(action_lists):
        return []
    return [
        (item.operand >> 8, item.operand & 0xFF)
        for item in action_lists[binding.operand]
        if item.opcode == OP_SEND
    ]
