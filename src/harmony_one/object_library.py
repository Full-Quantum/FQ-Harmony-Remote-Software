"""Export recovered remote objects without writing to a remote."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from dataclasses import asdict
from pathlib import Path
from typing import Any

from harmony_one.activities import read_activities, read_devices
from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.harmony_usb import CONFIG_FLASH_BASE, trailer_checksum
from harmony_one.ir_records import read_ir_groups
from harmony_one.key_bindings import OP_CALL, codes_sent, read_action_lists, read_key_maps

FORMAT = "fq-harmony-object"
VERSION = 1


def build_remote_objects(data: bytes) -> dict[str, Any]:
    """Return decoded, source-specific objects; these are not uploadable."""
    config = parse_config(data)
    if config.architecture != 12 or config.base_address != CONFIG_FLASH_BASE:
        raise ConfigError("Object export requires a Harmony One architecture 12 backup")
    stored = int.from_bytes(data[-6:-4], "little")
    if stored != trailer_checksum(data):
        raise ConfigError("Backup trailer checksum does not match; object export refused")

    digest = hashlib.sha256(data).hexdigest()
    prefix = digest[:16]
    source = {"backup_sha256": digest, "architecture": config.architecture, "skin": config.skin}
    devices = read_devices(data, config)
    activities = read_activities(data, config)
    ir_groups = {group.index: group for group in read_ir_groups(data, config)}
    action_lists = read_action_lists(data, config)
    key_maps = {key_map.index: key_map for key_map in read_key_maps(data, config)}
    device_ids = [f"{prefix}-device-{index:03d}" for index in range(len(devices))]
    label_ids: dict[str, list[str]] = {}
    group_ids: dict[int, list[str]] = {}
    for device, object_id in zip(devices, device_ids):
        label_ids.setdefault(device.label, []).append(object_id)
        if device.group is not None:
            group_ids.setdefault(device.group, []).append(object_id)

    objects = []
    for device, object_id in zip(devices, device_ids):
        group = ir_groups.get(device.group)
        records = []
        if group is not None:
            for record in group.records:
                records.append({
                    "index": record.index,
                    "carrier_hz": record.carrier_hz,
                    "record_class": record.record_class,
                    "pointer_groups": record.pointer_groups,
                    "first_pointer_group_blocks_us": {
                        name: list(block) if block is not None else None
                        for name, block in zip(("once", "held", "tail"), record.blocks)
                    },
                })
        objects.append({
            "format": FORMAT, "schema_version": VERSION, "object_type": "device",
            "object_id": object_id, "name": device.label, "source": source,
            "source_ir_group": device.group, "source_state_variables": device.variables,
            "ir_records": records, "transfer_ready": False,
            "limitations": [
                "Device identity and original command names are not decoded.",
                "IR group association is inferred and can be missing or ambiguous.",
                "Only the first IR pointer group's timing blocks are decoded; marks are positive and spaces negative microseconds.",
                "Not directly importable into another remote; encoding and remapping are not implemented.",
            ],
        })

    for index, activity in enumerate(activities):
        key_map = key_maps.get(activity.key_map)
        if key_map is None:
            raise ConfigError(f"Activity {activity.name!r} references a missing key map")
        bindings = []
        pending = []
        for binding in key_map.bindings:
            references = []
            for group, record in codes_sent(binding, action_lists):
                references.append({
                    "source_ir_group": group, "source_record_index": record,
                    "candidate_device_object_ids": group_ids.get(group, []),
                })
            bindings.append({
                "tag": binding.tag, "operand": binding.operand, "opcode": binding.opcode,
                "scan_code": binding.scan_code if binding.tag & 0xC0 else None,
                "direct_action_list_ir_references": references,
            })
            if binding.opcode == OP_CALL:
                pending.append(binding.operand)
        reachable: dict[int, list[dict[str, int]]] = {}
        while pending:
            number = pending.pop()
            if number in reachable:
                continue
            if number >= len(action_lists):
                raise ConfigError(f"Activity {activity.name!r} references missing action list {number}")
            instructions = action_lists[number]
            reachable[number] = [asdict(item) for item in instructions]
            pending.extend(item.operand for item in instructions if item.opcode == OP_CALL)
        objects.append({
            "format": FORMAT, "schema_version": VERSION, "object_type": "activity",
            "object_id": f"{prefix}-activity-{index:03d}", "name": activity.name,
            "source": source, "source_activity_number": activity.number,
            "source_key_map": activity.key_map, "source_mode": activity.mode,
            "devices": [
                {"name": name, "candidate_device_object_ids": label_ids.get(name, [])}
                for name in activity.devices
            ],
            "decoded_state_steps": [asdict(step) for step in activity.steps],
            "key_map_bindings": bindings,
            "source_action_lists": {str(k): v for k, v in sorted(reachable.items())},
            "transfer_ready": False,
            "limitations": [
                "Participating devices and activity steps are only partially decoded.",
                "Scan codes, input labels, device roles, and action semantics are not fully decoded.",
                "Source IDs and action operands are remote-specific; encoding and remapping are not implemented.",
            ],
        })
    return {"format": FORMAT, "schema_version": VERSION, "source": source,
            "transfer_ready": False, "objects": objects}


def _filename_fragment(name: str) -> str:
    """Return a short, filesystem-safe fragment of an object name."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:40]


def export_remote_objects(
    data: bytes, destination: Path, selected_ids: Collection[str] | None = None
) -> list[Path]:
    """Create a new export directory and write its object files and index.

    When ``selected_ids`` is provided, only those objects are exported; every
    requested identifier must exist in the decoded backup.
    """
    bundle = build_remote_objects(data)
    objects = bundle["objects"]
    if selected_ids is not None:
        requested = set(selected_ids)
        if not requested:
            raise ValueError("No objects were selected for export")
        known = {item["object_id"] for item in objects}
        missing = sorted(requested - known)
        if missing:
            raise ValueError(
                "Selected object id(s) not present in this backup: " + ", ".join(missing)
            )
        objects = [item for item in objects if item["object_id"] in requested]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    paths = []
    entries = []
    for item in objects:
        folder = "devices" if item["object_type"] == "device" else "activities"
        directory = destination / folder
        directory.mkdir(exist_ok=True)
        fragment = _filename_fragment(item["name"])
        stem = item["object_id"] + (f"__{fragment}" if fragment else "")
        relative = Path(folder) / (stem + ".json")
        path = destination / relative
        path.write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
        entries.append({"object_id": item["object_id"], "object_type": item["object_type"],
                        "name": item["name"], "path": relative.as_posix()})
    index = {key: bundle[key] for key in ("format", "schema_version", "source", "transfer_ready")}
    index["partial_export"] = selected_ids is not None and len(objects) < len(bundle["objects"])
    index["objects"] = entries
    index_path = destination / "index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths.append(index_path)
    return paths
