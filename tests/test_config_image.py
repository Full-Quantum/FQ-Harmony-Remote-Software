import unittest
from pathlib import Path

from harmony_one.config_image import BytePatch, patch_image, validate_image
from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.harmony_usb import trailer_checksum
from test_config_reader import build_container


def fixture():
    data = bytearray(build_container([(1, 0, "TV_Power_2")]))
    data[-6:-4] = trailer_checksum(data).to_bytes(2, "little")
    return bytes(data)


class ConfigImageTests(unittest.TestCase):
    def setUp(self):
        self.blob = fixture()
        self.offset = parse_config(self.blob).sections[1][0] + 4

    def test_empty_patches_are_byte_identical(self):
        self.assertEqual(patch_image(self.blob, []), self.blob)

    def test_only_requested_bytes_and_checksum_change(self):
        patch = BytePatch(self.offset, self.blob[self.offset:self.offset + 1], b"\x7f", "test")
        result = patch_image(self.blob, [patch])
        validate_image(result)
        self.assertEqual(result[self.offset], 0x7f)
        self.assertEqual(
            [i for i, (a, b) in enumerate(zip(self.blob, result))
             if a != b and i not in (len(result) - 6, len(result) - 5)],
            [self.offset],
        )

    def test_rejects_bad_patches(self):
        invalid = [
            [BytePatch(self.offset, b"\xff", b"\x01", "stale")],
            [BytePatch(self.offset, b"", b"", "empty")],
            [BytePatch(self.offset, b"\x00", b"\x01\x02", "growth")],
            [BytePatch(0, b"G", b"Q", "header")],
            [BytePatch(len(self.blob) - 6, self.blob[-6:-5], b"\x01", "checksum")],
            [BytePatch(self.offset, b"\x00", b"\x01", "one"),
             BytePatch(self.offset, b"\x00", b"\x02", "two")],
        ]
        for patches in invalid:
            with self.subTest(patches=patches), self.assertRaises(ConfigError):
                patch_image(self.blob, patches)

    def test_rejects_corrupted_checksum(self):
        bad = bytearray(self.blob)
        bad[-6] ^= 1
        with self.assertRaisesRegex(ConfigError, "checksum"):
            patch_image(bytes(bad), [])

    def test_rejects_other_architecture(self):
        bad = bytearray(self.blob)
        bad[parse_config(bad).sections[1][0]] = 14
        bad[-6:-4] = trailer_checksum(bad).to_bytes(2, "little")
        with self.assertRaisesRegex(ConfigError, "architecture 12"):
            validate_image(bytes(bad))


BACKUP = Path(__file__).resolve().parents[1] / "backups" / "HarmonyOne-SPARE-20261106-before-development.EZHex"


@unittest.skipUnless(BACKUP.exists(), "spare backup not present")
class SpareImageTests(unittest.TestCase):
    def test_backup_lossless_without_edits(self):
        data = BACKUP.read_bytes()
        self.assertEqual(patch_image(data, []), data)


if __name__ == "__main__":
    unittest.main()
