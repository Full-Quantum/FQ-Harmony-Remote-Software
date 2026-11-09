"""Prepare an experimental fixed-size device IR carrier edit; never writes USB."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config, raw_slot_for_base_slot
from harmony_one.harmony_usb import trailer_checksum
from harmony_one.ir_records import read_ir_groups


@dataclass(frozen=True)
class IrLocation:
    group: int
    record: int
    header: int
    blocks: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class CarrierCandidate:
    image: bytes
    group: int
    record: int
    old_period_ns: int
    new_period_ns: int


def locate_ir_records(blob: bytes) -> list[IrLocation]:
    """Locate every referenced IR header and timing block, including all pointer groups."""
    validate_image(blob)
    config = parse_config(blob)
    section = config.sections.get(raw_slot_for_base_slot(5))
    if section is None:
        raise ConfigError("Configuration has no IR group table")
    limit = len(blob) - 6

    def checked(offset: int, length: int, what: str) -> None:
        if offset < 0 or length < 0 or offset + length > limit:
            raise ConfigError(f"{what} lies outside the configuration content")

    def u24(offset: int) -> int:
        checked(offset, 3, "IR pointer")
        return int.from_bytes(blob[offset:offset + 3], "little")

    start, end = section
    checked(start, 1, "IR group count")
    count = blob[start]
    if start + 1 + 3 * count > end:
        raise ConfigError("IR group pointer table is truncated")
    locations = []
    for group in range(count):
        group_offset = u24(start + 1 + 3 * group) - config.base_address
        checked(group_offset, 3, "IR group header")
        records = int.from_bytes(blob[group_offset + 1:group_offset + 3], "little")
        checked(group_offset + 3, 3 * records, "IR record pointer table")
        for record in range(records):
            header = u24(group_offset + 3 + 3 * record) - config.base_address - 7
            checked(header, 12, "IR record header")
            groups = blob[header + 11]
            checked(header, 12 + 9 * groups, "IR record pointer groups")
            if u24(header + 8) != config.base_address + header:
                raise ConfigError(f"IR group {group}, record {record} has an invalid self pointer")
            if blob[header + 7] != 1:
                raise ConfigError("IR layout census only supports class-1 records")
            blocks = []
            for pointer in range(groups * 3):
                address = u24(header + 12 + 3 * pointer)
                if address:
                    offset = address - config.base_address
                    checked(offset, 2, "IR timing block")
                    end_offset = offset
                    while True:
                        checked(end_offset, 2, "IR timing block")
                        word = int.from_bytes(blob[end_offset:end_offset + 2], "little")
                        end_offset += 2
                        if word == 0:
                            break
                    blocks.append((offset, end_offset))
            locations.append(IrLocation(group, record, header, tuple(blocks)))
    return locations


def prepare_carrier_change(
    blob: bytes, group: int, record: int, carrier_hz: int
) -> CarrierCandidate:
    """Change one class-1 carrier offline; no claim that the resulting code works."""
    if isinstance(carrier_hz, bool) or not isinstance(carrier_hz, int) or not 4_000 <= carrier_hz <= 500_000:
        raise ConfigError("Carrier must be an integer from 4000 to 500000 Hz")
    locations = locate_ir_records(blob)
    targets = [item for item in locations if (item.group, item.record) == (group, record)]
    if len(targets) != 1:
        raise ConfigError("IR group and record do not identify one command")
    target = targets[0]
    target_start, target_end = target.header + 1, target.header + 7
    for other in locations:
        if other != target:
            other_end = other.header + 12 + 9 * blob[other.header + 11]
            if other.header < target_end and target_start < other_end:
                raise ConfigError("The target carrier overlaps another IR record")
        if any(start < target_end and target_start < end for start, end in other.blocks):
            raise ConfigError("An IR timing block overlaps the target carrier")
    old_period = int.from_bytes(blob[target_start:target_start + 3], "little")
    period = 1_000_000_000 // carrier_hz
    if period == old_period:
        raise ConfigError("The selected carrier does not change the stored period")
    replacement = period.to_bytes(3, "little") + (period // 2).to_bytes(3, "little")
    before = read_ir_groups(blob, parse_config(blob))
    image_buffer = bytearray(blob)
    image_buffer[target_start:target_end] = replacement
    image_buffer[-6:-4] = trailer_checksum(image_buffer).to_bytes(2, "little")
    image = bytes(image_buffer)
    validate_image(image)
    after = read_ir_groups(image, parse_config(image))
    for old_group, new_group in zip(before, after):
        for old, new in zip(old_group.records, new_group.records):
            if old_group.index == group and old.index == record:
                if (old.index, old.record_class, old.blocks, old.pointer_groups) != (
                    new.index, new.record_class, new.blocks, new.pointer_groups
                ):
                    raise ConfigError("IR timing or record metadata changed unexpectedly")
                if new.carrier_hz != round(1_000_000_000 / period):
                    raise ConfigError("Candidate carrier did not decode as expected")
            elif old != new:
                raise ConfigError("Another decoded IR command changed unexpectedly")
    return CarrierCandidate(image, group, record, old_period, period)
