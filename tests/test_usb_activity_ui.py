import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from harmony_one.app import HarmonyOfflineApp


class USBActivityUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        directory = Path(self.directory.name)
        self.portable = patch("harmony_one.app.portable_directory", return_value=directory)
        self.portable.start()
        self.addCleanup(self.portable.stop)
        self.backup_dir = patch("harmony_one.app.backup_directory", return_value=directory)
        self.backup_dir.start()
        self.addCleanup(self.backup_dir.stop)
        self.thread = patch("harmony_one.app.threading.Thread")
        self.thread.start()
        self.addCleanup(self.thread.stop)
        self.app = HarmonyOfflineApp(self.root)

    def test_read_remote_progress_button_and_completion(self):
        self.app._read_from_remote()
        self.assertTrue(self.app._backup_running)
        self.assertEqual(self.app.tabs.tab(self.app.usb_tab, "text"), "USB - Reading")
        self.assertEqual(self.app.usb_activity_button.winfo_manager(), "pack")
        self.app._backup_events.put(("progress", (25, 100)))
        self.app._poll_backup_events()
        self.assertEqual(self.app.tabs.tab(self.app.usb_tab, "text"), "USB - Reading 25%")
        self.assertIn("25%", self.app.usb_activity_text.get())
        self.app.usb_activity_button.invoke()
        self.assertEqual(self.app.tabs.select(), str(self.app.usb_tab))
        destination = Path(self.directory.name) / "backup.EZHex"
        self.app._backup_events.put(("complete", (destination, b"backup")))
        with patch.object(self.app, "_load_remote_blob") as load:
            self.app._poll_backup_events()
        load.assert_called_once_with(b"backup", destination)
        self.assertFalse(self.app._backup_running)
        self.assertEqual(self.app.tabs.tab(self.app.usb_tab, "text"), "USB")
        self.assertEqual(self.app.usb_activity_button.winfo_manager(), "")

    def test_browse_and_export_share_indicator_and_error_clears(self):
        for flag in ("browse_after", "export_after"):
            with self.subTest(flag=flag):
                self.app._start_backup(**{flag: True})
                self.assertIn("Reading", self.app.tabs.tab(self.app.usb_tab, "text"))
                self.app._backup_events.put(("error", "USB failed"))
                with patch("harmony_one.app.messagebox.showerror") as error:
                    self.app._poll_backup_events()
                error.assert_called_once()
                self.assertEqual(self.app.tabs.tab(self.app.usb_tab, "text"), "USB")
                self.assertEqual(self.app.usb_activity_button.winfo_manager(), "")
                self.assertIn("USB failed", self.app.usb_status.get())

    def test_declined_overwrite_never_shows_activity(self):
        (Path(self.directory.name) / "HarmonyOne-current.EZHex").write_bytes(b"old")
        with patch("harmony_one.app.messagebox.askyesno", return_value=False):
            self.app._start_backup()
        self.assertFalse(self.app._backup_running)
        self.assertEqual(self.app.tabs.tab(self.app.usb_tab, "text"), "USB")
        self.assertEqual(self.app.usb_activity_button.winfo_manager(), "")

    def test_usb_tab_and_browse_completion(self):
        self.app.tabs.select(self.app.usb_tab)
        self.app._start_backup(browse_after=True)
        self.assertEqual(self.app.tabs.select(), str(self.app.usb_tab))
        destination = Path(self.directory.name) / "backup.EZHex"
        self.app._backup_events.put(("complete", (destination, b"backup")))
        with patch.object(self.app.backup_browser, "load") as load:
            self.app._poll_backup_events()
        load.assert_called_once_with(b"backup", destination)
        self.assertEqual(self.app.tabs.select(), str(self.app.backup_frame))
        self.assertEqual(self.app.tabs.tab(self.app.usb_tab, "text"), "USB")


if __name__ == "__main__":
    unittest.main()
