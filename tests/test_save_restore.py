import unittest
from unittest.mock import patch

from harmony_one.config_reader import parse_config
from harmony_one.harmony_usb import CONFIG_FLASH_BASE, trailer_checksum
from harmony_one.save_restore import SaveRestoreError, validate_restore_pair
from test_config_image import fixture


def change_skin(blob: bytes, skin: int) -> bytes:
    result = bytearray(blob)
    architecture_start = parse_config(blob).sections[1][0]
    result[architecture_start + 2] = skin
    result[-6:-4] = trailer_checksum(result).to_bytes(2, "little")
    return bytes(result)


def change_name(blob: bytes) -> bytes:
    result = bytearray(blob)
    result[result.index(b"TV_Power_2")] = ord("V")
    result[-6:-4] = trailer_checksum(result).to_bytes(2, "little")
    return bytes(result)


class SaveRestoreCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.source = fixture()

    def test_accepts_valid_distinct_saves_with_matching_skin(self):
        source_config, target_config = validate_restore_pair(
            self.source, change_name(self.source)
        )
        self.assertEqual(source_config.architecture, 12)
        self.assertEqual(target_config.architecture, 12)
        self.assertEqual(source_config.skin, target_config.skin)

    def test_rejects_different_screen_skin(self):
        with self.assertRaisesRegex(SaveRestoreError, "different Harmony One screen skins"):
            validate_restore_pair(self.source, change_skin(self.source, 0x3C))

    def test_rejects_identical_saves(self):
        with self.assertRaisesRegex(SaveRestoreError, "identical"):
            validate_restore_pair(self.source, self.source)

    def test_rejects_invalid_source_checksum(self):
        invalid = bytearray(self.source)
        invalid[-6] ^= 1
        with self.assertRaisesRegex(SaveRestoreError, "checksum"):
            validate_restore_pair(bytes(invalid), change_name(self.source))

    def test_rejects_save_whose_erase_padding_exceeds_capacity(self):
        with patch("harmony_one.save_restore.CONFIG_FLASH_LIMIT", CONFIG_FLASH_BASE + 1):
            with self.assertRaisesRegex(SaveRestoreError, "erase-block padding"):
                validate_restore_pair(self.source, change_name(self.source))


if __name__ == "__main__":
    unittest.main()
