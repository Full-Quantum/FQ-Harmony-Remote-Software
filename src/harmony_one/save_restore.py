"""Compatibility checks for restoring one complete Harmony One save onto another."""

from __future__ import annotations

from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, RemoteConfig, parse_config
from harmony_one.flash_writer import ERASE_BLOCK
from harmony_one.harmony_usb import CONFIG_FLASH_BASE, CONFIG_FLASH_LIMIT


class SaveRestoreError(ValueError):
    """A complete-save restore is invalid or incompatible with its target."""


def validate_source_save(blob: bytes, label: str = "Source") -> RemoteConfig:
    """Validate one complete image and ensure its padded write fits configuration flash."""
    try:
        validate_image(blob)
        config = parse_config(blob)
    except (ConfigError, ValueError) as error:
        raise SaveRestoreError(f"{label} save is not a valid Harmony One image: {error}") from error
    if config.base_address != CONFIG_FLASH_BASE or config.architecture != 12:
        raise SaveRestoreError(
            f"{label} save is not a supported architecture 12 Harmony One configuration."
        )
    if config.skin is None:
        raise SaveRestoreError(f"{label} save does not identify a screen skin.")
    padded_length = -(-len(blob) // ERASE_BLOCK) * ERASE_BLOCK
    if CONFIG_FLASH_BASE + padded_length > CONFIG_FLASH_LIMIT:
        raise SaveRestoreError(
            f"{label} save and its erase-block padding exceed Harmony One flash capacity."
        )
    return config


def validate_restore_pair(source: bytes, target: bytes) -> tuple[RemoteConfig, RemoteConfig]:
    """Validate both saves and require matching Harmony One architecture and screen skin."""
    source_config = validate_source_save(source, "Source")
    target_config = validate_source_save(target, "Target")
    if source_config.skin != target_config.skin:
        raise SaveRestoreError(
            "Source and target use different Harmony One screen skins "
            f"({source_config.skin} vs {target_config.skin}); refusing a full-save restore."
        )
    if source == target:
        raise SaveRestoreError("Source and target saves are identical; there is nothing to restore.")
    return source_config, target_config
