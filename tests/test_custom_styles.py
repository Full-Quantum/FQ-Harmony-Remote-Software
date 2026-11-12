import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harmony_one.activities import read_activities, read_devices
from harmony_one.config_image import validate_image
from harmony_one.config_reader import parse_config
from harmony_one.custom_styles import MenuDesign, build_custom_style, render_menu_artwork
from harmony_one.fonts import read_font_sets
from harmony_one.pictures import decode_picture, picture_at
from harmony_one.screen_styles import (
    BUTTON_ROWS, ScreenStyleError, _menu_pages, _pages_match_style,
    create_style_candidate_from_style, load_user_styles, read_picture_draws, save_user_style,
)
from harmony_one.screens import read_mode_pages
from harmony_one.slideshow import locate_bank


def decode(encoded):
    picture = picture_at(encoded, 0, len(encoded))
    if picture is None or picture.end != len(encoded):
        raise AssertionError("Generated picture did not decode completely")
    return picture, decode_picture(encoded, picture)


class CustomStyleTests(unittest.TestCase):
    def test_solid_background_has_exact_screen_size_and_colour(self):
        artwork = render_menu_artwork(MenuDesign(colour="#ff0000"))
        picture, pixels = decode(artwork.background)
        self.assertEqual((picture.width, picture.height), (176, 220))
        self.assertEqual({pixel for row in pixels for pixel in row}, {(255, 0, 0, 255)})
        self.assertEqual(set(artwork.buttons), set(BUTTON_ROWS))

    def test_gradient_endpoints_and_direction(self):
        for direction in ("Vertical", "Horizontal"):
            with self.subTest(direction=direction):
                artwork = render_menu_artwork(MenuDesign(
                    background="Two-colour gradient", colour="#ff0000",
                    second_colour="#0000ff", gradient_direction=direction,
                ))
                _, pixels = decode(artwork.background)
                self.assertEqual(pixels[0][0], (255, 0, 0, 255))
                self.assertEqual(pixels[-1][-1], (0, 0, 255, 255))
                if direction == "Vertical":
                    self.assertEqual(pixels[100][0], pixels[100][-1])
                    self.assertNotEqual(pixels[0][0], pixels[100][0])
                else:
                    self.assertEqual(pixels[0][80], pixels[-1][80])
                    self.assertNotEqual(pixels[0][0], pixels[0][80])

    def test_image_background_uses_pixels_and_fills_transparency_with_first_colour(self):
        rgba = bytearray(bytes((255, 0, 0, 255)) * (176 * 220))
        rgba[:4] = bytes(4)
        rgba[4:8] = bytes((255, 0, 0, 128))
        artwork = render_menu_artwork(
            MenuDesign(background="Image", colour="#0000ff"), bytes(rgba)
        )
        _, pixels = decode(artwork.background)
        self.assertEqual(pixels[0][0], (0, 0, 255, 255))
        red, green, blue, alpha = pixels[0][1]
        self.assertEqual((red >> 3, green, blue >> 3, alpha), (16, 0, 15, 255))
        self.assertEqual(pixels[0][2], (255, 0, 0, 255))

    def test_sharp_border_has_exact_requested_pixel_thickness(self):
        artwork = render_menu_artwork(MenuDesign(
            corners="Sharp", button_colour="#0000ff", border_width=3, border_colour="#ff0000",
        ))
        picture, pixels = decode(artwork.buttons[38])
        self.assertEqual((picture.width, picture.height), (164, 50))
        for y in range(50):
            for x in range(164):
                expected = (255, 0, 0, 255) if min(x, y, 163 - x, 49 - y) < 3 else (0, 0, 255, 255)
                self.assertEqual(pixels[y][x], expected)

    def test_rounded_corners_transparent_and_border_can_be_disabled(self):
        design = MenuDesign(
            button_colour="#0000ff", border_width=3, border_colour="#ff0000",
        )
        _, pixels = decode(render_menu_artwork(design).buttons[38])
        self.assertIsNone(pixels[0][0])
        self.assertIsNone(pixels[49][163])
        self.assertEqual(pixels[0][82], (255, 0, 0, 255))
        self.assertEqual(pixels[3][82], (0, 0, 255, 255))
        for no_border in (replace(design, border=False), replace(design, border_width=0)):
            _, pixels = decode(render_menu_artwork(no_border).buttons[38])
            self.assertIsNone(pixels[0][0])
            self.assertEqual(pixels[0][82], (0, 0, 255, 255))
        _, sharp = decode(render_menu_artwork(replace(design, corners="Sharp", border=False)).buttons[38])
        self.assertEqual(sharp[0][0], (0, 0, 255, 255))

    def test_maximum_border_keeps_two_pixel_centre(self):
        _, pixels = decode(render_menu_artwork(MenuDesign(
            corners="Sharp", border_width=24,
            button_colour="#0000ff", border_colour="#ff0000",
        )).buttons[38])
        self.assertEqual(pixels[23][82], (255, 0, 0, 255))
        self.assertEqual(pixels[24][82], (0, 0, 255, 255))
        self.assertEqual(pixels[25][82], (0, 0, 255, 255))
        self.assertEqual(pixels[26][82], (255, 0, 0, 255))

    def test_invalid_inputs_are_explicit_errors(self):
        for changes in (
            {"background": "unknown"}, {"colour": "red"}, {"second_colour": "#123"},
            {"border_colour": "#zzzzzz"}, {"button_colour": "#ffffff00"},
            {"border_width": -1}, {"border_width": 25}, {"border_width": 1.5},
            {"gradient_direction": "diagonal"}, {"corners": "circle"},
            {"background": "Image"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ScreenStyleError):
                render_menu_artwork(replace(MenuDesign(), **changes))
        with self.assertRaises(ScreenStyleError):
            render_menu_artwork(MenuDesign(background="Image"), b"short")
        with self.assertRaises(ScreenStyleError):
            build_custom_style(" ", MenuDesign(), MenuDesign())

    def test_distinct_menu_designs_roundtrip_through_existing_style_library(self):
        style = build_custom_style(
            "My design", MenuDesign(colour="#ff0000"),
            MenuDesign(colour="#0000ff", corners="Sharp"),
        )
        self.assertNotEqual(style.activity_menu.background, style.device_menu.background)
        self.assertNotEqual(style.activity_menu.buttons, style.device_menu.buttons)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom_screen_styles.json"
            style_id, saved = save_user_style(style.name, style, path)
            self.assertEqual(load_user_styles(path)[style_id], saved)
            with self.assertRaisesRegex(ScreenStyleError, "already exists"):
                save_user_style("MY DESIGN", style, path)

    def test_builtin_style_names_cannot_hide_a_saved_design(self):
        style = build_custom_style("Newer", MenuDesign(), MenuDesign())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom_screen_styles.json"
            for name in ("Newer", "OLDER"):
                with self.subTest(name=name), self.assertRaisesRegex(ScreenStyleError, "already exists"):
                    save_user_style(name, style, path)
            self.assertFalse(path.exists())

    def test_real_backup_candidate_preserves_all_existing_text_fonts_and_remote_data(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "src" / "harmony_one" / "data" / "presets" / "FQ Preset older.EZHex"
        )
        blob = path.read_bytes()
        config = parse_config(blob)
        before_pages = read_mode_pages(blob, config)
        fonts = read_font_sets(blob, config)
        style = build_custom_style(
            "Independent menus",
            MenuDesign(background="Two-colour gradient", colour="#0000ff", second_colour="#ff0000"),
            MenuDesign(colour="#00ff00", corners="Sharp", border_width=4),
        )
        result = create_style_candidate_from_style(blob, style)
        candidate = result.candidate
        validate_image(candidate)
        updated = parse_config(candidate)
        self.assertGreater(result.changed_pages, 0)
        self.assertTrue(_pages_match_style(
            candidate, _menu_pages(candidate, updated),
            {"activities": style.activity_menu, "devices": style.device_menu},
        ))
        self.assertEqual(read_devices(candidate, updated), read_devices(blob, config))
        self.assertEqual(read_activities(candidate, updated), read_activities(blob, config))
        self.assertEqual(read_mode_pages(candidate, updated), before_pages)
        self.assertEqual(read_font_sets(candidate, updated), fonts)
        for page in before_pages:
            for draw in page.draws:
                # Both inline and referenced glyph strings retain their original bytes.
                length = sum(2 if code > 127 else 1 for code in draw.codes) + 1
                self.assertEqual(
                    candidate[draw.address:draw.address + length],
                    blob[draw.address:draw.address + length],
                )
        # All existing font data, including glyph pixels, precedes the picture bank.
        first_picture = locate_bank(blob)[0].offset
        changed = {i for i in range(first_picture) if candidate[i] != blob[i]}
        allowed = set(range(4, 8))
        for page in before_pages:
            for draw in read_picture_draws(blob, config, config.base_address + page.program):
                allowed.update(range(draw.pointer_offset, draw.pointer_offset + 3))
        self.assertTrue(changed <= allowed)
