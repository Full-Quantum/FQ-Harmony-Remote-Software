import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.activities import Activity, Device, Step
from harmony_one.config_reader import ConfigError
from harmony_one.ir_records import IrGroup, IrRecord
from harmony_one.key_bindings import Binding, Instruction, KeyMap
from harmony_one.object_library import build_remote_objects, export_remote_objects


class ObjectLibraryTests(unittest.TestCase):
    def setUp(self):
        config = SimpleNamespace(architecture=12, base_address=0x40000, skin=54)
        patches = [
            patch("harmony_one.object_library.parse_config", return_value=config),
            patch("harmony_one.object_library.trailer_checksum", return_value=0),
            patch("harmony_one.object_library.read_devices", return_value=[
                Device("TV", 1, {"Power": 4}),
            ]),
            patch("harmony_one.object_library.read_ir_groups", return_value=[
                IrGroup(1, (IrRecord(0, 38000, 2, ((100, -200), None, None), 2),)),
            ]),
            patch("harmony_one.object_library.read_activities", return_value=[
                Activity(0, "Watch TV", 7, 9, [Step("power", "TV", 1)], ["TV"]),
            ]),
            patch("harmony_one.object_library.read_key_maps", return_value=[
                KeyMap(7, (Binding(0x81, 0, 0x7F),)),
            ]),
            patch("harmony_one.object_library.read_action_lists", return_value=[
                (Instruction(0x7D, 0x100), Instruction(0x7F, 1)),
                (Instruction(0x7C, 10),),
            ]),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.data = b"example\x00\x00PTYY"

    def test_round_trip_and_references(self):
        bundle = build_remote_objects(self.data)
        device, activity = bundle["objects"]
        self.assertFalse(device["transfer_ready"])
        self.assertEqual(device["ir_records"][0]["first_pointer_group_blocks_us"]["once"], [100, -200])
        self.assertEqual(activity["devices"][0]["candidate_device_object_ids"], [device["object_id"]])
        self.assertEqual(activity["key_map_bindings"][0]["direct_action_list_ir_references"][0]
                         ["candidate_device_object_ids"], [device["object_id"]])
        self.assertEqual(activity["source_action_lists"]["1"][0]["operand"], 10)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "export"
            paths = export_remote_objects(self.data, destination)
            index = json.loads((destination / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(paths), 3)
            self.assertFalse(index["partial_export"])
            for entry in index["objects"]:
                item = json.loads((destination / entry["path"]).read_text(encoding="utf-8"))
                self.assertEqual(item["object_id"], entry["object_id"])
                self.assertEqual(item["source"], index["source"])
            device_entry = index["objects"][0]
            self.assertTrue(device_entry["path"].endswith("__TV.json"))
            with self.assertRaises(FileExistsError):
                export_remote_objects(self.data, destination)

    def test_selected_export_writes_only_requested_objects(self):
        bundle = build_remote_objects(self.data)
        activity_id = bundle["objects"][1]["object_id"]
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "export"
            paths = export_remote_objects(self.data, destination, selected_ids=[activity_id])
            self.assertEqual(len(paths), 2)
            index = json.loads((destination / "index.json").read_text(encoding="utf-8"))
            self.assertTrue(index["partial_export"])
            self.assertEqual([entry["object_id"] for entry in index["objects"]], [activity_id])
            self.assertEqual(index["objects"][0]["name"], "Watch TV")
            self.assertFalse((destination / "devices").exists())
            self.assertTrue(index["objects"][0]["path"].endswith("__Watch-TV.json"))

    def test_selected_export_rejects_unknown_and_empty_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "not present"):
                export_remote_objects(self.data, Path(temp) / "a", selected_ids=["bogus"])
            self.assertFalse((Path(temp) / "a").exists())
            with self.assertRaisesRegex(ValueError, "No objects"):
                export_remote_objects(self.data, Path(temp) / "b", selected_ids=[])
            self.assertFalse((Path(temp) / "b").exists())

    def test_rejects_bad_checksum(self):
        with patch("harmony_one.object_library.trailer_checksum", return_value=1):
            with self.assertRaisesRegex(ConfigError, "checksum"):
                build_remote_objects(self.data)

    def test_rejects_missing_action_list(self):
        with patch("harmony_one.object_library.read_action_lists", return_value=[]):
            with self.assertRaisesRegex(ConfigError, "missing action list"):
                build_remote_objects(self.data)


if __name__ == "__main__":
    unittest.main()
