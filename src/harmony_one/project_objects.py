"""Portable local project packages for individual devices and activities."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from harmony_one.harmony_project import ProjectError, validate_project


FORMAT = "fq-harmony-project-object"
VERSION = 1


def export_project_object(
    project: dict[str, Any], object_type: str, object_id: str
) -> dict[str, Any]:
    """Export one local object, including device dependencies for an activity."""
    validate_project(project)
    if object_type == "device":
        item = next((one for one in project["devices"] if one["id"] == object_id), None)
        if item is None:
            raise ProjectError(f"Device {object_id!r} was not found")
        return {
            "format": FORMAT,
            "schema_version": VERSION,
            "object_type": "device",
            "transfer_ready": False,
            "item": copy.deepcopy(item),
            "dependency_devices": [],
        }
    if object_type != "activity":
        raise ProjectError("Object type must be 'device' or 'activity'")

    item = next((one for one in project["activities"] if one["id"] == object_id), None)
    if item is None:
        raise ProjectError(f"Activity {object_id!r} was not found")
    dependencies = {
        step["device_id"] for step in item["steps"] if "device_id" in step
    }
    dependencies.update(item.get("participating_device_ids", []))
    dependencies.update(
        item[key] for key in ("channel_device_id", "volume_device_id") if item.get(key)
    )
    dependencies.update(item.get("inputs", {}).keys())
    devices = [
        copy.deepcopy(device)
        for device in project["devices"]
        if device["id"] in dependencies
    ]
    return {
        "format": FORMAT,
        "schema_version": VERSION,
        "object_type": "activity",
        "transfer_ready": False,
        "item": copy.deepcopy(item),
        "dependency_devices": devices,
    }


def import_project_object(
    project: dict[str, Any], package: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Import one package into a project with fresh IDs and validated references."""
    validate_project(project)
    if not isinstance(package, dict) or package.get("format") != FORMAT:
        raise ProjectError("Not a Harmony Remote Data object package")
    if package.get("schema_version") != VERSION:
        raise ProjectError(f"Unsupported Remote Data object schema; expected {VERSION}")
    if package.get("transfer_ready") is not False:
        raise ProjectError("Remote Data object packages are never accepted as upload-ready")

    object_type = package.get("object_type")
    item = package.get("item")
    dependencies = package.get("dependency_devices")
    if not isinstance(item, dict) or not isinstance(dependencies, list):
        raise ProjectError("Remote Data object package has malformed item or dependencies")

    imported = copy.deepcopy(item)
    target = copy.deepcopy(project)
    if object_type == "device":
        if dependencies:
            raise ProjectError("A device package cannot have dependencies")
        imported["id"] = uuid.uuid4().hex
        target["devices"].append(imported)
    elif object_type == "activity":
        try:
            validate_project({
                "formatVersion": 1,
                "devices": dependencies,
                "activities": [item],
            })
        except ProjectError as error:
            raise ProjectError(f"Activity package is invalid: {error}") from error
        source_ids: set[str] = set()
        id_map: dict[str, str] = {}
        imported_devices = []
        for device in dependencies:
            if not isinstance(device, dict):
                raise ProjectError("Activity dependency must be a device object")
            source_id = device.get("id")
            if not isinstance(source_id, str) or not source_id or source_id in source_ids:
                raise ProjectError("Activity dependencies must have unique, non-empty IDs")
            source_ids.add(source_id)
            id_map[source_id] = uuid.uuid4().hex
            copied = copy.deepcopy(device)
            copied["id"] = id_map[source_id]
            imported_devices.append(copied)

        referenced_ids = {
            step["device_id"] for step in imported.get("steps", [])
            if isinstance(step, dict) and "device_id" in step
        }
        referenced_ids.update(imported.get("participating_device_ids", []))
        referenced_ids.update(
            imported[key] for key in ("channel_device_id", "volume_device_id")
            if imported.get(key)
        )
        inputs = imported.get("inputs", {})
        if isinstance(inputs, dict):
            referenced_ids.update(inputs)
        if referenced_ids != source_ids:
            raise ProjectError("Activity package dependencies do not match its device references")

        def remap(device_id: str) -> str:
            return id_map[device_id]

        for step in imported.get("steps", []):
            if isinstance(step, dict) and "device_id" in step:
                step["device_id"] = remap(step["device_id"])
        if "participating_device_ids" in imported:
            imported["participating_device_ids"] = [
                remap(device_id) for device_id in imported["participating_device_ids"]
            ]
        for key in ("channel_device_id", "volume_device_id"):
            if imported.get(key) is not None:
                imported[key] = remap(imported[key])
        if isinstance(inputs, dict):
            imported["inputs"] = {
                remap(device_id): value for device_id, value in inputs.items()
            }
        imported["id"] = uuid.uuid4().hex
        target["devices"].extend(imported_devices)
        target["activities"].append(imported)
    else:
        raise ProjectError("Object type must be 'device' or 'activity'")

    validate_project(target)
    return target, imported