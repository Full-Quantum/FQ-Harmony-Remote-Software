import json
import unittest

from harmony_one.app import bundled_project, resource_path
from harmony_one.feature_tracker import FUNCTIONALITY_LEVELS, load_feature_catalog
from harmony_one.harmony_project import validate_project


class BundledCatalogTests(unittest.TestCase):
    def test_feature_tracker_catalog_has_valid_levels_and_core_limitations(self):
        features = load_feature_catalog(resource_path("data/features.json"))

        self.assertEqual(
            {feature["level"] for feature in features},
            set(FUNCTIONALITY_LEVELS),
        )
        by_id = {feature["id"]: feature for feature in features}
        self.assertEqual(by_id["remote-object-export"]["level"], "Partially functional")
        self.assertEqual(by_id["harmony-one-backup"]["level"], "Read-only")
        self.assertEqual(by_id["clock-sync"]["level"], "Partially functional")
        self.assertEqual(by_id["remote-programming"]["level"], "Partially functional")
        self.assertEqual(by_id["remote-config-generation"]["level"], "Not implemented")

    def test_starter_project_loads_all_curated_profiles(self):
        project = bundled_project()
        validate_project(project)

        self.assertEqual(len(project["devices"]), 82)
        self.assertEqual(
            sum(len(device["commands"]) for device in project["devices"]),
            1956,
        )
        categories = {device["category"] for device in project["devices"]}
        self.assertIn("TVs", categories)
        self.assertIn("Audio and Video Receivers", categories)
        self.assertIn("Streaming Devices", categories)

    def test_catalog_manifest_records_post_cutoff_source_revision(self):
        manifest = json.loads(
            resource_path("data/catalogs.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["license"], "CC0-1.0")
        self.assertEqual(
            manifest["licenseCutoffCommit"],
            "2319685f2cbf0cd3f809609622cade14d24fb819",
        )
        self.assertEqual(
            manifest["revision"],
            "d126fb1b6f1e114c52b4a8c19839ea65e3a9c24d",
        )
        self.assertEqual(len(manifest["catalogs"]), 82)
        self.assertTrue(
            all(
                catalog["sourceUrl"].startswith(manifest["repository"])
                for catalog in manifest["catalogs"]
            )
        )


if __name__ == "__main__":
    unittest.main()
