import hashlib
import unittest

from harmony_one.app import resource_path
from harmony_one.save_restore import validate_restore_pair, validate_source_save
from harmony_one.save_restore_tab import FQ_PRESET_FILES


class BundledPresetTests(unittest.TestCase):
    def test_bundled_fq_presets_are_the_validated_owner_saves(self):
        expected = {
            "FQ Preset older": (
                1_802_273,
                "39fdf8d56ac8171af7ba18d2164a634a7df93c81f9dad6029586021d3b806a31",
            ),
            "FQ Preset newer": (
                2_138_771,
                "14d6bd9fd9c00822a4208d79673a51b1a65ada24e7b23deaf0bb66771cfab99d",
            ),
        }
        saves = {}
        for label, filename in FQ_PRESET_FILES.items():
            path = resource_path(f"data/presets/{filename}")
            blob = path.read_bytes()
            config = validate_source_save(blob, label)
            self.assertEqual(len(blob), expected[label][0])
            self.assertEqual(hashlib.sha256(blob).hexdigest(), expected[label][1])
            self.assertEqual(config.architecture, 12)
            self.assertEqual(config.skin, 54)
            saves[label] = blob

        validate_restore_pair(
            saves["FQ Preset older"],
            saves["FQ Preset newer"],
        )


if __name__ == "__main__":
    unittest.main()
