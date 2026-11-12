import struct
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from harmony_one.app_paths import (
    application_data_directory,
    backup_directory,
    custom_style_library_path,
    icon_library_directory,
    slideshow_library_directory,
)
from harmony_one.icon_library import (
    IconLibraryError,
    export_remote_menu_icons,
    load_user_menu_icons,
    seed_default_icons,
)
from harmony_one.pictures import decode_picture, picture_at, write_png
from harmony_one.remote_menu_icons import encode_remote_menu_icon_rgba


def menu_icon(red: int) -> bytes:
    rgba = bytes((red, 0, 0, 255)) * (51 * 48)
    return encode_remote_menu_icon_rgba(rgba, (51, 48))


class IconLibraryTests(unittest.TestCase):
    def test_bundled_default_icon_assets_are_valid_pngs(self):
        directory = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "harmony_one"
            / "data"
            / "default_icons"
        )
        icons = sorted(directory.glob("*.png"))

        self.assertGreaterEqual(len(icons), 15)
        for path in icons:
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack_from(">II", data, 16), (51, 48))

    def test_seed_archives_other_button_artwork_without_offering_it_as_menu_icons(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            defaults, destination = root / "defaults", root / "icons"
            (defaults / "button_assets").mkdir(parents=True)
            (defaults / "menu.png").write_bytes(b"menu")
            (defaults / "button_assets" / "system.png").write_bytes(b"system")
            seeded = seed_default_icons(destination, defaults)
            self.assertEqual(len(seeded), 2)
            self.assertEqual((destination / "button_assets" / "system.png").read_bytes(), b"system")
            self.assertEqual([path.name for path in destination.glob("*.png")], ["menu.png"])
            (destination / "button_assets" / "system.png").write_bytes(b"user version")
            seed_default_icons(destination, defaults)
            self.assertEqual(
                (destination / "button_assets" / "system.png").read_bytes(), b"user version"
            )

    def test_bundled_button_artwork_is_seeded_without_changing_original_images(self):
        defaults = (
            Path(__file__).resolve().parents[1]
            / "src" / "harmony_one" / "data" / "default_icons"
        )
        archived = sorted((defaults / "button_assets").glob("*.png"))
        self.assertTrue(archived)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            seeded = seed_default_icons(destination, defaults)
            self.assertEqual(len(seeded), len(list(defaults.rglob("*.png"))))
            for path in archived:
                data = path.read_bytes()
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
                dimensions = struct.unpack_from(">II", data, 16)
                self.assertNotEqual(dimensions, (51, 48))
                self.assertNotEqual(dimensions, (176, 220))
                self.assertEqual(
                    (destination / path.relative_to(defaults)).read_bytes(), data
                )

    def test_user_managed_folders_share_the_backup_root(self):
        root = Path("C:\\HarmonyApp")
        with patch("harmony_one.app_paths.portable_directory", return_value=root):
            self.assertEqual(backup_directory(), root / "backups")
            self.assertEqual(icon_library_directory(), root / "icons")
            self.assertEqual(slideshow_library_directory(), root / "slideshow")
            self.assertEqual(
                custom_style_library_path(), root / "custom_screen_styles.json"
            )

    def test_portable_install_keeps_user_data_beside_portable_directory(self):
        install = Path("C:\\HarmonyApp\\Portable")
        with (
            patch("harmony_one.app_paths.sys.frozen", True, create=True),
            patch("harmony_one.app_paths.portable_directory", return_value=install),
        ):
            root = application_data_directory()

        self.assertEqual(root, install.parent)

    def test_exports_distinct_remote_menu_icons_as_png_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "icons"
            with patch(
                "harmony_one.icon_library.list_menu_icon_choices",
                return_value=(menu_icon(255), menu_icon(0)),
            ):
                exported = export_remote_menu_icons(b"backup", directory)

            self.assertEqual(len(exported), 2)
            self.assertTrue(all(path.parent == directory for path in exported))
            self.assertTrue(all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in exported))
            self.assertTrue(all("harmony-one-icon-" in path.name for path in exported))

    def test_no_icons_is_reported_without_creating_export_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "icons"
            with patch("harmony_one.icon_library.list_menu_icon_choices", return_value=()):
                with self.assertRaisesRegex(IconLibraryError, "No 51 x 48 menu icons"):
                    export_remote_menu_icons(b"backup", directory)
            self.assertFalse(directory.exists())

    def test_seed_default_icons_copies_missing_files_without_overwriting_user_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            defaults = root / "defaults"
            destination = root / "icons"
            defaults.mkdir()
            (defaults / "default-icon-001.png").write_bytes(b"bundled")
            (defaults / "default-icon-002.png").write_bytes(b"another")
            destination.mkdir()
            (destination / "default-icon-001.png").write_bytes(b"user version")
            (destination / "my-icon.png").write_bytes(b"user icon")

            seeded = seed_default_icons(destination, defaults)

            self.assertEqual(len(seeded), 2)
            self.assertEqual(
                (destination / "default-icon-001.png").read_bytes(), b"user version"
            )
            self.assertEqual(
                (destination / "default-icon-002.png").read_bytes(), b"another"
            )
            self.assertEqual((destination / "my-icon.png").read_bytes(), b"user icon")

    def test_pngs_added_to_user_folder_load_into_picker_format(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_png(directory / "user.png", [[(20, 40, 60, 255)]])

            loaded, errors = load_user_menu_icons(root, directory)

        self.assertEqual(errors, ())
        self.assertEqual(loaded[0][0], "User icon: user.png")
        picture = picture_at(loaded[0][1], 0, len(loaded[0][1]))
        self.assertIsNotNone(picture)
        self.assertEqual((picture.width, picture.height), (51, 48))
        self.assertEqual(decode_picture(loaded[0][1], picture)[24][25], (16, 40, 57, 255))


if __name__ == "__main__":
    unittest.main()
