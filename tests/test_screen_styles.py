import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harmony_one.activities import read_activities, read_devices
from harmony_one.config_image import validate_image
from harmony_one.config_reader import parse_config
from harmony_one.pictures import decode_picture, picture_at
from harmony_one.screen_style_tab import _preview_png
from harmony_one.screen_styles import (
    ScreenStyleError,
    GENERIC_MENU_ICON,
    _image_bytes,
    _menu_pages,
    _pages_match_style,
    create_style_candidate,
    create_style_candidate_from_style,
    inspect_remote_style,
    load_builtin_styles,
    load_user_styles,
    save_user_style,
)

BACKUPS = Path(__file__).resolve().parents[1] / "backups"
BASELINES = {
    "oldest": BACKUPS / "HarmonyOne-oldest-style-baseline-20261108-185955.EZHex",
    "older": BACKUPS / "HarmonyOne-older-style-baseline-20261108-190909.EZHex",
    "newest": BACKUPS / "HarmonyOne-newest-style-baseline-20261108-191346.EZHex",
}
LIVE_REPAIR_BASELINE = (
    BACKUPS / "HarmonyOne-live-repair-baseline-20261109-134457.EZHex"
)


@unittest.skipUnless(all(path.exists() for path in BASELINES.values()), "owner backups not present")
class ScreenStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blobs = {name: path.read_bytes() for name, path in BASELINES.items()}
        cls.styles = {
            name: inspect_remote_style(blob) for name, blob in cls.blobs.items()
        }
        cls.builtin_styles = load_builtin_styles()

    def _remote_items(self, blob):
        config = parse_config(blob)
        return (
            tuple((device.label, device.group) for device in read_devices(blob, config)),
            tuple((activity.number, activity.name) for activity in read_activities(blob, config)),
        )

    def test_owner_backups_have_three_distinct_styles(self):
        fingerprints = {style.fingerprint for style in self.styles.values()}
        self.assertEqual(len(fingerprints), 3)
        self.assertEqual(set(self.builtin_styles), {"style-1", "style-2"})
        self.assertEqual(
            [style.name for style in self.builtin_styles.values()], ["Newer", "Older"]
        )
        self.assertEqual(
            self.builtin_styles["style-2"].fingerprint,
            self.styles["older"].fingerprint,
        )
        self.assertEqual(
            self.styles["oldest"].fingerprint,
            self.styles["oldest"].fingerprint,
        )
        self.assertEqual(
            self.builtin_styles["style-1"].activity_menu,
            self.styles["newest"].activity_menu,
        )
        self.assertEqual(
            self.builtin_styles["style-1"].device_menu.buttons,
            self.styles["newest"].device_menu.buttons,
        )
        self.assertEqual(
            self.builtin_styles["style-1"].device_menu.last_page_buttons,
            self.styles["newest"].device_menu.last_page_buttons,
        )
    def test_newest_style_preserves_its_page_specific_bottom_row(self):
        newest = self.styles["newest"].activity_menu
        self.assertNotEqual(newest.buttons[146], newest.last_page_buttons[146])

    def test_newest_and_oldest_differ_on_the_first_activities_page(self):
        newest = self.builtin_styles["style-1"].activity_menu
        oldest = self.styles["oldest"].activity_menu
        self.assertNotEqual(newest.buttons[146], oldest.buttons[146])
        self.assertEqual(
            newest.last_page_buttons[146],
            oldest.buttons[146],
        )
        self.assertEqual(
            self.builtin_styles["style-1"].device_menu.buttons,
            self.styles["oldest"].device_menu.buttons,
        )

    def test_newest_builtin_device_background_is_clean_black(self):
        style = self.builtin_styles["style-1"]
        background = style.device_menu.background
        picture = picture_at(background, 0, len(background))
        self.assertIsNotNone(picture)
        self.assertEqual(
            {
                pixel
                for row in decode_picture(background, picture)
                for pixel in row
            },
            {(0, 0, 0, 255)},
        )

        candidate = create_style_candidate_from_style(
            self.blobs["older"], style
        ).candidate
        config = parse_config(candidate)
        device_pages = [
            page for page in _menu_pages(candidate, config)
            if page.role == "devices"
        ]
        self.assertTrue(device_pages)
        for page in device_pages:
            with self.subTest(mode=page.mode, page=page.page):
                self.assertEqual(_image_bytes(candidate, page.background), background)

    @unittest.skipUnless(
        LIVE_REPAIR_BASELINE.exists(), "recent live backup not present"
    )
    def test_partial_final_device_page_is_detected_and_styled(self):
        blob = LIVE_REPAIR_BASELINE.read_bytes()
        original_items = self._remote_items(blob)
        config = parse_config(blob)
        target_pages = _menu_pages(blob, config)
        final_pages = [
            page for page in target_pages
            if page.role == "devices" and page.page == 1
        ]
        self.assertTrue(final_pages, "two-row final Devices page must be recognized")
        self.assertTrue(
            all(set(page.buttons) == {38, 92} for page in final_pages)
        )

        style = self.builtin_styles["style-1"]
        candidate = create_style_candidate_from_style(blob, style).candidate
        self.assertEqual(self._remote_items(candidate), original_items)
        candidate_config = parse_config(candidate)
        candidate_pages = _menu_pages(candidate, candidate_config)
        final_pages = [
            page for page in candidate_pages
            if page.role == "devices" and page.page == 1
        ]
        self.assertTrue(final_pages)
        self.assertTrue(all(len(page.icons) == 2 for page in final_pages))
        self.assertTrue(
            _pages_match_style(
                candidate,
                candidate_pages,
                {"activities": style.activity_menu, "devices": style.device_menu},
            )
        )
        for page in final_pages:
            for row, draw in page.buttons.items():
                self.assertEqual(
                    _image_bytes(candidate, draw), style.device_menu.buttons[row]
                )
            for draw in page.icons.values():
                self.assertEqual(_image_bytes(candidate, draw), GENERIC_MENU_ICON)

    def test_builtin_style_previews_render_full_lcds(self):
        for style_id, style in self.builtin_styles.items():
            for role in ("activities", "devices"):
                with self.subTest(style=style_id, role=role):
                    artwork = (
                        style.activity_menu
                        if role == "activities"
                        else style.device_menu
                    )
                    preview = _preview_png(artwork, role)
                    self.assertTrue(preview.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertEqual(preview[-8:-4], b"IEND")

    def test_same_style_source_normalizes_icons_without_changing_button_art(self):
        for name, blob in self.blobs.items():
            with self.subTest(style=name):
                result = create_style_candidate(blob, blob)
                config = parse_config(result.candidate)
                pages = _menu_pages(result.candidate, config)
                style = self.styles[name]
                self.assertTrue(
                    _pages_match_style(
                        result.candidate,
                        pages,
                        {
                            "activities": style.activity_menu,
                            "devices": style.device_menu,
                        },
                    )
                )
                self.assertEqual(
                    self._remote_items(result.candidate), self._remote_items(blob)
                )
                self.assertTrue(
                    all(
                        _image_bytes(result.candidate, draw) == GENERIC_MENU_ICON
                        for page in pages
                        for draw in page.icons.values()
                    )
                )

    def test_builtin_styles_apply_without_a_donor_backup(self):
        target = self.blobs["oldest"]
        for name, style in self.builtin_styles.items():
            with self.subTest(style=name):
                result = create_style_candidate_from_style(target, style)
                self.assertEqual(
                    result.style_fingerprint,
                    style.fingerprint,
                )
                self.assertEqual(
                    self._remote_items(result.candidate),
                    self._remote_items(target),
                )

    def test_each_style_transfers_to_the_candidate_without_changing_remote_data(self):
        for target_name, target_blob in self.blobs.items():
            before_items = self._remote_items(target_blob)
            for donor_name, donor_blob in self.blobs.items():
                if target_name == donor_name:
                    continue
                with self.subTest(target=target_name, donor=donor_name):
                    result = create_style_candidate(target_blob, donor_blob)
                    validate_image(result.candidate)
                    self.assertEqual(
                        result.style_fingerprint,
                        self.styles[donor_name].fingerprint,
                    )
                    self.assertEqual(self._remote_items(result.candidate), before_items)
                    self.assertGreater(result.changed_references, 0)

    def test_style_transfer_replaces_every_activity_and_device_icon(self):
        target = self.blobs["newest"]
        result = create_style_candidate(target, self.blobs["older"])
        target_config = parse_config(target)
        target_pages = _menu_pages(target, target_config)
        candidate_config = parse_config(result.candidate)
        candidate_pages = _menu_pages(result.candidate, candidate_config)
        target_by_page = {
            (page.role, page.mode, page.page): page for page in target_pages
        }
        self.assertEqual(
            set(target_by_page),
            {(page.role, page.mode, page.page) for page in candidate_pages},
        )
        for page in candidate_pages:
            original = target_by_page[(page.role, page.mode, page.page)]
            self.assertEqual(page.labels, original.labels)
            self.assertEqual(page.icons.keys(), original.icons.keys())
            for row, draw in page.icons.items():
                with self.subTest(role=page.role, mode=page.mode, page=page.page, row=row):
                    self.assertEqual(
                        _image_bytes(result.candidate, draw),
                        GENERIC_MENU_ICON,
                    )

    def test_generic_icon_is_a_valid_remote_picture(self):
        picture = picture_at(GENERIC_MENU_ICON, 0, len(GENERIC_MENU_ICON))
        self.assertIsNotNone(picture)
        self.assertEqual((picture.width, picture.height), (51, 48))
        pixels = decode_picture(GENERIC_MENU_ICON, picture)
        self.assertIsNone(pixels[0][0])
        self.assertTrue(any(pixel is not None for row in pixels for pixel in row))

    def test_older_then_newer_keeps_all_menu_art_consistent(self):
        source = self.blobs["older"]
        older = create_style_candidate_from_style(
            source, self.builtin_styles["style-2"]
        ).candidate
        newer = create_style_candidate_from_style(
            older, self.builtin_styles["style-1"]
        ).candidate
        config = parse_config(newer)
        pages = _menu_pages(newer, config)
        artwork = {
            "activities": self.builtin_styles["style-1"].activity_menu,
            "devices": self.builtin_styles["style-1"].device_menu,
        }
        self.assertTrue(_pages_match_style(newer, pages, artwork))
        for page in pages:
            for draw in page.icons.values():
                self.assertEqual(_image_bytes(newer, draw), GENERIC_MENU_ICON)

    def test_invalid_backup_is_refused(self):
        with self.assertRaises(ScreenStyleError):
            inspect_remote_style(b"not a remote backup")
        with self.assertRaises(ScreenStyleError):
            create_style_candidate(b"not a target", self.blobs["newest"])


class UserStyleLibraryTests(unittest.TestCase):
    def test_saved_style_roundtrips_and_rejects_duplicate_names(self):
        source = next(iter(load_builtin_styles().values()))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "custom-styles.json"
            style_id, saved = save_user_style("My look", source, path)
            loaded = load_user_styles(path)
            self.assertEqual(set(loaded), {style_id})
            self.assertEqual(loaded[style_id], saved)
            with self.assertRaisesRegex(ScreenStyleError, "already exists"):
                save_user_style("my LOOK", source, path)

    def test_missing_user_style_file_starts_as_an_empty_library(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(load_user_styles(Path(directory) / "styles.json"), {})


if __name__ == "__main__":
    unittest.main()
