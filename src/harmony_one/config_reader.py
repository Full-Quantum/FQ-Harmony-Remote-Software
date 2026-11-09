"""Read-only parser for a Harmony One configuration container (GSPM).

Format knowledge comes from public documentation (dannybloe/harmony-explorations,
MIT) and from the owner's own remote backup. Nothing here writes to a remote.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

COOKIE = b"GSPM"
END_MARKER = b"PTYY"
ONE_ARCHITECTURE = 12
NAME_NODE_TAG = 0xA7


class ConfigError(ValueError):
    """Raised when a configuration container cannot be read."""


@dataclass(frozen=True)
class NameNode:
    level: int
    index: int
    name: str


@dataclass(frozen=True)
class StateVariable:
    index: int
    label: str
    values: int | None
    name: str


@dataclass
class RemoteConfig:
    size: int
    base_address: int
    architecture: int | None
    skin: int | None
    pointers: list[int]
    sections: dict[int, tuple[int, int]]
    names: list[NameNode] = field(default_factory=list)

    @property
    def state_variables(self) -> list[StateVariable]:
        variables = []
        for node in self.names:
            if node.level != 1:
                continue
            head, _, tail = node.name.rpartition("_")
            values = int(tail) if head and tail.isdigit() else None
            variables.append(
                StateVariable(node.index, head if values is not None else node.name, values, node.name)
            )
        return sorted(variables, key=lambda variable: variable.index)

    @property
    def activity_count(self) -> int | None:
        for variable in self.state_variables:
            if variable.name.startswith("CurrentActivityState_0_") and variable.values:
                # The counter's value count includes the idle value.
                return variable.values - 1
        return None

    @property
    def device_labels(self) -> list[str]:
        """Device names inferred from '<device>_Power'/'<device>_Input' variables."""
        devices: dict[str, None] = {}
        for variable in self.state_variables:
            for suffix in ("_Power", "_Input"):
                if variable.label.endswith(suffix):
                    devices[variable.label[: -len(suffix)].replace("_", " ")] = None
        return sorted(devices, key=str.casefold)


def raw_slot_for_base_slot(base_slot: int) -> int:
    """Map a base slot to the Harmony One's raw section-table slot."""
    if base_slot >= 17:
        return base_slot + 2
    if base_slot >= 8:
        return base_slot + 1
    return base_slot


def parse_config(data: bytes) -> RemoteConfig:
    if len(data) < 0x70 or data[:4] != COOKIE:
        raise ConfigError("Not a Harmony One (GSPM) configuration container")
    if data[-4:] != END_MARKER:
        raise ConfigError("Configuration is missing the PTYY end marker")

    end_address = struct.unpack_from("<I", data, 4)[0]
    count = data[9]
    if count != 22 or 0x0B + 4 * count > len(data):
        raise ConfigError(f"Unexpected section table length {count}; expected 22")
    base = end_address - (len(data) - 4)

    pointers = [
        int.from_bytes(data[0x0C + 4 * index : 0x0F + 4 * index], "little")
        for index in range(count)
    ]
    ordered = sorted((pointer, slot) for slot, pointer in enumerate(pointers) if pointer)
    if [slot for _, slot in ordered] != sorted(slot for _, slot in ordered):
        raise ConfigError("Section pointers do not ascend with slot number")

    trailer = len(data) - 6
    sections: dict[int, tuple[int, int]] = {}
    for position, (pointer, slot) in enumerate(ordered):
        start = pointer - base
        end = ordered[position + 1][0] - base if position + 1 < len(ordered) else trailer
        if not 0 <= start <= end <= len(data):
            raise ConfigError(f"Section {slot} lies outside the container")
        sections[slot] = (start, end)

    architecture = skin = None
    arch_range = sections.get(raw_slot_for_base_slot(1))
    if arch_range and arch_range[1] - arch_range[0] >= 4:
        record = data[arch_range[0] : arch_range[0] + 4]
        architecture, skin = record[0], record[2]

    config = RemoteConfig(len(data), base, architecture, skin, pointers, sections)
    name_range = sections.get(raw_slot_for_base_slot(0))
    if name_range:
        config.names = parse_name_tree(data, name_range[0])
    return config


def parse_name_tree(data: bytes, start: int) -> list[NameNode]:
    if data[start : start + 2] != b"\xed\xfe":
        raise ConfigError("Name tree does not start with 0xFEED")
    length = int.from_bytes(data[start + 2 : start + 5], "little")
    end = start + length
    if data[end : end + 2] != b"\xef\xbe":
        raise ConfigError("Name tree does not end with 0xBEEF")
    position = start + 5
    nodes: list[NameNode] = []
    while position < end:
        if data[position] != NAME_NODE_TAG:
            raise ConfigError(f"Unexpected name node tag at {position}")
        size, level, index = struct.unpack_from("<HHH", data, position + 1)
        if size < 4:
            raise ConfigError("Name node is too short")
        name = data[position + 7 : position + 3 + size].decode("ascii", errors="replace")
        nodes.append(NameNode(level, index, name))
        position += 3 + size
    if position != end:
        raise ConfigError("Name nodes do not tile the name tree")
    return nodes
