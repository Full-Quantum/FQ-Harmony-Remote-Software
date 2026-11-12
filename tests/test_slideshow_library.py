import struct
import tempfile
import unittest
from pathlib import Path

from harmony_one.app_paths import slideshow_library_directory
from harmony_one.pictures import decode_picture, encode_png
from harmony_one.slideshow import full_screen_pictures
from harmony_one.slideshow_library import (
    seed_default_slideshow_images,
)
from harmony_one.slideshow_cycle import read_picture_cycle


class SlideshowLibraryTests(unittest.TestCase):
    def test_bundled_defaults_are_four_full_screen_png_images(self):
        defaults = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "harmony_one"
            / "data"
            / "default_slideshow"
        )
        images = sorted(defaults.glob("*.png"))

        self.assertEqual(
            [path.name for path in images],
            [
                "01-charging-battery.png",
                "02-blurred-lights-1.png",
                "03-blurred-lights-2.png",
                "04-blurred-lights-3.png",
            ],
        )
        for path in images:
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack_from(">II", data, 16), (176, 220))

    def test_bundled_defaults_match_oldest_newer_profile_cycle(self):
        repository = Path(__file__).resolve().parents[1]
        source = repository / "backups" / "HarmonyOne-StyleB-20261101-120043.EZHex"
        if not source.exists():
            self.skipTest("Private historical source backup is not distributed")
        blob = source.read_bytes()
        cycle = read_picture_cycle(blob, full_screen_pictures(blob))
        defaults = repository / "src" / "harmony_one" / "data" / "default_slideshow"
        names = (
            "01-charging-battery.png",
            "02-blurred-lights-1.png",
            "03-blurred-lights-2.png",
            "04-blurred-lights-3.png",
        )

        self.assertEqual(len(cycle), len(names))
        self.assertEqual(
            [encode_png(decode_picture(blob, screen.picture)) for screen in cycle],
            [(defaults / name).read_bytes() for name in names],
        )

    def test_slideshow_folder_is_beside_backups_and_preserves_user_files(self):
        root = Path("C:\\HarmonyApp")
        with tempfile.TemporaryDirectory() as temporary:
            user_folder = Path(temporary) / "slideshow"
            defaults = (
                Path(__file__).resolve().parents[1]
                / "src"
                / "harmony_one"
                / "data"
                / "default_slideshow"
            )
            user_folder.mkdir()
            (user_folder / "01-charging-battery.png").write_bytes(b"custom")
            (user_folder / "personal.gif").write_bytes(b"gif")

            seeded = seed_default_slideshow_images(user_folder, defaults)
            listed = sorted(
                path
                for path in user_folder.iterdir()
                if path.suffix.casefold() in {".png", ".gif"}
            )
            charging_contents = (user_folder / "01-charging-battery.png").read_bytes()

        self.assertEqual(slideshow_library_directory(root), root / "slideshow")
        self.assertEqual(len(seeded), 4)
        self.assertEqual(charging_contents, b"custom")
        self.assertEqual(
            [path.suffix for path in listed], [".png", ".png", ".png", ".png", ".gif"]
        )


if __name__ == "__main__":
    unittest.main()
