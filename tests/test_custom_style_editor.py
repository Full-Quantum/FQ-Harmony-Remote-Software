import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harmony_one.custom_styles import MenuDesign, build_custom_style
from harmony_one.custom_style_editor import CustomStyleEditor
from harmony_one.screen_style_tab import ScreenStyleTab, _preview_png
from harmony_one.screen_styles import load_user_styles


class CustomStyleEditorTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.saved = Mock()
        self.editor = CustomStyleEditor(tk.ttk.Frame(self.root), _preview_png, self.saved)

    def test_independent_devices_and_activities_pages_and_disabled_text_controls(self):
        self.assertEqual(
            [self.editor.pages.tab(tab, "text") for tab in self.editor.pages.tabs()],
            ["Activities", "Devices"],
        )
        self.assertEqual(self.editor.pages.select(), str(self.editor.activities.frame))
        self.editor.devices.colour.set("#ff0000")
        self.editor.devices.refresh_preview()
        self.assertEqual(self.editor.activities.colour.get(), MenuDesign().colour)
        self.assertIsNotNone(self.editor.devices.preview)
        self.assertIsNotNone(self.editor.activities.preview)
        for page in (self.editor.devices, self.editor.activities):
            self.assertTrue(all(control.instate(["disabled"]) for control in page.text_controls))

    def test_colour_picker_and_cancel(self):
        page = self.editor.devices
        with patch(
            "harmony_one.custom_style_editor.colorchooser.askcolor",
            return_value=((255, 0, 0), "#ff0000"),
        ):
            page._pick_colour(page.colour)
        self.assertEqual(page.colour.get(), "#ff0000")
        with patch(
            "harmony_one.custom_style_editor.colorchooser.askcolor", return_value=(None, None)
        ):
            page._pick_colour(page.colour)
        self.assertEqual(page.colour.get(), "#ff0000")

    def test_image_upload_fits_screen_and_survives_source_file_removal(self):
        from harmony_one.pictures import write_png
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiny.png"
            write_png(path, [[(255, 0, 0, 255)] * 2])
            with patch(
                "harmony_one.custom_style_editor.filedialog.askopenfilename", return_value=str(path)
            ):
                self.editor.devices._browse_image()
        self.assertEqual(self.editor.devices.background.get(), "Image")
        self.assertEqual(len(self.editor.devices.image_rgba), 176 * 220 * 4)
        self.assertIsNotNone(self.editor.devices.preview)
        style = build_custom_style(
            "Image", self.editor.activities.design(), self.editor.devices.design(),
            device_image=self.editor.devices.image_rgba,
        )
        self.assertTrue(style.device_menu.background)

    def test_invalid_image_does_not_replace_existing_design(self):
        page = self.editor.devices
        with patch(
            "harmony_one.custom_style_editor.filedialog.askopenfilename",
            return_value="missing.png",
        ), patch("harmony_one.custom_style_editor.messagebox.showerror") as error:
            page._browse_image()
        error.assert_called_once()
        self.assertIsNone(page.image_rgba)
        self.assertEqual(page.background.get(), "Solid colour")

    def test_invalid_settings_clear_preview_and_block_save(self):
        self.editor.devices.border_width.set("not a number")
        self.editor.devices.refresh_preview()
        self.assertIsNone(self.editor.devices.preview)
        self.assertIn("whole number", self.editor.devices.status.get())
        with patch("harmony_one.custom_style_editor.save_user_style") as save, patch(
            "harmony_one.custom_style_editor.messagebox.showerror"
        ) as error:
            self.editor._save()
        error.assert_called_once()
        save.assert_not_called()
        self.saved.assert_not_called()

    def test_destroy_cancels_pending_preview(self):
        page = self.editor.devices
        page.colour.set("#ff0000")
        pending = page.pending_preview
        self.assertIn(pending, self.root.tk.call("after", "info"))
        page.frame.destroy()
        self.assertIsNone(page.pending_preview)
        self.assertNotIn(pending, self.root.tk.call("after", "info"))

    def test_cancel_image_selection_leaves_design_unchanged(self):
        page = self.editor.devices
        with patch(
            "harmony_one.custom_style_editor.filedialog.askopenfilename", return_value=""
        ), patch("harmony_one.custom_style_editor.import_rgba_image") as importer:
            page._browse_image()
        importer.assert_not_called()
        self.assertEqual(page.background.get(), "Solid colour")

    def test_save_uses_library_and_does_not_touch_usb_or_require_a_backup(self):
        self.editor.name.set("Separate menus")
        self.editor.devices.colour.set("#ff0000")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom_screen_styles.json"
            with patch("harmony_one.screen_styles._style_library_path", return_value=path):
                self.editor.save_button.invoke()
            styles = load_user_styles(path)
        self.assertEqual(len(styles), 1)
        style = next(iter(styles.values()))
        self.assertEqual(style.name, "Separate menus")
        self.assertNotEqual(style.device_menu.background, style.activity_menu.background)
        self.saved.assert_called_once_with(next(iter(styles)), style)

    def test_saved_design_appears_in_apply_styles_and_can_generate_candidate(self):
        with patch("harmony_one.screen_style_tab.load_user_styles", return_value={}):
            tab = ScreenStyleTab(tk.ttk.Frame(self.root), SimpleNamespace(root=self.root))
        tab.result = SimpleNamespace(candidate=b"stale")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom_screen_styles.json"
            with patch("harmony_one.screen_styles._style_library_path", return_value=path):
                tab.custom_editor.save_button.invoke()
            self.assertEqual(tab.sections.select(), str(tab.apply_page))
            self.assertEqual(tab.selected_style_name.get(), "My custom style")
            self.assertIn("My custom style", tab.style_picker.cget("values"))
            self.assertIsNone(tab.result)
            style = next(iter(load_user_styles(path).values()))
        tab.load(b"backup", None)
        with patch(
            "harmony_one.screen_style_tab.create_style_candidate_from_style",
            return_value=SimpleNamespace(
                style_fingerprint=style.fingerprint, changed_pages=1,
                changed_references=3, appended_pictures=2, candidate=b"candidate",
            ),
        ) as generate:
            tab.generate_button.invoke()
        generate.assert_called_once_with(b"backup", style)
        self.assertEqual(tab.result.candidate, b"candidate")
