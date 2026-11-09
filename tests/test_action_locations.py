import unittest
from pathlib import Path
from types import SimpleNamespace

from harmony_one.config_reader import ConfigError, parse_config, raw_slot_for_base_slot
from harmony_one.key_bindings import Instruction, locate_action_lists, read_action_lists


class ActionLocationTests(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(80)
        self.config = SimpleNamespace(
            base_address=0x40000, sections={raw_slot_for_base_slot(10): (10, 25)}
        )
        self.data[10:12] = (2).to_bytes(2, "little")
        self.data[12:15] = (0x40000 + 30).to_bytes(3, "little")
        self.data[15:18] = (0x40000 + 30).to_bytes(3, "little")
        self.data[30:34] = bytes([1, 0x34, 0x12, 0x7d])

    def test_records_shared_source_offset(self):
        entries = locate_action_lists(bytes(self.data), self.config)
        self.assertEqual([entry.offset for entry in entries], [30, 30])
        self.assertEqual(entries[0].instructions, (Instruction(0x7d, 0x1234),))

    def test_rejects_bad_pointer_and_truncated_body(self):
        self.data[12:15] = (0x40000 + 73).to_bytes(3, "little")
        self.data[73] = 1
        with self.assertRaisesRegex(ConfigError, "truncated"):
            locate_action_lists(bytes(self.data), self.config)
        self.data[12:15] = (0x40000 - 1).to_bytes(3, "little")
        with self.assertRaisesRegex(ConfigError, "outside"):
            locate_action_lists(bytes(self.data), self.config)

    def test_rejects_truncated_pointer_table(self):
        self.data[10:12] = (10).to_bytes(2, "little")
        with self.assertRaisesRegex(ConfigError, "pointer table"):
            locate_action_lists(bytes(self.data), self.config)


BACKUP = Path(__file__).resolve().parents[1] / "backups" / "HarmonyOne-SPARE-20261106-before-development.EZHex"


@unittest.skipUnless(BACKUP.exists(), "spare backup not present")
class SpareActionLocationTests(unittest.TestCase):
    def test_locations_match_existing_action_decoder(self):
        data = BACKUP.read_bytes()
        config = parse_config(data)
        located = locate_action_lists(data, config)
        decoded = read_action_lists(data, config)
        self.assertEqual([item.instructions for item in located], decoded)
        self.assertGreater(len(located), 0)


if __name__ == "__main__":
    unittest.main()
