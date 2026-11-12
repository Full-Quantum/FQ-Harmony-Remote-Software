import unittest

from harmony_one.config_blocks import build_config_blocks
from harmony_one.config_reader import ConfigError
from test_config_image import fixture


class ConfigBlockTests(unittest.TestCase):
    def test_map_covers_complete_backup_in_order(self):
        blob = fixture()
        blocks = build_config_blocks(blob)
        self.assertEqual(blocks[0].kind, "header")
        self.assertEqual(blocks[-1].kind, "trailer")
        self.assertEqual([block.slot for block in blocks if block.kind == "section"], [0, 1])
        self.assertEqual(blocks[0].start_offset, 0)
        self.assertEqual(blocks[-1].end_offset, len(blob))
        self.assertEqual(sum(block.size for block in blocks), len(blob))
        self.assertTrue(
            all(left.end_offset == right.start_offset for left, right in zip(blocks, blocks[1:]))
        )

    def test_rejects_non_configuration_data(self):
        with self.assertRaises(ConfigError):
            build_config_blocks(b"not a configuration")


if __name__ == "__main__":
    unittest.main()
