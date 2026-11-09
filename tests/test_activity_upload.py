import unittest
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.activity_upload import find_node_runtime, prepare_activity_addition, validate_activity_addition
from harmony_one.config_reader import ConfigError


class ActivityAdditionValidationTests(unittest.TestCase):
    def setUp(self):
        self.before = b"a" * 100
        self.after = b"b" * 110
        self.config = SimpleNamespace(architecture=12, skin=54, base_address=0x40000)
        values = {
            "validate_image": None,
            "parse_config": self.config,
            "read_devices": [],
            "read_ir_groups": [],
            "read_action_lists": ["old"],
            "read_state_records": {},
        }
        self.mocks = {}
        for name, value in values.items():
            patcher = patch(f"harmony_one.activity_upload.{name}", return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        for name, values in (
            ("read_key_maps", [["old"], ["old", "new"]]),
            ("read_activities", [["old"], ["old", "new"]]),
        ):
            patcher = patch(f"harmony_one.activity_upload.{name}", side_effect=values)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def test_accepts_preserved_configuration_with_one_addition(self):
        validate_activity_addition(self.before, self.after)

    def test_rejects_changed_identity(self):
        self.mocks["parse_config"].side_effect = [
            self.config, SimpleNamespace(architecture=12, skin=55, base_address=0x40000)
        ]
        with self.assertRaisesRegex(ConfigError, "identity"):
            validate_activity_addition(self.before, self.after)

    def test_rejects_growth_into_another_erase_block(self):
        with self.assertRaisesRegex(ConfigError, "erase block"):
            validate_activity_addition(self.before, b"x" * 65537)

    def test_rejects_changed_infrared(self):
        self.mocks["read_ir_groups"].side_effect = [["old"], ["changed"]]
        with self.assertRaisesRegex(ConfigError, "infrared"):
            validate_activity_addition(self.before, self.after)

    def test_rejects_changed_existing_actions(self):
        self.mocks["read_action_lists"].side_effect = [["old"], ["changed", "new"]]
        with self.assertRaisesRegex(ConfigError, "action lists"):
            validate_activity_addition(self.before, self.after)

    def test_rejects_changed_existing_keys(self):
        self.mocks["read_key_maps"].side_effect = [["old"], ["changed", "new"]]
        with self.assertRaisesRegex(ConfigError, "key maps"):
            validate_activity_addition(self.before, self.after)

    def test_rejects_changed_existing_activities(self):
        self.mocks["read_activities"].side_effect = [["old"], ["changed", "new"]]
        with self.assertRaisesRegex(ConfigError, "activities"):
            validate_activity_addition(self.before, self.after)

    def test_rejects_changed_device_state_transitions(self):
        device = SimpleNamespace(label="TV", variables={"Power": 32})
        self.mocks["read_devices"].return_value = [device]
        self.mocks["read_state_records"].side_effect = [{32: "old"}, {32: "changed"}]
        with self.assertRaisesRegex(ConfigError, "state transitions"):
            validate_activity_addition(self.before, self.after)


class NodeRuntimeTests(unittest.TestCase):
    def test_prefers_project_runtime_when_present(self):
        with patch("harmony_one.activity_upload.Path.is_file", return_value=True), \
             patch("harmony_one.activity_upload.shutil.which") as system:
            result = find_node_runtime()
        self.assertEqual(Path(result).name, "node.exe")
        self.assertEqual(Path(result).parent.name, "node")
        system.assert_not_called()

    def test_uses_path_when_project_runtime_absent(self):
        with patch("harmony_one.activity_upload.Path.is_file", return_value=False), \
             patch("harmony_one.activity_upload.shutil.which", return_value="system-node"):
            self.assertEqual(find_node_runtime(), "system-node")


BACKUP = (
    Path(__file__).resolve().parents[1]
    / "backups"
    / "HarmonyOne-FQS1-before-test-20261107-111016.EZHex"
)


@unittest.skipUnless(BACKUP.exists() and find_node_runtime(), "FQS1 backup or Node.js unavailable")
class ActivityAdditionScreenIntegrationTests(unittest.TestCase):
    def test_candidate_preserves_configuration_and_reuses_template_screen(self):
        baseline = BACKUP.read_bytes()
        with tempfile.TemporaryDirectory(prefix="harmony-screen-test-") as directory:
            candidate = prepare_activity_addition(
                baseline, "Test TV", "FQS1 (PC)", Path(directory)
            )
            self.assertIn("Template working screen", candidate.report)
            self.assertIn("return verified", candidate.report)
            self.assertEqual(candidate.path.read_bytes(), candidate.image)
