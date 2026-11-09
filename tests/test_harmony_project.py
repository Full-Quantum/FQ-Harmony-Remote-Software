import json
import tempfile
import unittest
from pathlib import Path

from harmony_one.harmony_project import (
    ProjectError,
    device_from_catalog,
    load_project,
    new_project,
    save_project,
    validate_project,
)


def sample_catalog():
    return {
        "device": {"name": "Example TV", "brand": "Example", "category": "TVs"},
        "signals": [
            {
                "name": "Power",
                "type": "parsed",
                "protocol": "Samsung32",
                "address": "07 00 00 00",
                "command": "02 00 00 00",
            }
        ],
    }


class HarmonyProjectTests(unittest.TestCase):
    def test_round_trips_project_with_device_and_activity(self):
        project = new_project()
        device = device_from_catalog(sample_catalog())
        project["devices"].append(device)
        project["activities"].append(
            {
                "id": "watch-tv",
                "name": "Watch TV",
                "steps": [{"device_id": device["id"], "command_name": "Power"}],
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            save_project(path, project)
            loaded = load_project(path)

        self.assertEqual(loaded, project)

    def test_rejects_activity_referencing_missing_device(self):
        project = new_project()
        project["activities"].append(
            {
                "id": "watch-tv",
                "name": "Watch TV",
                "steps": [{"device_id": "missing", "command_name": "Power"}],
            }
        )
        with self.assertRaisesRegex(ProjectError, "unknown device"):
            validate_project(project)

    def test_rejects_activity_referencing_missing_command(self):
        project = new_project()
        device = device_from_catalog(sample_catalog())
        project["devices"].append(device)
        project["activities"].append(
            {
                "id": "watch-tv",
                "name": "Watch TV",
                "steps": [{"device_id": device["id"], "command_name": "Unknown"}],
            }
        )
        with self.assertRaisesRegex(ProjectError, "unknown device command"):
            validate_project(project)

    def test_rejects_command_without_transfer_fields(self):
        project = new_project()
        device = device_from_catalog(sample_catalog())
        device["commands"][0]["command"] = ""
        project["devices"].append(device)
        with self.assertRaisesRegex(ProjectError, "non-empty command"):
            validate_project(project)

    def test_save_rejects_invalid_project_without_replacing_existing_file(self):
        project = new_project()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text('{"keep":"old"}\n', encoding="utf-8")
            invalid = dict(project, formatVersion=999)
            with self.assertRaises(ProjectError):
                save_project(path, invalid)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"keep": "old"})


if __name__ == "__main__":
    unittest.main()
