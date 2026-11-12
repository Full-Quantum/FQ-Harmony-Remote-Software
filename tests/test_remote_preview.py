import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.remote_preview import (
    RemotePreviewPage,
    RemotePreviewRow,
    _consistent_device_pages,
    read_remote_preview,
    render_remote_preview_page,
)
from harmony_one.screen_styles import MenuPage, PictureDraw


def raw_picture(width: int, height: int, color: int) -> bytes:
    header = bytes((0,)) + width.to_bytes(2, "little") + height.to_bytes(2, "little")
    pixel = color.to_bytes(2, "big")
    return header + pixel * (width * height)


class RemotePreviewTests(unittest.TestCase):
    def test_device_preview_keeps_the_mode_with_most_unique_devices(self):
        pages = (
            RemotePreviewPage(
                "devices", 40, 0, b"", (RemotePreviewRow(38, "TV", b"", None),)
            ),
            RemotePreviewPage(
                "devices",
                40,
                1,
                b"",
                (RemotePreviewRow(92, "Receiver", b"", None),),
            ),
            RemotePreviewPage(
                "devices",
                41,
                0,
                b"",
                (
                    RemotePreviewRow(38, "TV", b"", None),
                    RemotePreviewRow(92, "Receiver", b"", None),
                    RemotePreviewRow(146, "Player", b"", None),
                ),
            ),
            RemotePreviewPage(
                "activities", 50, 0, b"", (RemotePreviewRow(38, "Movie", b"", None),)
            ),
        )

        selected = _consistent_device_pages(
            pages, {"tv": "TV", "receiver": "Receiver", "player": "Player"}
        )

        self.assertEqual(
            [(page.role, page.mode, page.page) for page in selected],
            [("devices", 41, 0), ("activities", 50, 0)],
        )

    def test_decodes_ordered_pages_and_resolves_project_names(self):
        activity_page = MenuPage(
            "activities",
            4,
            1,
            PictureDraw(0, 0, 0, 0, 0),
            {38: PictureDraw(1, 0, 0, 5, 38)},
            {38: PictureDraw(2, 0, 0, 11, 39)},
            {38: "watchtv"},
            {},
        )
        device_page = MenuPage(
            "devices",
            5,
            0,
            PictureDraw(3, 0, 0, 0, 0),
            {92: PictureDraw(4, 0, 0, 5, 92)},
            {},
            {92: "television"},
            {},
        )
        images = {
            0: b"activity background",
            1: b"activity button",
            2: b"activity icon",
            3: b"device background",
            4: b"device button",
        }
        with (
            patch("harmony_one.remote_preview.validate_image"),
            patch("harmony_one.remote_preview.parse_config", return_value=object()),
            patch("harmony_one.remote_preview._find_picture_bank", return_value=()),
            patch(
                "harmony_one.remote_preview._menu_pages",
                return_value=(device_page, activity_page),
            ),
            patch(
                "harmony_one.remote_preview.read_activities",
                return_value=(SimpleNamespace(name="Watch TV"),),
            ),
            patch(
                "harmony_one.remote_preview.read_devices",
                return_value=(SimpleNamespace(label="Television"),),
            ),
            patch(
                "harmony_one.remote_preview._image_bytes",
                side_effect=lambda _blob, draw: images[draw.instruction_offset],
            ),
            patch(
                "harmony_one.remote_preview.inspect_remote_style",
                return_value=SimpleNamespace(fingerprint="0123456789abcdef"),
            ),
            patch(
                "harmony_one.remote_preview._named_style",
                return_value="Test style",
            ),
        ):
            preview = read_remote_preview(b"remote backup")

        self.assertEqual(preview.style_name, "Test style")
        self.assertEqual(preview.style_fingerprint, "0123456789abcdef")
        self.assertEqual(
            [(page.role, page.mode, page.page) for page in preview.pages],
            [("activities", 4, 1), ("devices", 5, 0)],
        )
        self.assertEqual(preview.pages[0].rows[0].name, "Watch TV")
        self.assertEqual(preview.pages[0].rows[0].icon, b"activity icon")
        self.assertEqual(preview.pages[1].rows[0].name, "Television")
        self.assertIsNone(preview.pages[1].rows[0].icon)

    def test_rendered_preview_is_full_remote_screen_png(self):
        page = RemotePreviewPage(
            "activities",
            4,
            0,
            raw_picture(176, 220, 0x0000),
            (
                RemotePreviewRow(
                    38,
                    "Watch TV",
                    raw_picture(164, 50, 0xFFFF),
                    raw_picture(51, 48, 0xF800),
                ),
            ),
        )

        png = render_remote_preview_page(page)

        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(png[16:20], "big"), 176)
        self.assertEqual(int.from_bytes(png[20:24], "big"), 220)


if __name__ == "__main__":
    unittest.main()
