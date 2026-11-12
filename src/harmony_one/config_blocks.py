"""Build a read-only map of the byte ranges in a Harmony One configuration."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.harmony_usb import TRAILER_CHECKSUM_OFFSET


@dataclass(frozen=True)
class ConfigBlock:
    label: str
    kind: str
    start_offset: int
    end_offset: int
    slot: int | None = None

    @property
    def size(self) -> int:
        return self.end_offset - self.start_offset


def build_config_blocks(blob: bytes) -> list[ConfigBlock]:
    """Return contiguous header, section, and trailer ranges for a parsed save."""
    config = parse_config(blob)
    ordered = sorted(
        (start, end, slot) for slot, (start, end) in config.sections.items()
    )
    if not ordered:
        raise ConfigError("Configuration has no data sections to map")

    blocks: list[ConfigBlock] = []
    first_start = ordered[0][0]
    if first_start:
        blocks.append(
            ConfigBlock(
                "Header and section pointer table",
                "header",
                0,
                first_start,
            )
        )
    cursor = first_start
    for start, end, slot in ordered:
        if start != cursor or end < start:
            raise ConfigError(f"Configuration sections do not form a continuous map at {cursor}")
        blocks.append(
            ConfigBlock(
                f"Data section (raw slot {slot:02d})",
                "section",
                start,
                end,
                slot,
            )
        )
        cursor = end

    trailer_start = len(blob) - TRAILER_CHECKSUM_OFFSET
    if cursor != trailer_start:
        raise ConfigError(
            f"Configuration sections end at {cursor}, before trailer at {trailer_start}"
        )
    blocks.append(
        ConfigBlock(
            "Checksum and end marker",
            "trailer",
            trailer_start,
            len(blob),
        )
    )
    if sum(block.size for block in blocks) != len(blob):
        raise ConfigError("Configuration block map does not cover the complete save")
    return blocks
