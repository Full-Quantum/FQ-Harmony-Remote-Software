"""Planning and safety gates for writing device/activity projects to a Harmony One.

This project-level path is intentionally separate from fixed-size picture and text edits.
It refuses project uploads until a config encoder and relocation have been verified. The
gates that can be built and tested now are:

1. a fresh read-only backup of the remote exists,
2. the container splits into sections and reassembles byte-for-byte,
3. a change plan describes exactly what would differ,
4. the project edits are limited to what the encoder is verified to produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harmony_one.config_reader import ConfigError, parse_config


class WriteBlocked(Exception):
    """Raised when a write is refused; the message lists every unmet gate."""


@dataclass
class ChangePlan:
    devices_added: list[str] = field(default_factory=list)
    devices_removed: list[str] = field(default_factory=list)
    devices_renamed: list[tuple[str, str]] = field(default_factory=list)
    devices_reordered: bool = False
    device_fields_changed: list[tuple[str, list[str]]] = field(default_factory=list)
    activities_added: list[str] = field(default_factory=list)
    activities_removed: list[str] = field(default_factory=list)
    activities_renamed: list[tuple[str, str]] = field(default_factory=list)
    activities_reordered: bool = False
    activities_with_steps_changed: list[str] = field(default_factory=list)
    activity_fields_changed: list[tuple[str, list[str]]] = field(default_factory=list)
    project_fields_changed: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.devices_added, self.devices_removed, self.devices_renamed,
                self.devices_reordered, self.activities_added, self.activities_removed,
                self.activities_renamed, self.activities_reordered,
                self.activities_with_steps_changed, self.device_fields_changed,
                self.activity_fields_changed, self.project_fields_changed,
            )
        )

    def describe(self) -> list[str]:
        lines: list[str] = []
        lines += [f"Add device: {name}" for name in self.devices_added]
        lines += [f"Remove device: {name}" for name in self.devices_removed]
        lines += [f"Rename device: {a} -> {b}" for a, b in self.devices_renamed]
        if self.devices_reordered:
            lines.append("Reorder devices on the Devices page")
        lines += [
            f"Change device {name}: {', '.join(fields)}"
            for name, fields in self.device_fields_changed
        ]
        lines += [f"Add activity: {name}" for name in self.activities_added]
        lines += [f"Remove activity: {name}" for name in self.activities_removed]
        lines += [f"Rename activity: {a} -> {b}" for a, b in self.activities_renamed]
        if self.activities_reordered:
            lines.append("Reorder activities on the Activities page")
        lines += [f"Change steps of activity: {name}" for name in self.activities_with_steps_changed]
        lines += [
            f"Change activity {name}: {', '.join(fields)}"
            for name, fields in self.activity_fields_changed
        ]
        if self.project_fields_changed:
            lines.append(f"Change Remote Data fields: {', '.join(self.project_fields_changed)}")
        return lines or ["No changes."]


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _diff_items(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[list[str], list[str], list[tuple[str, str]], bool, list[str]]:
    old, new = _by_id(before), _by_id(after)
    added = [new[i]["name"] for i in new if i not in old]
    removed = [old[i]["name"] for i in old if i not in new]
    kept = [i for i in new if i in old]
    renamed = [(old[i]["name"], new[i]["name"]) for i in kept if old[i]["name"] != new[i]["name"]]
    reordered = [i for i in old if i in new] != kept
    steps = [
        new[i]["name"] for i in kept
        if ("steps" in old[i]) != ("steps" in new[i])
        or old[i].get("steps") != new[i].get("steps")
    ]
    return added, removed, renamed, reordered, steps


def plan_changes(remote_project: dict[str, Any], edited_project: dict[str, Any]) -> ChangePlan:
    """Compare the project as read from the remote with the user's edited project (by item id)."""
    plan = ChangePlan()
    plan.devices_added, plan.devices_removed, plan.devices_renamed, plan.devices_reordered, _ = (
        _diff_items(remote_project["devices"], edited_project["devices"])
    )
    (
        plan.activities_added, plan.activities_removed, plan.activities_renamed,
        plan.activities_reordered, plan.activities_with_steps_changed,
    ) = _diff_items(remote_project["activities"], edited_project["activities"])
    for collection, excluded, target in (
        ("devices", {"id", "name"}, plan.device_fields_changed),
        ("activities", {"id", "name", "steps"}, plan.activity_fields_changed),
    ):
        old = _by_id(remote_project[collection])
        for item in edited_project[collection]:
            before = old.get(item["id"])
            if before is None:
                continue
            keys = (before.keys() | item.keys()) - excluded
            changed = sorted(key for key in keys if key not in before or key not in item
                             or before[key] != item[key])
            if changed:
                target.append((item["name"], changed))
    keys = (remote_project.keys() | edited_project.keys()) - {"devices", "activities"}
    plan.project_fields_changed = sorted(
        key for key in keys if key not in remote_project or key not in edited_project
        or remote_project[key] != edited_project[key]
    )
    return plan


def split_container(blob: bytes) -> tuple[bytes, list[bytes], bytes]:
    """Split a configuration into (header, sections, trailer) using the parsed section bounds."""
    config = parse_config(blob)
    ordered = sorted(config.sections.values())
    if not ordered:
        raise ConfigError("configuration has no sections")
    header = blob[: ordered[0][0]]
    sections: list[bytes] = []
    cursor = ordered[0][0]
    for start, end in ordered:
        if start != cursor:
            raise ConfigError(f"gap or overlap in sections at offset {cursor}")
        sections.append(blob[start:end])
        cursor = end
    return header, sections, blob[cursor:]


def verify_container_roundtrip(blob: bytes) -> bool:
    """True when the container splits and reassembles to exactly the original bytes."""
    try:
        header, sections, trailer = split_container(blob)
    except ConfigError:
        return False
    return header + b"".join(sections) + trailer == blob


def check_gates(
    blob: bytes | None, backup_path: Path | None, plan: ChangePlan
) -> list[str]:
    """Return the unmet gates; an empty list means every software gate passed."""
    problems: list[str] = []
    if blob is None or backup_path is None or not backup_path.exists():
        problems.append("No fresh read-only backup of the connected remote was taken.")
    elif not verify_container_roundtrip(blob):
        problems.append("The remote's configuration did not round-trip byte-for-byte.")
    if plan.is_empty:
        problems.append("There are no changes to write.")
    return problems


def write_project_to_remote(
    blob: bytes | None, backup_path: Path | None, plan: ChangePlan
) -> None:
    problems = check_gates(blob, backup_path, plan)
    problems.append(
        "Local Remote Data editor changes cannot be uploaded as a complete remote save. "
        "To transfer a complete raw .EZHex snapshot between remotes, use the Transfer "
        "Remote tab; it does not merge or include local editor changes."
    )
    raise WriteBlocked("\n".join(problems))
