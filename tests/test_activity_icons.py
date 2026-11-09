import base64
import copy
import struct
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from harmony_one.activity_icon_dialog import ActivityIconDialog
from harmony_one.activity_icons import (
    BUILTIN_ICONS,
    ICON_SIZE,
    IconError,
    icon_pixels,
    import_icon,
    make_preview,
    validate_icon,
)
from harmony_one.harmony_project import ProjectError, load_project, new_project, save_project, validate_project
from harmony_one.pictures import write_png
from harmony_one.project_objects import export_project_object, import_project_object
from harmony_one.remote_writer import plan_changes


class IconModelTests(unittest.TestCase):
    def test_builtins_and_invalid_icons(self):
        for name in BUILTIN_ICONS:
            self.assertEqual(len(icon_pixels({"kind": "builtin", "name": name})), 4096)
        for icon in (None, {}, {"kind": "builtin", "name": []},
                     {"kind": "builtin", "name": "other"},
                     {"kind": "custom", "rgba": "bad"}):
            with self.subTest(icon=icon), self.assertRaises(IconError):
                validate_icon(icon)

    def test_local_icon_roundtrips_and_blocks_write_plan(self):
        original = new_project()
        original["activities"] = [{"id": "a", "name": "TV", "steps": []}]
        edited = copy.deepcopy(original)
        edited["activities"][0]["icon"] = {
            "kind": "custom",
            "rgba": base64.b64encode(bytes([1, 2, 3, 255]) * 1024).decode("ascii"),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            save_project(path, edited)
            self.assertEqual(load_project(path), edited)
        package = export_project_object(edited, "activity", "a")
        imported_project, imported = import_project_object(new_project(), package)
        self.assertEqual(imported["icon"], edited["activities"][0]["icon"])
        validate_project(imported_project)
        self.assertEqual(plan_changes(original, edited).activity_fields_changed, [("TV", ["icon"])])

    def test_malformed_custom_data_is_rejected_in_projects(self):
        project = new_project()
        project["activities"] = [{"id": "a", "name": "TV", "steps": [], "icon": {
            "kind": "custom", "rgba": base64.b64encode(bytes([0, 0, 0, 1]) * 1024).decode("ascii")
        }}]
        with self.assertRaises(ProjectError):
            validate_project(project)


class IconImageTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "icon.png"

    def test_png_is_fitted_and_transparency_preserved(self):
        write_png(self.path, [[(255, 0, 0, 255), None]])
        icon = import_icon(self.path, self.root)
        pixels = icon_pixels(icon)
        self.assertEqual(len(pixels), ICON_SIZE * ICON_SIZE * 4)
        self.assertEqual(pixels[(8 * 32) * 4:(8 * 32) * 4 + 4], bytes((255, 0, 0, 255)))
        self.assertEqual(pixels[(8 * 32 + 31) * 4 + 3], 0)
        self.assertEqual(pixels[3], 0)
        preview = make_preview(self.root, icon)
        self.assertEqual(preview.get(0, 8), (255, 0, 0))
        self.assertTrue(preview.transparency_get(31, 8))

    def test_rejects_bad_dimensions_invalid_and_large_files(self):
        for data in (
            b"not an image",
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 1025, 1),
            b"X" * (2 * 1024 * 1024 + 1),
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 10, 10),
        ):
            self.path.write_bytes(data)
            with self.subTest(length=len(data)), self.assertRaises(IconError):
                import_icon(self.path, self.root)

    def test_gif_import_and_picker_apply_cancel(self):
        image = tk.PhotoImage(master=self.root, width=2, height=2)
        image.put("#00ff00", to=(0, 0, 2, 2))
        image.write(str(self.path), format="gif")
        icon = import_icon(self.path, self.root)
        self.assertEqual(icon_pixels(icon)[:4], bytes((0, 255, 0, 255)))
        applied = []
        dialog = ActivityIconDialog(self.root, None, applied.append)
        dialog._select({"kind": "builtin", "name": "tv"})
        dialog.window.destroy()
        self.assertEqual(applied, [])
        dialog = ActivityIconDialog(self.root, None, applied.append)
        dialog._select(icon)
        dialog._apply()
        self.assertEqual(applied, [icon])


if __name__ == "__main__":
    unittest.main()
