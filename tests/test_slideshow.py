import unittest
from pathlib import Path

from harmony_one.config_reader import parse_config
from harmony_one.harmony_usb import trailer_checksum
from harmony_one.pictures import Picture, find_picture_bank
from harmony_one.slideshow import PictureReplaceError, replace_picture

BACKUPS = Path(__file__).resolve().parents[1] / "backups"
SPARE = BACKUPS / "HarmonyOne-SPARE-StyleA-20261101-180311.EZHex"
MAIN = BACKUPS / "HarmonyOne-current.EZHex"


def stored_checksum(blob):
    return int.from_bytes(blob[-6:-4], "little")


class PictureReplaceTests(unittest.TestCase):
    def test_encode_decode_roundtrip_and_fit(self):
        from harmony_one.pictures import decode_picture, picture_at
        from harmony_one.slideshow import SCREEN_HEIGHT, SCREEN_WIDTH, encode_raw_picture, fit_cover

        pixels = fit_cover(4, 4, lambda x, y: (x * 60, y * 60, 128))
        self.assertEqual((len(pixels), len(pixels[0])), (SCREEN_HEIGHT, SCREEN_WIDTH))
        data = encode_raw_picture(pixels)
        picture = picture_at(data, 0, len(data))
        self.assertEqual(picture.size, len(data))
        back = decode_picture(data, picture)
        for want, got in zip(pixels[0][:8], back[0][:8]):
            self.assertTrue(all(abs(a - b) <= 8 for a, b in zip(want, got[:3])))


@unittest.skipUnless(SPARE.exists() and MAIN.exists(), "owner backups not present")
class ReplaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spare = SPARE.read_bytes()
        cls.main = MAIN.read_bytes()
        cls.sbank = find_picture_bank(cls.spare, parse_config(cls.spare).sections[max(parse_config(cls.spare).sections)][0])
        cls.mbank = find_picture_bank(cls.main, parse_config(cls.main).sections[max(parse_config(cls.main).sections)][0])

    def test_stored_checksum_matches_computation_on_real_backup(self):
        self.assertEqual(stored_checksum(self.spare), trailer_checksum(self.spare))

    def test_replacement_changes_only_target_and_checksum(self):
        target = self.sbank[33]
        donor = next(p for p in self.mbank if (p.width, p.height, p.kind) == (176, 220, 0))
        new = replace_picture(self.spare, target, self.main, donor)
        self.assertEqual(len(new), len(self.spare))
        differing = [i for i in range(len(new)) if new[i] != self.spare[i]]
        inside = range(target.offset, target.offset + target.size)
        checksum = range(len(new) - 6, len(new) - 4)
        self.assertTrue(all(i in inside or i in checksum for i in differing))
        self.assertEqual(stored_checksum(new), trailer_checksum(new))

    def test_size_mismatch_and_compressed_are_refused(self):
        small = next(p for p in self.sbank if p.width < 100)
        with self.assertRaises(PictureReplaceError):
            replace_picture(self.spare, self.sbank[33], self.spare, small)
        compressed = next(p for p in self.sbank if p.kind == 1)
        with self.assertRaises(PictureReplaceError):
            replace_picture(self.spare, compressed, self.spare, compressed)


if __name__ == "__main__":
    unittest.main()
