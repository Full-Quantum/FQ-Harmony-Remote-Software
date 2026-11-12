import copy
import unittest
from pathlib import Path

from harmony_one.harmony_project import new_project
from harmony_one.remote_writer import (
    WriteBlocked,
    check_gates,
    plan_changes,
    verify_container_roundtrip,
    write_project_to_remote,
)

BACKUPS = Path(__file__).resolve().parents[1] / "backups"


def project_with(devices, activities):
    project = new_project()
    project["devices"] = [{"id": i, "name": n} for i, n in devices]
    project["activities"] = [{"id": i, "name": n, "steps": s} for i, n, s in activities]
    return project


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.remote = project_with(
            [("d1", "TV"), ("d2", "AV")], [("a1", "Watch TV", []), ("a2", "Play", [])]
        )

    def test_identical_projects_have_empty_plan(self):
        self.assertTrue(plan_changes(self.remote, copy.deepcopy(self.remote)).is_empty)

    def test_add_remove_rename_reorder(self):
        edited = copy.deepcopy(self.remote)
        edited["devices"][0]["name"] = "Samsung TV"
        edited["devices"].append({"id": "d3", "name": "BluRay"})
        edited["activities"].reverse()
        edited["activities"][0]["steps"] = [{"type": "delay", "ms": 500}]
        plan = plan_changes(self.remote, edited)
        self.assertEqual(plan.devices_added, ["BluRay"])
        self.assertEqual(plan.devices_renamed, [("TV", "Samsung TV")])
        self.assertTrue(plan.activities_reordered)
        self.assertEqual(plan.activities_with_steps_changed, ["Play"])
        edited["devices"].pop(1)
        self.assertEqual(plan_changes(self.remote, edited).devices_removed, ["AV"])

    def test_write_is_blocked_until_encoder_verified(self):
        edited = copy.deepcopy(self.remote)
        edited["devices"].pop()
        plan = plan_changes(self.remote, edited)
        with self.assertRaises(WriteBlocked) as raised:
            write_project_to_remote(None, None, plan)
        self.assertIn("backup", str(raised.exception))
        self.assertIn("Transfer Remote tab", str(raised.exception))

    def test_plan_covers_device_and_activity_fields(self):
        edited = copy.deepcopy(self.remote)
        edited["devices"][0]["commands"] = [{"name": "Power"}]
        edited["devices"][1]["brand"] = "Example"
        edited["activities"][0].update(
            participating_device_ids=["d1"], volume_device_id="d1",
            inputs={"d1": "HDMI 1"},
        )
        edited["extra"] = "new field"
        plan = plan_changes(self.remote, edited)
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.device_fields_changed, [
            ("TV", ["commands"]), ("AV", ["brand"])
        ])
        self.assertEqual(plan.activity_fields_changed, [
            ("Watch TV", ["inputs", "participating_device_ids", "volume_device_id"])
        ])
        self.assertEqual(plan.project_fields_changed, ["extra"])
        self.assertIn("commands", "\n".join(plan.describe()))

    def test_removed_null_field_is_still_a_change(self):
        before = copy.deepcopy(self.remote)
        before["activities"][0]["volume_device_id"] = None
        plan = plan_changes(before, self.remote)
        self.assertEqual(plan.activity_fields_changed, [("Watch TV", ["volume_device_id"])])

    def test_gate_rejects_empty_plan(self):
        self.assertIn("no changes", " ".join(check_gates(None, None, plan_changes(self.remote, self.remote))).lower())

    def test_garbage_does_not_roundtrip(self):
        self.assertFalse(verify_container_roundtrip(b"not a config"))


@unittest.skipUnless((BACKUPS / "HarmonyOne-current.EZHex").exists(), "owner backup not present")
class OwnerBackupTests(unittest.TestCase):
    def test_real_backups_roundtrip(self):
        for path in BACKUPS.glob("HarmonyOne-*.EZHex"):
            with self.subTest(path.name):
                self.assertTrue(verify_container_roundtrip(path.read_bytes()))


if __name__ == "__main__":
    unittest.main()
