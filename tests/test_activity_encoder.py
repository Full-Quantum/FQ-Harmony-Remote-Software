import unittest
from pathlib import Path
from unittest.mock import patch

from harmony_one.activities import read_activities
from harmony_one.activity_encoder import prepare_activity_rename
from harmony_one.config_image import validate_image
from harmony_one.config_reader import parse_config
from harmony_one.rename import RenameError


BACKUP = Path(__file__).resolve().parents[1] / "backups" / "HarmonyOne-SPARE-20261106-before-development.EZHex"


@unittest.skipUnless(BACKUP.exists(), "spare backup not present")
class ActivityEncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = BACKUP.read_bytes()

    def test_prepare_rename_preserves_length_and_other_activity(self):
        candidate = prepare_activity_rename(self.blob, "Watch VT", "Watch TV")
        self.assertEqual(len(candidate.image), len(self.blob))
        self.assertGreater(candidate.places_changed, 0)
        validate_image(candidate.image)
        names = [a.name for a in read_activities(candidate.image, parse_config(candidate.image))]
        self.assertEqual(names, ["Watch a DVD", "Watch TV"])
        self.assertEqual(
            candidate.changed_bytes,
            sum(a != b for a, b in zip(self.blob, candidate.image)),
        )

    def test_rejects_bad_names(self):
        for old, new in (("Watch VT", "Watch"), ("Watch VT", "Watch VT"),
                         ("Missing!", "Whatever")):
            with self.subTest(old=old, new=new), self.assertRaises(RenameError):
                prepare_activity_rename(self.blob, old, new)

    def test_rejects_ambiguous_names(self):
        activities = read_activities(self.blob, parse_config(self.blob))
        activities[0].name = "Watch VT"
        with patch("harmony_one.activity_encoder.read_activities", return_value=activities):
            with self.assertRaisesRegex(RenameError, "exactly one"):
                prepare_activity_rename(self.blob, "Watch VT", "Watch TV")


if __name__ == "__main__":
    unittest.main()
