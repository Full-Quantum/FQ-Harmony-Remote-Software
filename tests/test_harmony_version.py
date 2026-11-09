import unittest

from harmony_one.harmony_usb import BackupError
from harmony_one.harmony_version import parse_version_reply


class VersionTests(unittest.TestCase):
    def test_spare_remote_reply(self):
        raw = bytes.fromhex("32 05 c8 1f c0 36 0c 32 32 16 32 32")
        version = parse_version_reply(bytes([0x28]) + raw + bytes(51))
        self.assertEqual(version.firmware, "3.2")
        self.assertEqual(version.hardware, "0.5")
        self.assertEqual(version.flash_chip_id, "1F:C8")
        self.assertEqual((version.architecture, version.software_type, version.skin), (12, 0, 54))

    def test_wrong_reply_is_rejected(self):
        with self.assertRaises(BackupError):
            parse_version_reply(bytes([0x27]) + bytes(63))


if __name__ == "__main__":
    unittest.main()
