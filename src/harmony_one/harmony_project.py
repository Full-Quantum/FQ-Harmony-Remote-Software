"""Local project storage and validation for the offline Harmony editor."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from harmony_one.activity_icons import IconError, validate_icon

PROJECT_VERSION = 1
MIN_DELAY_MS = 100
MAX_DELAY_MS = 10000


class ProjectError(ValueError):
    """Raised when a Harmony project is malformed or inconsistent."""


def new_project() -> dict[str, Any]:
    return {"formatVersion": PROJECT_VERSION, "devices": [], "activities": []}


def device_from_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    device = catalog.get("device")
    signals = catalog.get("signals")
    if not isinstance(device, dict) or not isinstance(signals, list) or not signals:
        raise ProjectError("IR catalog must contain device metadata and signals")

    commands: list[dict[str, str]] = []
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("type") != "parsed":
            raise ProjectError("Only parsed IR signals can be added to a device")
        for field in ("name", "protocol", "address", "command"):
            value = signal.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ProjectError(f"IR signal is missing a valid {field}")
        commands.append(
            {
                "name": signal["name"],
                "protocol": signal["protocol"],
                "address": signal["address"],
                "command": signal["command"],
            }
        )

    result = {
        "id": uuid.uuid4().hex,
        "name": str(device.get("name", "")).strip(),
        "brand": str(device.get("brand", "")).strip(),
        "category": str(device.get("category", "")).strip(),
        "commands": commands,
    }
    if not result["name"]:
        raise ProjectError("IR catalog device name cannot be empty")
    return result


def move_item(items: list[Any], index: int, offset: int) -> int:
    """Swap an item with its neighbour and return its new index (unchanged if out of range)."""
    target = index + offset
    if not 0 <= index < len(items) or not 0 <= target < len(items):
        return index
    items[index], items[target] = items[target], items[index]
    return target


def project_from_remote(config: Any, data: bytes | None = None) -> dict[str, Any]:
    """Build a project from a parsed remote backup.

    With the raw container (`data`) the devices carry their infrared codes and the
    activities carry their decoded names and enter-sequence steps. Without it only
    device names and the activity count are imported. Imported items carry
    source="remote" so the editor can flag them.
    """
    if data is not None:
        return _project_from_container(config, data)
    project = new_project()
    for label in config.device_labels:
        project["devices"].append(
            {
                "id": uuid.uuid4().hex,
                "name": label,
                "brand": "",
                "category": "",
                "commands": [],
                "source": "remote",
            }
        )
    for number in range(1, config.activity_count + 1):
        project["activities"].append(
            {
                "id": uuid.uuid4().hex,
                "name": f"Activity {number} (name not decoded)",
                "steps": [],
                "source": "remote",
            }
        )
    return project


def _project_from_container(config: Any, data: bytes) -> dict[str, Any]:
    from harmony_one.activities import read_activities, read_devices
    from harmony_one.ir_records import read_ir_groups

    project = new_project()
    groups = {group.index: group for group in read_ir_groups(data, config)}
    ids: dict[str, str] = {}
    for device in read_devices(data, config):
        ids[device.label] = uuid.uuid4().hex
        commands = []
        group = groups.get(device.group) if device.group is not None else None
        for record in group.records if group else ():
            commands.append(
                {
                    "name": f"Code {record.index}",
                    "protocol": f"raw-ir {record.carrier_hz} Hz",
                    "address": f"group {device.group}",
                    "command": f"record {record.index}",
                }
            )
        project["devices"].append(
            {
                "id": ids[device.label],
                "name": device.label,
                "brand": "",
                "category": "",
                "commands": commands,
                "source": "remote",
            }
        )
    for activity in read_activities(data, config):
        steps = []
        for step in activity.steps:
            if step.kind == "power":
                steps.append(
                    {"type": "state", "device_id": ids[step.device],
                     "label": "Power on" if step.value else "Power off"}
                )
            elif step.kind == "input":
                steps.append(
                    {"type": "state", "device_id": ids[step.device], "label": f"Input setting {step.value}"}
                )
        project["activities"].append(
            {"id": uuid.uuid4().hex, "name": activity.name, "steps": steps, "source": "remote"}
        )
    return project



def validate_project(project: Any) -> dict[str, Any]:
    if not isinstance(project, dict):
        raise ProjectError("Remote Data must be a JSON object")
    if project.get("formatVersion") != PROJECT_VERSION:
        raise ProjectError(f"Unsupported Remote Data format; expected version {PROJECT_VERSION}")

    devices = project.get("devices")
    activities = project.get("activities")
    if not isinstance(devices, list) or not isinstance(activities, list):
        raise ProjectError("Remote Data must contain device and activity lists")

    device_ids: set[str] = set()
    command_names: dict[str, set[str]] = {}
    for device in devices:
        if not isinstance(device, dict):
            raise ProjectError("Every device must be an object")
        device_id = device.get("id")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ProjectError("Every device must have a non-empty id")
        if device_id in device_ids:
            raise ProjectError(f"Duplicate device id {device_id!r}")
        device_ids.add(device_id)
        for field in ("name", "brand", "category"):
            if not isinstance(device.get(field), str):
                raise ProjectError(f"Device {device_id!r} must have a {field} string")
        if not device["name"].strip():
            raise ProjectError(f"Device {device_id!r} must have a non-empty name")
        commands = device.get("commands")
        if not isinstance(commands, list):
            raise ProjectError(f"Device {device_id!r} must have a command list")
        names: set[str] = set()
        for command in commands:
            if not isinstance(command, dict):
                raise ProjectError(f"Device {device_id!r} has a malformed command")
            name = command.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ProjectError(f"Device {device_id!r} has a command without a name")
            if name in names:
                raise ProjectError(f"Device {device_id!r} has duplicate command {name!r}")
            names.add(name)
            for field in ("protocol", "address", "command"):
                if not isinstance(command.get(field), str) or not command[field].strip():
                    raise ProjectError(
                        f"Command {name!r} on device {device_id!r} must have a non-empty {field}"
                    )
        command_names[device_id] = names

    activity_ids: set[str] = set()
    for activity in activities:
        if not isinstance(activity, dict):
            raise ProjectError("Every activity must be an object")
        activity_id = activity.get("id")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ProjectError("Every activity must have a non-empty id")
        if activity_id in activity_ids:
            raise ProjectError(f"Duplicate activity id {activity_id!r}")
        activity_ids.add(activity_id)
        if not isinstance(activity.get("name"), str) or not activity["name"].strip():
            raise ProjectError(f"Activity {activity_id!r} must have a name")
        if "icon" in activity:
            try:
                validate_icon(activity["icon"])
            except IconError as error:
                raise ProjectError(f"Activity {activity['name']!r}: {error}") from error
        has_participating = "participating_device_ids" in activity
        participating = activity.get("participating_device_ids", [])
        if not isinstance(participating, list) or any(
            not isinstance(device_id, str) or device_id not in device_ids
            for device_id in participating
        ):
            raise ProjectError(
                f"Activity {activity['name']!r} has an invalid participating-device list"
            )
        if len(set(participating)) != len(participating):
            raise ProjectError(
                f"Activity {activity['name']!r} has duplicate participating devices"
            )
        for role in ("channel_device_id", "volume_device_id"):
            role_device = activity.get(role)
            if role_device is not None and (
                not isinstance(role_device, str)
                or role_device not in device_ids
                or (has_participating and role_device not in participating)
            ):
                raise ProjectError(
                    f"Activity {activity['name']!r} has an invalid {role.replace('_', ' ')}"
                )
        inputs = activity.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ProjectError(f"Activity {activity['name']!r} has malformed input selections")
        for device_id, input_name in inputs.items():
            if (
                not isinstance(device_id, str)
                or device_id not in device_ids
                or (has_participating and device_id not in participating)
                or not isinstance(input_name, str)
                or not input_name.strip()
            ):
                raise ProjectError(
                    f"Activity {activity['name']!r} has an invalid device input selection"
                )
        steps = activity.get("steps")
        if not isinstance(steps, list):
            raise ProjectError(f"Activity {activity_id!r} must have a step list")
        for step in steps:
            if not isinstance(step, dict):
                raise ProjectError(f"Activity {activity_id!r} has a malformed step")
            if step.get("type") == "delay":
                ms = step.get("ms")
                if (
                    not isinstance(ms, int)
                    or isinstance(ms, bool)
                    or not MIN_DELAY_MS <= ms <= MAX_DELAY_MS
                ):
                    raise ProjectError(
                        f"Activity {activity['name']!r} has a delay outside "
                        f"{MIN_DELAY_MS}-{MAX_DELAY_MS} ms"
                    )
                continue
            if step.get("type") == "state":
                if step.get("device_id") not in device_ids or not isinstance(step.get("label"), str):
                    raise ProjectError(f"Activity {activity['name']!r} has a malformed state step")
                continue
            if step.get("type", "command") != "command":
                raise ProjectError(f"Activity {activity['name']!r} has an unknown step type")
            sequence_name = step.get("sequence_name")
            if sequence_name is not None and (
                not isinstance(sequence_name, str) or not sequence_name.strip()
            ):
                raise ProjectError(f"Activity {activity['name']!r} has an invalid sequence name")
            device_id = step.get("device_id")
            command_name = step.get("command_name")
            if not isinstance(device_id, str) or not isinstance(command_name, str):
                raise ProjectError(
                    f"Activity {activity['name']!r} has a step without a device or command name"
                )
            if device_id not in device_ids:
                raise ProjectError(
                    f"Activity {activity['name']!r} refers to an unknown device"
                )
            if command_name not in command_names[device_id]:
                raise ProjectError(
                    f"Activity {activity['name']!r} refers to an unknown device command"
                )

    return project


def load_project(path: Path) -> dict[str, Any]:
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectError(f"Could not load Remote Data {path}: {error}") from error
    return validate_project(project)


def save_project(path: Path, project: dict[str, Any]) -> None:
    validate_project(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(project, temporary, indent=2, ensure_ascii=False)
            temporary.write("\n")
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ProjectError(f"Could not save Remote Data {path}: {error}") from error
