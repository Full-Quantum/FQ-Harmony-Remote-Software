import base64
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

from harmony_one.activities import read_activities, read_devices
from harmony_one.activity_icons import ICON_SIZE
from harmony_one.config_image import validate_image
from harmony_one.config_reader import parse_config
from harmony_one.pictures import decode_picture, picture_at, write_png
from harmony_one.remote_menu_icon_dialog import RemoteMenuIconDialog
from harmony_one.remote_menu_icons import encode_remote_menu_icon, import_remote_menu_icon
from harmony_one.remote_menu_icon_dialog import RemoteMenuIconDialog
from harmony_one.screen_styles import (
    ScreenStyleError,
    _image_bytes,
    _menu_pages,
    create_menu_icon_candidate,
    inspect_remote_style,
    list_menu_icon_choices,
    list_menu_icon_occurrences,
)

BACKUPS = Path(__file__).resolve().parents[1] / "backups"
BASELINES = (
    BACKUPS / "HarmonyOne-oldest-style-baseline-20261108-185955.EZHex",
    BACKUPS / "HarmonyOne-older-style-baseline-20261108-190909.EZHex",
    BACKUPS / "HarmonyOne-newest-style-baseline-20261108-191346.EZHex",
)


class RemoteIconEncodingTests(unittest.TestCase):
    def test_custom_rgba_icon_encodes_as_transparent_51_by_48_picture(self):
        red = bytes((255, 0, 0, 255))
        transparent = bytes((0, 0, 0, 0))
        rgba = bytearray(ICON_SIZE * ICON_SIZE * 4)
        rgba[:4] = red
        rgba[(ICON_SIZE * ICON_SIZE - 1) * 4 :] = transparent
        icon = {"kind": "custom", "rgba": base64.b64encode(rgba).decode("ascii")}

        encoded = encode_remote_menu_icon(icon)
        picture = picture_at(encoded, 0, len(encoded))

        self.assertIsNotNone(picture)
        self.assertEqual(picture.end, len(encoded))
        self.assertEqual((picture.width, picture.height), (51, 48))
        pixels = decode_picture(encoded, picture)
        self.assertIsNone(pixels[0][0])
        self.assertEqual(pixels[0][1], (255, 0, 0, 255))
        self.assertIsNone(pixels[-1][-1])

    def test_png_import_is_fitted_and_keeps_transparent_background(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "icon.png"
            write_png(path, [[(0, 255, 0, 255), None]])

            encoded = import_remote_menu_icon(path, root)
            picture = picture_at(encoded, 0, len(encoded))
            pixels = decode_picture(encoded, picture)

        self.assertEqual((picture.width, picture.height), (51, 48))
        self.assertIsNone(pixels[0][0])
        self.assertEqual(pixels[18][1], (0, 255, 0, 255))
        self.assertIsNone(pixels[18][26])


@unittest.skipUnless(all(path.exists() for path in BASELINES), "owner backups not present")
class RemoteIconCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blobs = [path.read_bytes() for path in BASELINES]

    def test_candidate_changes_one_menu_occurrence_and_preserves_remote_style(self):
        for blob in self.blobs:
            with self.subTest(length=len(blob)):
                occurrences = list_menu_icon_occurrences(blob)
                choices = list_menu_icon_choices(blob)
                target = next(item for item in occurrences if item.picture_data != choices[0])
                replacement = next(image for image in choices if image != target.picture_data)
                style_before = inspect_remote_style(blob)
                candidate = create_menu_icon_candidate(blob, target, replacement).candidate
                style_after = inspect_remote_style(candidate)

                validate_image(candidate)
                original_config = parse_config(blob)
                candidate_config = parse_config(candidate)
                self.assertEqual(
                    read_devices(blob, original_config),
                    read_devices(candidate, candidate_config),
                )
                self.assertEqual(
                    read_activities(blob, original_config),
                    read_activities(candidate, candidate_config),
                )
                self.assertEqual(
                    (style_before.activity_menu, style_before.device_menu),
                    (style_after.activity_menu, style_after.device_menu),
                )
                after = {
                    (item.role, item.mode, item.page, item.row): item.picture_data
                    for item in list_menu_icon_occurrences(candidate)
                }
                for item in occurrences:
                    key = (item.role, item.mode, item.page, item.row)
                    self.assertEqual(
                        after[key],
                        replacement if item == target else item.picture_data,
                    )

                original_pages = _menu_pages(blob, original_config)
                candidate_pages = _menu_pages(candidate, candidate_config)
                self.assertEqual(len(original_pages), len(candidate_pages))
                for original, changed in zip(original_pages, candidate_pages):
                    self.assertEqual(
                        _image_bytes(blob, original.background),
                        _image_bytes(candidate, changed.background),
                    )
                    self.assertEqual(original.buttons.keys(), changed.buttons.keys())
                    for row, draw in original.buttons.items():
                        self.assertEqual(
                            _image_bytes(blob, draw),
                            _image_bytes(candidate, changed.buttons[row]),
                        )

    def test_edit_rejects_stale_occurrence_and_wrong_size_picture(self):
        blob = self.blobs[0]
        target = list_menu_icon_occurrences(blob)[0]
        changed_target = target.__class__(
            target.role,
            target.mode,
            target.page,
            target.row,
            target.item_name,
            b"stale",
            target.button_data,
        )
        with self.assertRaisesRegex(ScreenStyleError, "changed"):
            create_menu_icon_candidate(blob, changed_target, target.picture_data)
        with self.assertRaisesRegex(ScreenStyleError, "51 x 48"):
            create_menu_icon_candidate(blob, target, b"not a picture")

    def test_icon_picker_lists_occurrences_and_previews_on_the_loaded_button_style(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        occurrences = list_menu_icon_occurrences(self.blobs[0])
        choices = list_menu_icon_choices(self.blobs[0])
        dialog = RemoteMenuIconDialog(root, tuple(occurrences[:2]), choices, lambda *_: True)
        self.addCleanup(dialog.window.destroy)

        self.assertEqual(len(dialog.occurrence_selector.cget("values")), 2)
        self.assertEqual((dialog.preview.width(), dialog.preview.height()), (164, 50))
        dialog.occurrence_selector.current(1)
        dialog._occurrence_changed()
        self.assertEqual(
            dialog.occurrence.get(),
            occurrences[1].description,
        )
        self.assertEqual((dialog.preview.width(), dialog.preview.height()), (164, 50))

    def test_item_icon_picker_has_no_menu_button_selector(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        occurrences = tuple(list_menu_icon_occurrences(self.blobs[0])[:2])
        choices = list_menu_icon_choices(self.blobs[0])
        dialog = RemoteMenuIconDialog(
            root,
            occurrences,
            choices,
            lambda *_: True,
            apply_to_all_occurrences=True,
        )
        self.addCleanup(dialog.window.destroy)

        self.assertIsNone(dialog.occurrence_selector)
        self.assertEqual(dialog.apply_button.cget("text"), "Apply to matching rows")

    def test_activity_fallback_picker_uses_a_list_instead_of_menu_button_dropdown(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        occurrences = tuple(list_menu_icon_occurrences(self.blobs[0])[:2])
        choices = list_menu_icon_choices(self.blobs[0])
        dialog = RemoteMenuIconDialog(
            root,
            occurrences,
            choices,
            lambda *_: True,
            instructions="Choose the correct activity row.",
            show_occurrence_list=True,
        )
        self.addCleanup(dialog.window.destroy)

        self.assertIsNone(dialog.occurrence_selector)
        self.assertIsNotNone(dialog.occurrence_listbox)
        self.assertEqual(dialog.occurrence_listbox.size(), 2)


if __name__ == "__main__":
    unittest.main()
