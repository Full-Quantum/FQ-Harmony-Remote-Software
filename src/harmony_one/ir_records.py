"""Decode the infrared database (base slot 5): groups, records, carriers, timings.

Layout comes from the public reference (dannybloe/harmony-explorations, MIT):
base slot 5 is `u8 count, u24 group_address[count]`; a group is
`u8 zero, u16 count, u24 record_address[count]`; a record pointer lands on the
class byte, seven bytes into the record. Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_reader import RemoteConfig, raw_slot_for_base_slot

IR_BASE_SLOT = 5


@dataclass(frozen=True)
class IrRecord:
    index: int
    carrier_hz: int
    record_class: int
    blocks: tuple[tuple[int, ...] | None, ...]  # once, held, tail of the first pointer group
    pointer_groups: int

    @property
    def once(self) -> tuple[int, ...] | None:
        return self.blocks[0] if self.blocks else None


@dataclass(frozen=True)
class IrGroup:
    index: int
    records: tuple[IrRecord, ...]


def _u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "little")


def _read_block(data: bytes, offset: int) -> tuple[int, ...]:
    """Durations in microseconds; a mark is positive, a space negative."""
    durations = []
    while offset + 2 <= len(data):
        word = int.from_bytes(data[offset : offset + 2], "little")
        if word == 0:
            break
        micros = word & 0x7FFF
        durations.append(micros if word & 0x8000 else -micros)
        offset += 2
    return tuple(durations)


def read_ir_groups(data: bytes, config: RemoteConfig) -> list[IrGroup]:
    section = config.sections.get(raw_slot_for_base_slot(IR_BASE_SLOT))
    if not section:
        return []
    base = config.base_address

    def local(address: int) -> int:
        return address - base

    start = section[0]
    group_count = data[start]
    groups = []
    for group_index in range(group_count):
        group_at = local(_u24(data, start + 1 + 3 * group_index))
        record_count = int.from_bytes(data[group_at + 1 : group_at + 3], "little")
        records = []
        for record_index in range(record_count):
            pointer = local(_u24(data, group_at + 3 + 3 * record_index))
            header = pointer - 7
            period_ns = _u24(data, header + 1)
            pointer_groups = data[header + 0x0B]
            blocks = []
            for slot in range(3 if pointer_groups else 0):
                address = _u24(data, header + 0x0C + 3 * slot)
                blocks.append(_read_block(data, local(address)) if address else None)
            records.append(
                IrRecord(
                    record_index,
                    round(1e9 / period_ns) if period_ns else 0,
                    data[pointer],
                    tuple(blocks),
                    pointer_groups,
                )
            )
        groups.append(IrGroup(group_index, tuple(records)))
    return groups
