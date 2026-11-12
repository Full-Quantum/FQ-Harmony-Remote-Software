import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from harmony_one import __version__
from harmony_one.app import APP_NAME, HarmonyOfflineApp
from harmony_one.harmony_project import new_project, save_project
from harmony_one.pictures import encode_png
from harmony_one.remote_preview import (
    RemotePreview,
    RemotePreviewPage,
    RemotePreviewRow,
)


class HeaderUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        for name in ("portable_directory", "backup_directory"):
            mock = patch(f"harmony_one.app.{name}", return_value=self.path)
            mock.start()
            self.addCleanup(mock.stop)
        self.app = HarmonyOfflineApp(self.root)
        self.addCleanup(self._cancel_pending_callbacks)

    def _cancel_pending_callbacks(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", callback)

    def test_brand_version_and_json_notice(self):
        self.assertIn(APP_NAME, self.root.title())
        self.assertEqual(self.app.tabs.tab(self.app.clock_frame, "text"), "Clock")
        self.assertEqual(
            self.app.tabs.tab(self.app.backup_transfer_frame, "text"),
            "Backups & Transfer",
        )
        top_level_tabs = [
            self.app.tabs.tab(tab, "text") for tab in self.app.tabs.tabs()
        ]
        self.assertNotIn("Backup", top_level_tabs)
        self.assertNotIn("Transfer Remote", top_level_tabs)
        self.assertIn("Devices", top_level_tabs)
        self.assertIn("Activities", top_level_tabs)
        self.assertEqual(
            top_level_tabs[top_level_tabs.index("Activities") + 1], "Devices"
        )
        self.assertIn("Remote Assistant", top_level_tabs)
        self.assertNotIn("Features", top_level_tabs)
        self.assertEqual(
            top_level_tabs[top_level_tabs.index("Display Style") + 1], "Icons"
        )
        self.assertEqual(self.app.tabs.tab(self.app.icons_frame, "text"), "Icons")
        self.assertEqual(
            self.app.backup_transfer_tabs.tab(self.app.restore_save_frame, "text"),
            "Transfer",
        )
        self.assertEqual(
            self.app.backup_transfer_tabs.tab(self.app.backup_frame, "text"),
            "Browse Contents",
        )
        self.assertNotIn("Memory Map", top_level_tabs)
        self.assertEqual(self.app.home_preview_style.get(), "Style: Not scanned")
        self.assertIn("Scan or load a remote backup", self.app.home_preview_canvas.itemcget(
            self.app.home_preview_placeholder, "text"
        ))
        text = self.app.header_status.cget("text")
        self.assertIn(f"Version {__version__}", text)
        self.assertIn("Remote Data JSON (not yet saved): HarmonyProject.json", text)
        self.assertIn("not the raw remote backup", text)
        header = self.app.header_status.master
        texts = [
            widget.cget("text")
            for frame in header.winfo_children()
            if isinstance(frame, tk.ttk.Frame)
            for widget in frame.winfo_children()
            if isinstance(widget, tk.ttk.Label)
        ]
        self.assertIn(APP_NAME, texts)
        self.assertIn("Model: Harmony One", texts)

    def test_object_export_places_activities_on_left_and_preserves_selection_ids(self):
        bundle = {"objects": [
            {"object_type": "device", "name": "TV", "object_id": "device-tv"},
            {"object_type": "activity", "name": "Watch TV", "object_id": "activity-tv"},
        ]}

        def export_from_dialog(dialog):
            groups = sorted(
                (child for child in dialog.winfo_children() if isinstance(child, tk.ttk.LabelFrame)),
                key=lambda child: child.grid_info()["column"],
            )
            self.assertEqual(
                [group.cget("text") for group in groups], ["Activities (1)", "Devices (1)"]
            )
            for group, expected in zip(groups, ("Watch TV", "TV")):
                listing = next(child for child in group.winfo_children() if isinstance(child, tk.Listbox))
                self.assertIn(expected, listing.get(0))
            buttons = next(
                child for child in dialog.winfo_children()
                if isinstance(child, tk.ttk.Frame) and not isinstance(child, tk.ttk.LabelFrame)
            )
            next(
                child for child in buttons.winfo_children() if child.cget("text") == "Export selected..."
            ).invoke()

        with patch.object(tk.Toplevel, "wait_window", autospec=True, side_effect=export_from_dialog):
            selected = self.app._choose_objects_to_export(bundle)
        self.assertCountEqual(selected, ["device-tv", "activity-tv"])

    def test_custom_style_maker_fits_normal_and_minimum_window_sizes(self):
        tab = self.app.screen_styles
        self.root.deiconify()
        self.app.tabs.select(tab.frame)
        tab.sections.select(tab.maker_page)
        editor = tab.custom_editor
        for geometry in ("1040x730", "820x580"):
            self.root.geometry(geometry)
            for page in (editor.devices, editor.activities):
                with self.subTest(geometry=geometry, role=page.role):
                    editor.pages.select(page.frame)
                    self.root.update()
                    viewport_right = editor.canvas.winfo_rootx() + editor.canvas.winfo_width()
                    for child in page.frame.winfo_children():
                        self.assertLessEqual(child.winfo_rootx() + child.winfo_width(), viewport_right)
                    editor.canvas.yview_moveto(1)
                    self.root.update()
                    save_bottom = editor.save_button.winfo_rooty() + editor.save_button.winfo_height()
                    self.assertLessEqual(
                        save_bottom, editor.canvas.winfo_rooty() + editor.canvas.winfo_height(),
                    )

    def test_restore_save_action_routes_to_clone_workflow(self):
        self.app._open_restore_save()
        self.assertEqual(self.app.tabs.select(), str(self.app.backup_transfer_frame))
        self.assertEqual(
            self.app.backup_transfer_tabs.select(), str(self.app.restore_save_frame)
        )
        self.assertIn("choose a saved .EZHex", self.app.save_restore.status.get())
        self.assertIn("not included", self.app.save_restore.status.get())

    def test_slideshow_timing_and_reset_fit_at_supported_window_sizes(self):
        tab = self.app.slideshow
        self.root.deiconify()
        self.app.tabs.select(self.app.slideshow_frame)
        for geometry in ("1040x730", "820x580"):
            self.root.geometry(geometry)
            self.root.update()
            for widget in (
                tab.slot_browse_frame, tab.canvas, tab.default_button, tab.write_button,
            ):
                with self.subTest(geometry=geometry, widget=str(widget)):
                    self.assertLessEqual(
                        widget.winfo_rootx() + widget.winfo_width(),
                        tab.frame.winfo_rootx() + tab.frame.winfo_width(),
                    )
                    self.assertLessEqual(
                        widget.winfo_rooty() + widget.winfo_height(),
                        tab.frame.winfo_rooty() + tab.frame.winfo_height(),
                    )

    def test_backup_browser_is_a_section_of_combined_page(self):
        with patch("harmony_one.backup_browser.filedialog.askopenfilename", return_value=""):
            self.app._home_browse_backup()
        self.assertEqual(self.app.tabs.select(), str(self.app.backup_transfer_frame))
        self.assertEqual(
            self.app.backup_transfer_tabs.select(), str(self.app.backup_frame)
        )

    def test_imported_backup_opens_device_editor_after_discard_confirmation(self):
        source = self.path / "remote.EZHex"
        with patch.object(self.app, "_confirm_discard", return_value=True), patch.object(
            self.app, "_load_remote_blob", return_value=True
        ) as load:
            self.app._start_editing_backup_copy(b"backup", source)

        load.assert_called_once_with(b"backup", source)
        self.assertEqual(self.app.tabs.select(), str(self.app.device_tab))

    def test_home_contains_project_actions_without_a_top_ribbon(self):
        home_cards = []

        def all_widgets(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.ttk.LabelFrame):
                    home_cards.append(str(child.cget("text")))
                yield child
                yield from all_widgets(child)

        buttons = [
            str(widget.cget("text"))
            for widget in all_widgets(self.app.home_tab)
            if isinstance(widget, tk.ttk.Button)
        ]
        self.assertEqual(
            buttons,
            [
                "Back up connected Harmony One Remote and start editing a copy...",
                "Open backup folder",
            ],
        )
        self.assertEqual(home_cards, ["Connected remote", "Scanned remote preview"])
        self.assertEqual(self.app.root.winfo_children()[1], self.app.tabs)

    def test_home_preview_switches_between_menu_types_and_pages(self):
        activity_page = RemotePreviewPage(
            "activities", 1, 0, b"", (RemotePreviewRow(38, "Movie", b"", None),)
        )
        device_page = RemotePreviewPage(
            "devices", 2, 0, b"", (RemotePreviewRow(92, "Television", b"", None),)
        )
        preview = RemotePreview(
            (activity_page, device_page), "Older", "0123456789abcdef"
        )
        png = encode_png([[(0, 0, 0, 255)]])
        with (
            patch("harmony_one.app.read_remote_preview", return_value=preview),
            patch("harmony_one.app.render_remote_preview_page", return_value=png),
        ):
            self.app._load_home_preview(b"backup")
            self.assertEqual(
                tuple(self.app.home_preview_role_combo.cget("values")),
                ("Activities", "Devices"),
            )
            self.assertEqual(self.app.home_preview_page.get(), "Page 1")
            self.assertIn("Older", self.app.home_preview_style.get())
            self.app.home_preview_role.set("Devices")
            self.app._on_home_preview_role_changed()
            self.assertEqual(self.app.home_preview_page.get(), "Page 1")
            canvas_text = [
                self.app.home_preview_canvas.itemcget(item, "text")
                for item in self.app.home_preview_canvas.find_all()
                if self.app.home_preview_canvas.type(item) == "text"
            ]
            self.assertIn("Television", canvas_text)
            self.app.home_preview_role.set("Activities")
            self.app._on_home_preview_role_changed()
            self.assertEqual(self.app.home_preview_page.get(), "Page 1")
            canvas_text = [
                self.app.home_preview_canvas.itemcget(item, "text")
                for item in self.app.home_preview_canvas.find_all()
                if self.app.home_preview_canvas.type(item) == "text"
            ]
            self.assertIn("Movie", canvas_text)

    def test_icons_page_has_scan_and_folder_actions(self):
        def all_widgets(widget):
            for child in widget.winfo_children():
                yield child
                yield from all_widgets(child)

        labels = [
            str(widget.cget("text"))
            for widget in all_widgets(self.app.icons_frame)
            if "text" in widget.keys()
        ]
        self.assertIn("Scan remote and save menu icons", labels)
        self.assertIn("Open icons folder", labels)
        with (
            patch("harmony_one.app.messagebox.askyesno", return_value=True),
            patch.object(self.app, "_start_backup") as start_backup,
        ):
            self.app.export_icons_button.invoke()

        start_backup.assert_called_once_with(
            archive_after=True, export_icons_after=True
        )

    def test_icons_page_open_folder_creates_and_opens_user_folder(self):
        directory = self.path / "icons"
        with (
            patch("harmony_one.app.icon_library_directory", return_value=directory),
            patch("harmony_one.app.os.startfile") as startfile,
        ):
            for widget in self.app.icons_frame.winfo_children():
                if isinstance(widget, tk.ttk.Frame):
                    for button in widget.winfo_children():
                        if (
                            isinstance(button, tk.ttk.Button)
                            and button.cget("text") == "Open icons folder"
                        ):
                            button.invoke()
                            break

        self.assertTrue(directory.is_dir())
        startfile.assert_called_once_with(str(directory))

    def test_home_open_backup_folder_creates_and_opens_user_folder(self):
        directory = self.path / "backups"
        with (
            patch("harmony_one.app.backup_directory", return_value=directory),
            patch("harmony_one.app.os.startfile") as startfile,
        ):
            self.app.home_backup_folder_button.invoke()

        self.assertTrue(directory.is_dir())
        startfile.assert_called_once_with(str(directory))

    def test_saved_opened_dirty_and_new_project_states(self):
        self.app._mark_dirty()
        self.assertIn("not yet saved; unsaved changes", self.app.header_status.cget("text"))
        self.app._save_project()
        self.assertIn("Remote Data JSON (saved)", self.app.header_status.cget("text"))
        self.app._mark_dirty()
        self.assertIn("saved; unsaved changes", self.app.header_status.cget("text"))
        with patch("harmony_one.app.messagebox.askyesno", return_value=True):
            self.app._new_project()
        self.assertIn("not yet saved", self.app.header_status.cget("text"))
        other = self.path / "other.json"
        save_project(other, new_project())
        with patch("harmony_one.app.filedialog.askopenfilename", return_value=str(other)):
            self.app._open_project()
        self.assertIn("Remote Data JSON (saved): other.json", self.app.header_status.cget("text"))

    def test_save_as_updates_selected_path_and_save_state(self):
        destination = self.path / "chosen.json"
        with patch("harmony_one.app.filedialog.asksaveasfilename", return_value=str(destination)):
            self.app._save_project_as()
        self.assertTrue(destination.exists())
        self.assertIn("Remote Data JSON (saved): chosen.json", self.app.header_status.cget("text"))

    def test_tip_centers_removed_from_both_pages(self):
        def all_widgets(widget):
            for child in widget.winfo_children():
                yield child
                yield from all_widgets(child)

        for tab in (self.app.device_tab, self.app.activity_tab):
            labels = [widget.cget("text") for widget in all_widgets(tab) if "text" in widget.keys()]
            self.assertNotIn("Tip Center", labels)
            self.assertTrue(any("local Remote Data" in label for label in labels))

    def test_project_open_and_save_dialog_labels(self):
        with patch("harmony_one.app.filedialog.askopenfilename", return_value="") as dialog:
            self.app._open_project()
        self.assertEqual(dialog.call_args.kwargs["title"], "Open Remote Data")
        with patch("harmony_one.app.filedialog.asksaveasfilename", return_value="") as dialog:
            self.app._save_project_as()
        self.assertEqual(dialog.call_args.kwargs["title"], "Save Remote Data")

    def test_transfer_page_still_offers_full_ezhex_download(self):
        self.assertIn(
            "Scan and download connected remote backup (.EZHex)...",
            [self.app.save_restore.source_button.cget("text")],
        )


if __name__ == "__main__":
    unittest.main()
