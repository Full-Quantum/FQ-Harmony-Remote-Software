"""Lossless, fixed-length configuration patches for future remote encoders.

This module produces images only. It never writes to a remote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.harmony_usb import CONFIG_FLASH_BASE, trailer_checksum


@dataclass(frozen=True)
class BytePatch:
    offset: int
    expected: bytes
    replacement: bytes
    description: str


def validate_image(blob: bytes) -> None:
    """Validate the known container envelope, without claiming semantic validity."""
    config = parse_config(blob)
    if config.architecture != 12 or config.base_address != CONFIG_FLASH_BASE:
        raise ConfigError("Expected an architecture 12 Harmony One configuration at 0x040000")
    stored = int.from_bytes(blob[-6:-4], "little")
    computed = trailer_checksum(blob)
    if stored != computed:
        raise ConfigError(
            f"Configuration checksum mismatch: stored 0x{stored:04x}, computed 0x{computed:04x}"
        )


def patch_image(blob: bytes, patches: Iterable[BytePatch]) -> bytes:
    """Apply nonoverlapping same-size patches, preserving all other bytes.

    The caller must establish the meaning and ownership of each target. This
    does not validate device/activity semantics and is not authorization to write.
    """
    validate_image(blob)
    config = parse_config(blob)
    ordered = sorted(patches, key=lambda patch: patch.offset)
    previous_end = -1
    for patch in ordered:
        if not patch.description.strip():
            raise ConfigError("Every patch must describe its intended change")
        if not patch.expected or len(patch.expected) != len(patch.replacement):
            raise ConfigError("Patches must be nonempty and preserve length")
        end = patch.offset + len(patch.expected)
        if not any(start <= patch.offset < end <= stop for start, stop in config.sections.values()):
            raise ConfigError(f"Patch {patch.description!r} is outside a configuration section")
        if patch.offset < previous_end:
            raise ConfigError("Overlapping patches are not permitted")
        if blob[patch.offset:end] != patch.expected:
            raise ConfigError(f"Source bytes changed for patch {patch.description!r}")
        previous_end = end
    if not ordered:
        return blob
    result = bytearray(blob)
    for patch in ordered:
        result[patch.offset:patch.offset + len(patch.expected)] = patch.replacement
    result[-6:-4] = trailer_checksum(result).to_bytes(2, "little")
    validate_image(bytes(result))
    return bytes(result)
