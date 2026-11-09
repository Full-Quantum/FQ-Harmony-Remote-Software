import unittest
from pathlib import Path

from harmony_one.activities import read_activities
from harmony_one.config_reader import parse_config
from harmony_one.rename import RenameError, rename_text

SPARE = Path(__file__).resolve().parents[1] / "backups" / "HarmonyOne-SPARE-after-ralsei.EZHex"


@unittest.skipUnless(SPARE.exists(), "needs the spare backup")
class RenameTests(unittest.TestCase):
    def test_same_length_rename(self):
        data = SPARE.read_bytes()
        new, places = rename_text(data, "Watch TV", "Watch VT")
        self.assertEqual(len(new), len(data))
        self.assertGreaterEqual(places, 1)
        names = [a.name for a in read_activities(new, parse_config(new))]
        self.assertIn("Watch VT", names)

    def test_length_change_rejected(self):
        with self.assertRaises(RenameError):
            rename_text(SPARE.read_bytes(), "Watch TV", "Watch")
