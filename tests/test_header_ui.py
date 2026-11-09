import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from harmony_one import __version__
from harmony_one.app import APP_NAME, HarmonyOfflineApp
from harmony_one.harmony_project import new_project, save_project


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

    def test_brand_version_and_json_notice(self):
        self.assertIn(APP_NAME, self.root.title())
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

    def test_new_remote_data_button_and_open_save_dialog_labels(self):
        labels = [widget.cget("text") for widget in self.root.winfo_children()
                  for widget in widget.winfo_children() if "text" in widget.keys()]
        self.assertIn("New Remote Data", labels)
        with patch("harmony_one.app.filedialog.askopenfilename", return_value="") as dialog:
            self.app._open_project()
        self.assertEqual(dialog.call_args.kwargs["title"], "Open Remote Data")
        with patch("harmony_one.app.filedialog.asksaveasfilename", return_value="") as dialog:
            self.app._save_project_as()
        self.assertEqual(dialog.call_args.kwargs["title"], "Save Remote Data")

    def test_features_page_removed_other_pages_retained(self):
        labels = [self.app.tabs.tab(tab, "text") for tab in self.app.tabs.tabs()]
        self.assertEqual(labels, ["Home", "Devices", "Activities", "Backup", "Slideshow", "USB"])
        self.assertFalse(hasattr(self.app, "features_tab"))


if __name__ == "__main__":
    unittest.main()
