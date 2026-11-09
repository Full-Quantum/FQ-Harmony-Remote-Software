import unittest
from pathlib import Path

from harmony_one.activities import read_activities, read_devices
from harmony_one.config_reader import parse_config
from harmony_one.harmony_project import project_from_remote, validate_project
from harmony_one.ir_records import read_ir_groups
from harmony_one.key_bindings import read_action_lists, read_key_maps

BACKUPS = Path(__file__).resolve().parents[1] / "backups"
SPARE = BACKUPS / "HarmonyOne-SPARE-StyleA-20261101-180311.EZHex"
MAIN = BACKUPS / "HarmonyOne-StyleA-20261101-115223.EZHex"


@unittest.skipUnless(SPARE.exists() and MAIN.exists(), "owner backups not present")
class DecodeRealBackups(unittest.TestCase):
    def test_spare_activities_and_devices(self):
        data = SPARE.read_bytes()
        config = parse_config(data)
        self.assertEqual(config.activity_count, 2)
        self.assertEqual(
            [(d.group, d.label) for d in read_devices(data, config)],
            [(0, "PVR"), (1, "TV"), (2, "AV Receiver-Bedroom"), (3, "DVD")],
        )
        activities = read_activities(data, config)
        self.assertEqual([a.name for a in activities], ["Watch a DVD", "Watch TV"])

    def test_every_backup_has_one_key_map_per_activity(self):
        for path in BACKUPS.glob("HarmonyOne-*.EZHex"):
            data = path.read_bytes()
            config = parse_config(data)
            # prefix of 7, one map per activity, one left over
            self.assertEqual(len(read_key_maps(data, config)), 7 + config.activity_count + 1, path.name)
            self.assertEqual(len(read_activities(data, config)), config.activity_count, path.name)

    def test_ir_groups_match_devices(self):
        data = SPARE.read_bytes()
        config = parse_config(data)
        groups = read_ir_groups(data, config)
        self.assertEqual(len(groups), 4)
        lists = read_action_lists(data, config)
        self.assertEqual(len(lists), 2981)

    def test_project_import_is_valid(self):
        data = MAIN.read_bytes()
        config = parse_config(data)
        project = project_from_remote(config, data)
        validate_project(project)
        self.assertEqual(len(project["activities"]), 10)
        self.assertIn("Record Player", [a["name"] for a in project["activities"]])


if __name__ == "__main__":
    unittest.main()
