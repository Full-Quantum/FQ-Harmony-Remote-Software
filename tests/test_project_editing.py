import unittest
import tempfile
from types import SimpleNamespace
from pathlib import Path

from harmony_one.harmony_project import (
    ProjectError,
    load_project,
    move_item,
    new_project,
    project_from_remote,
    save_project,
    validate_project,
)


def project_with_activity(steps):
    project = new_project()
    project["devices"].append(
        {
            "id": "tv", "name": "TV", "brand": "", "category": "",
            "commands": [{"name": "Power", "protocol": "NEC", "address": "07", "command": "02"}],
        }
    )
    project["activities"].append({"id": "a1", "name": "Watch TV", "steps": steps})
    return project


class MoveItemTests(unittest.TestCase):
    def test_moves_and_returns_new_index(self):
        items = ["a", "b", "c"]
        self.assertEqual(move_item(items, 0, 1), 1)
        self.assertEqual(items, ["b", "a", "c"])

    def test_out_of_range_is_a_no_op(self):
        items = ["a", "b"]
        self.assertEqual(move_item(items, 0, -1), 0)
        self.assertEqual(move_item(items, 1, 1), 1)
        self.assertEqual(move_item(items, 5, -1), 5)
        self.assertEqual(items, ["a", "b"])


class DelayStepTests(unittest.TestCase):
    def test_delay_and_command_steps_validate(self):
        steps = [
            {"device_id": "tv", "command_name": "Power"},
            {"type": "delay", "ms": 500},
            {"type": "command", "device_id": "tv", "command_name": "Power"},
        ]
        validate_project(project_with_activity(steps))

    def test_bad_delays_are_rejected(self):
        for ms in (0, 99, 10001, "500", True, None):
            with self.subTest(ms=ms), self.assertRaises(ProjectError):
                validate_project(project_with_activity([{"type": "delay", "ms": ms}]))

    def test_unknown_step_type_is_rejected(self):
        with self.assertRaises(ProjectError):
            validate_project(project_with_activity([{"type": "teleport"}]))


class ActivitySetupTests(unittest.TestCase):
    def test_participating_devices_roles_inputs_and_named_sequences_validate(self):
        project = project_with_activity(
            [
                {
                    "type": "command",
                    "sequence_name": "Power on",
                    "device_id": "tv",
                    "command_name": "Power",
                }
            ]
        )
        project["activities"][0].update(
            participating_device_ids=["tv"],
            channel_device_id="tv",
            volume_device_id="tv",
            inputs={"tv": "InputHDMI1"},
        )
        validate_project(project)

    def test_activity_setup_roundtrips_in_project_file(self):
        project = project_with_activity(
            [
                {
                    "type": "command",
                    "sequence_name": "Start player",
                    "device_id": "tv",
                    "command_name": "Power",
                }
            ]
        )
        activity = project["activities"][0]
        activity.update(
            participating_device_ids=["tv"],
            channel_device_id="tv",
            volume_device_id="tv",
            inputs={"tv": "InputHDMI1"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            save_project(path, project)
            loaded = load_project(path)
        self.assertEqual(loaded["activities"][0]["participating_device_ids"], ["tv"])
        self.assertEqual(loaded["activities"][0]["channel_device_id"], "tv")
        self.assertEqual(loaded["activities"][0]["volume_device_id"], "tv")
        self.assertEqual(loaded["activities"][0]["inputs"], {"tv": "InputHDMI1"})
        self.assertEqual(
            loaded["activities"][0]["steps"][0]["sequence_name"], "Start player"
        )

    def test_activity_roles_must_use_participating_devices(self):
        project = project_with_activity([])
        project["activities"][0]["participating_device_ids"] = []
        project["activities"][0]["volume_device_id"] = "tv"
        with self.assertRaises(ProjectError):
            validate_project(project)

    def test_activity_inputs_must_use_participating_devices(self):
        project = project_with_activity([])
        project["activities"][0].update(participating_device_ids=[], inputs={"tv": "HDMI 1"})
        with self.assertRaises(ProjectError):
            validate_project(project)

    def test_legacy_activity_without_setup_fields_remains_valid(self):
        project = project_with_activity([{"device_id": "tv", "command_name": "Power"}])
        validate_project(project)

    def test_empty_named_sequence_is_rejected(self):
        project = project_with_activity(
            [
                {
                    "type": "command",
                    "sequence_name": "  ",
                    "device_id": "tv",
                    "command_name": "Power",
                }
            ]
        )
        with self.assertRaises(ProjectError):
            validate_project(project)


class RemoteImportTests(unittest.TestCase):
    def test_imports_devices_and_placeholder_activities(self):
        config = SimpleNamespace(device_labels=["TV", "BR/DVD"], activity_count=3)
        project = validate_project(project_from_remote(config))
        self.assertEqual([d["name"] for d in project["devices"]], ["TV", "BR/DVD"])
        self.assertEqual(len(project["activities"]), 3)
        self.assertTrue(all(a["source"] == "remote" for a in project["activities"]))
        self.assertEqual(len({d["id"] for d in project["devices"]}), 2)


if __name__ == "__main__":
    unittest.main()
