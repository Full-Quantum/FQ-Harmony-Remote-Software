"""Prepare a limited activity label edit for offline inspection, not USB writing."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.activities import read_activities, read_devices
from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.fonts import read_font_sets
from harmony_one.rename import RenameError, rename_text
from harmony_one.screens import read_mode_pages
from harmony_one.text_decode import decode_text, load_bundled_alphabet


@dataclass(frozen=True)
class ActivityRenameCandidate:
    image: bytes
    places_changed: int
    changed_bytes: int


def prepare_activity_rename(blob: bytes, old: str, new: str) -> ActivityRenameCandidate:
    """Produce a same-length rename candidate; semantic and hardware validation remain open."""
    validate_image(blob)
    if not old or not new.strip() or old == new or len(old) != len(new):
        raise RenameError("Use a different, nonempty activity name of the same length")
    config = parse_config(blob)
    activities = read_activities(blob, config)
    names = [activity.name for activity in activities]
    if names.count(old) != 1:
        raise RenameError("The original activity name must identify exactly one activity")
    if new in names:
        raise RenameError("The new activity name already exists")
    if any(old in device.label for device in read_devices(blob, config)):
        raise RenameError("The activity name overlaps a device label")

    fonts = read_font_sets(blob, config)
    alphabet = load_bundled_alphabet()
    def normalized(text: str) -> str:
        return text.replace("I", "l")

    matches = set()
    for page in read_mode_pages(blob, config):
        for draw in page.draws:
            if draw.font >= len(fonts):
                continue
            text = decode_text(blob, config, draw, alphabet, fonts)
            if normalized(old) in normalized(text):
                if normalized(text) not in (
                    normalized(old), normalized("Starting " + old)
                ):
                    raise RenameError(f"Activity name occurs in unexpected text: {text!r}")
                matches.add(draw.address)
    if not matches:
        raise RenameError("No matching activity labels could be decoded")

    candidate, changed = rename_text(blob, old, new)
    if changed != len(matches):
        raise ConfigError("Candidate changed an unexpected number of text locations")
    validate_image(candidate)
    updated = read_activities(candidate, parse_config(candidate))
    expected_names = [new if name == old else name for name in names]
    if [activity.name for activity in updated] != expected_names:
        raise ConfigError("Candidate did not decode to the requested activity names")
    for before, after in zip(activities, updated):
        if (before.number, before.key_map, before.mode, before.steps, before.devices) != (
            after.number, after.key_map, after.mode, after.steps, after.devices
        ):
            raise ConfigError("Candidate unexpectedly changed decoded activity structure")
    if len(candidate) != len(blob):
        raise ConfigError("Candidate changed the configuration length")
    return ActivityRenameCandidate(
        candidate, changed, sum(a != b for a, b in zip(blob, candidate))
    )
