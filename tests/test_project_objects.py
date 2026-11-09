import copy
import unittest

from harmony_one.harmony_project import ProjectError, new_project, validate_project
from harmony_one.project_objects import export_project_object, import_project_object


def project_with_objects():
    project = new_project()
    project["devices"] = [
        {
            "id": "tv",
            "name": "TV",
            "brand": "Example",
            "category": "Display",
            "commands": [
                {"name": "Power", "protocol": "NEC", "address": "01", "command": "02"}
            ],
        },
        {"id": "unused", "name": "Unused", "brand": "", "category": "", "commands": []},
    ]
    project["activities"] = [
        {
            "id": "watch-tv",
            "name": "Watch TV",
            "steps": [
                {"type": "command", "device_id": "tv", "command_name": "Power"}
            ],
            "participating_device_ids": ["tv"],
            "channel_device_id": "tv",
            "volume_device_id": None,
            "inputs": {"tv": "HDMI 1"},
        }
    ]
    return validate_project(project)


class ProjectObjectTests(unittest.TestCase):
    def test_device_package_imports_with_a_fresh_id(self):
        source = project_with_objects()
        original = copy.deepcopy(source)
        package = export_project_object(source, "device", "tv")
        target, imported = import_project_object(new_project(), package)

        self.assertEqual(source, original)
        self.assertNotEqual(imported["id"], "tv")
        self.assertEqual(target["devices"], [imported])
        self.assertFalse(package["transfer_ready"])

    def test_activity_package_carries_only_dependencies_and_remaps_references(self):
        source = project_with_objects()
        package = export_project_object(source, "activity", "watch-tv")
        self.assertEqual([device["id"] for device in package["dependency_devices"]], ["tv"])

        target = new_project()
        target["devices"].append(
            {"id": "tv", "name": "Other TV", "brand": "", "category": "", "commands": []}
        )
        imported_project, imported = import_project_object(target, package)
        validate_project(imported_project)

        self.assertNotEqual(imported["id"], "watch-tv")
        self.assertEqual(len(imported_project["devices"]), 2)
        imported_tv = next(device for device in imported_project["devices"] if device["name"] == "TV")
        self.assertNotEqual(imported_tv["id"], "tv")
        self.assertEqual(imported["steps"][0]["device_id"], imported_tv["id"])
        self.assertEqual(imported["participating_device_ids"], [imported_tv["id"]])
        self.assertEqual(imported["inputs"], {imported_tv["id"]: "HDMI 1"})

    def test_activity_package_rejects_missing_dependency(self):
        package = export_project_object(project_with_objects(), "activity", "watch-tv")
        package["dependency_devices"].clear()
        with self.assertRaisesRegex(ProjectError, "invalid|dependencies"):
            import_project_object(new_project(), package)

    def test_import_never_trusts_transfer_ready_flag(self):
        package = export_project_object(project_with_objects(), "device", "tv")
        package["transfer_ready"] = True
        with self.assertRaisesRegex(ProjectError, "never accepted as upload-ready"):
            import_project_object(new_project(), package)


if __name__ == "__main__":
    unittest.main()