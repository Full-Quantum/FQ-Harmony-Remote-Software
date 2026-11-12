import struct
import unittest
from pathlib import Path

from harmony_one.config_reader import parse_config
from harmony_one.fonts import read_font_sets
from harmony_one.pictures import (
    PictureError,
    decode_picture,
    encode_rle_picture,
    find_picture_bank,
    picture_at,
    walk_bank,
    write_png,
)

BACKUPS = Path(__file__).resolve().parents[1] / "backups"


def raw_picture(width, height, value=0xF800):
    body = struct.pack(">H", value) * (width * height)
    return b"\x00" + struct.pack("<HH", width, height) + body


def encoded_picture(width, height):
    # Row 1: skip 1, one drawn red pixel. Row 2: skip the full width.
    stream = bytes([0x81, 0x01, 0xF8, 0x00, 0x80, 0x80 | width, 0x00])
    assert width == 2 and height == 2
    return b"\x01" + struct.pack("<HH", width, height) + stream


class PictureTests(unittest.TestCase):
    def test_rle_encoder_handles_wide_opaque_and_transparent_runs(self):
        for pixels in (
            [[0xF800] * 164, [None] * 164],
            [[None, 0, 0xFFFF] * 50],
        ):
            encoded = encode_rle_picture(pixels)
            picture = picture_at(encoded, 0, len(encoded))
            self.assertEqual(picture.end, len(encoded))
            decoded = decode_picture(encoded, picture)
            self.assertEqual(len(decoded), len(pixels))
            for original, row in zip(pixels, decoded):
                for value, pixel in zip(original, row):
                    if value is None:
                        self.assertIsNone(pixel)
                    else:
                        red, green, blue, _ = pixel
                        self.assertEqual((red >> 3) << 11 | (green >> 2) << 5 | blue >> 3, value)

    def test_rle_encoder_rejects_invalid_dimensions_pixels_and_rows(self):
        for pixels in ([], [[]], [[0] * 257], [[0], [0, 1]], [[-1]], [[0x10000]]):
            with self.subTest(pixels=str(pixels)[:30]), self.assertRaises(PictureError):
                encode_rle_picture(pixels)

    def test_raw_picture_measures_and_decodes(self):
        data = raw_picture(3, 2)
        picture = picture_at(data, 0, len(data))
        self.assertEqual((picture.kind, picture.width, picture.height, picture.size), (0, 3, 2, len(data)))
        pixels = decode_picture(data, picture)
        self.assertEqual(pixels[0][0], (255, 0, 0, 255))
        self.assertEqual(len(pixels), 2)

    def test_encoded_picture_has_transparent_pixels(self):
        data = encoded_picture(2, 2)
        picture = picture_at(data, 0, len(data))
        self.assertEqual(picture.size, len(data))
        pixels = decode_picture(data, picture)
        self.assertIsNone(pixels[0][0])
        self.assertEqual(pixels[0][1], (255, 0, 0, 255))
        self.assertEqual(pixels[1], [None, None])

    def test_bank_must_land_exactly_on_the_end(self):
        bank = raw_picture(2, 2) + encoded_picture(2, 2) + raw_picture(4, 1)
        pictures = walk_bank(bank, 0, len(bank))
        self.assertEqual([p.kind for p in pictures], [0, 1, 0])
        self.assertIsNone(walk_bank(bank + b"\x00", 0, len(bank) + 1))

    def test_bank_is_found_after_unrelated_bytes(self):
        bank = raw_picture(2, 2) + raw_picture(3, 3)
        blob = b"\x07\x07\x07\x07" + bank + b"\x00" * 6
        found = find_picture_bank(blob, 0)
        self.assertEqual([p.offset for p in found], [4, 4 + len(raw_picture(2, 2))])

    def test_oversized_and_unknown_kinds_are_rejected(self):
        self.assertIsNone(picture_at(b"\x02" + struct.pack("<HH", 2, 2) + b"\x00" * 8, 0, 13))
        self.assertIsNone(picture_at(b"\x00" + struct.pack("<HH", 999, 2) + b"\x00" * 8, 0, 13))

    def test_png_output_has_signature(self):
        path = Path(self._testMethodName + ".png")
        try:
            write_png(path, [[(1, 2, 3, 255), None]])
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        finally:
            path.unlink(missing_ok=True)


@unittest.skipUnless((BACKUPS / "HarmonyOne-current.EZHex").exists(), "owner backup not present")
class OwnerBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = (BACKUPS / "HarmonyOne-current.EZHex").read_bytes()
        cls.config = parse_config(cls.data)

    def test_picture_bank_lands_on_trailer_with_full_screens(self):
        bank = find_picture_bank(self.data, self.config.sections[max(self.config.sections)][0])
        self.assertEqual(bank[-1].end, len(self.data) - 6)
        self.assertEqual(sum((p.width, p.height) == (176, 220) for p in bank), 16)

    def test_font_sets_point_inside_the_container(self):
        sets = read_font_sets(self.data, self.config)
        self.assertEqual(len(sets), 18)
        for font in sets:
            self.assertEqual(len(font.glyph_offsets), 74)
            self.assertTrue(all(o is None or 0 <= o < len(self.data) for o in font.glyph_offsets))


if __name__ == "__main__":
    unittest.main()
