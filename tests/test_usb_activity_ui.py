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
        with patch("harmony_one.app.messagebox.askyesno", return_value=True):
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
        self.assertEqual(self.app.tabs.select(), str(self.app.backup_transfer_frame))
        self.assertEqual(
            self.app.backup_transfer_tabs.select(), str(self.app.backup_frame)
        )
        self.assertEqual(self.app.tabs.tab(self.app.usb_tab, "text"), "USB")

    def test_archive_download_uses_a_unique_ezhex_and_refreshes_transfer_list(self):
        self.app._start_backup(archive_after=True)
        destination_text = self.app.backup_progress_text.get()
        self.assertIn("HarmonyOne-", destination_text)
        self.assertTrue(destination_text.endswith(".EZHex"))
        self.assertNotIn("HarmonyOne-current.EZHex", destination_text)

        destination = Path(self.directory.name) / "HarmonyOne-20261110-100000-123456.EZHex"
        with (
            patch.object(self.app.save_restore, "_refresh_saved_backups") as refresh,
            patch.object(self.app.slideshow, "load") as load_slideshow,
            patch("harmony_one.app.messagebox.showinfo"),
        ):
            self.app._backup_events.put(("complete", (destination, b"saved backup")))
            self.app._poll_backup_events()
        refresh.assert_called_once_with(destination)
        load_slideshow.assert_called_once_with(b"saved backup")
        self.assertFalse(self.app._backup_running)

    def test_scan_and_export_icons_requires_readiness_confirmation(self):
        with (
            patch("harmony_one.app.messagebox.askyesno", return_value=True) as confirm,
            patch.object(self.app, "_start_backup") as start_backup,
        ):
            self.app._scan_and_export_icons()

        confirm.assert_called_once()
        self.assertIn("charged and ready", confirm.call_args.args[1])
        start_backup.assert_called_once_with(
            archive_after=True, export_icons_after=True
        )

    def test_open_icons_folder_creates_directory_and_opens_explorer(self):
        directory = Path(self.directory.name) / "icons"
        with (
            patch("harmony_one.app.icon_library_directory", return_value=directory),
            patch("harmony_one.app.os.startfile") as startfile,
        ):
            self.app._open_icons_folder()

        self.assertTrue(directory.is_dir())
        startfile.assert_called_once_with(str(directory))

    def test_open_icons_folder_reports_errors(self):
        directory = Path(self.directory.name) / "icons"
        with (
            patch("harmony_one.app.icon_library_directory", return_value=directory),
            patch("harmony_one.app.os.startfile", side_effect=OSError("Explorer failed")),
            patch("harmony_one.app.messagebox.showerror") as showerror,
        ):
            self.app._open_icons_folder()

        showerror.assert_called_once()
        self.assertIn(str(directory), showerror.call_args.args[1])

    def test_icon_export_runs_after_validated_backup_and_saves_to_user_library(self):
        destination = Path(self.directory.name) / "HarmonyOne-scan.EZHex"
        self.app._backup_running = True
        self.app._export_icons_after_backup = True
        self.app.export_icons_button.configure(state="disabled")
        exported = (
            Path(self.directory.name) / "icons" / "remote-icon-001.png",
        )
        with (
            patch("harmony_one.app.export_remote_menu_icons", return_value=exported) as export_icons,
            patch.object(self.app.slideshow, "load") as load_slideshow,
            patch("harmony_one.app.messagebox.showinfo"),
        ):
            self.app._backup_events.put(("complete", (destination, b"backup")))
            self.app._poll_backup_events()

        export_icons.assert_called_once_with(b"backup")
        load_slideshow.assert_called_once_with(b"backup")
        self.assertFalse(self.app._backup_running)
        self.assertEqual(str(self.app.export_icons_button.cget("state")), "normal")
        self.assertIn("Exported 1 distinct menu icons", self.app.usb_status.get())


if __name__ == "__main__":
    unittest.main()
