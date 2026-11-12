import tkinter as tk
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harmony_one.save_restore_tab import SaveRestoreTab
from harmony_one.config_reader import parse_config
from harmony_one.save_restore import SaveRestoreError, validate_source_save
from test_config_image import fixture


class SaveRestoreTabTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        app = SimpleNamespace(
            root=self.root,
            backup_initialdir=lambda: Path("."),
            _backup_running=False,
            slideshow=SimpleNamespace(writing=False),
            clock=SimpleNamespace(writing=False),
            screen_styles=SimpleNamespace(writing=False),
            activity_upload_dialog=None,
        )
        self.tab = SaveRestoreTab(tk.Frame(self.root), app)

    def test_write_button_requires_source_target_and_both_confirmations(self):
        self.assertEqual(str(self.tab.restore_button.cget("state")), "disabled")
        self.tab.source_blob = fixture()
        self.tab.target_blob = b"different validated target"
        self.tab.target_ready.set(True)
        self.tab._update_buttons()
        self.assertEqual(str(self.tab.restore_button.cget("state")), "disabled")

        self.tab.confirm_replace.set(True)
        self.tab._update_buttons()
        self.assertEqual(str(self.tab.restore_button.cget("state")), "normal")

        self.tab.busy = True
        self.tab._update_buttons()
        self.assertEqual(str(self.tab.restore_button.cget("state")), "disabled")

    def test_backup_folder_button_is_beside_destination_read_and_opens_actual_folder(self):
        self.assertIs(self.tab.open_backup_folder_button.master, self.tab.read_button.master)
        siblings = self.tab.read_button.master.winfo_children()
        self.assertEqual(siblings.index(self.tab.open_backup_folder_button), siblings.index(self.tab.read_button) + 1)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "backups"
            with patch.object(self.tab, "_backup_directory", return_value=folder), patch(
                "harmony_one.save_restore_tab.os.startfile"
            ) as start:
                self.tab.open_backup_folder_button.invoke()
            self.assertTrue(folder.is_dir())
            start.assert_called_once_with(str(folder))

    def test_backup_folder_open_errors_are_reported(self):
        with patch("harmony_one.save_restore_tab.os.startfile", side_effect=OSError("denied")), patch(
            "harmony_one.save_restore_tab.messagebox.showerror"
        ) as error:
            self.tab._open_backup_folder()
        error.assert_called_once()
        self.assertIn("denied", error.call_args.args[1])

    def test_transfer_workflow_is_in_a_scrollable_step_by_step_page(self):
        self.assertIsNotNone(self.tab.canvas)
        self.assertIsNotNone(self.tab.scrollbar)
        self.assertEqual(
            self.tab.canvas.itemcget(self.tab.content_window, "window"),
            str(self.tab.content),
        )
        section_titles = [
            str(widget.cget("text"))
            for widget in self.tab.content.winfo_children()
            if isinstance(widget, tk.ttk.LabelFrame)
        ]
        self.assertEqual(
            section_titles,
            [
                "1. Choose the backup to copy",
                "2. Back up and check the destination remote",
                "3. Replace the destination",
            ],
        )
        with patch.object(self.tab.canvas, "yview_scroll") as scroll:
            self.tab._scroll_with_mousewheel(SimpleNamespace(delta=-120))
        scroll.assert_called_once_with(1, "units")

    def test_source_scan_is_disabled_until_charged_usb_confirmation(self):
        self.assertEqual(str(self.tab.source_button.cget("state")), "disabled")
        with patch("harmony_one.save_restore_tab.open_harmony_one") as open_remote:
            self.tab._scan_source()
        open_remote.assert_not_called()

        self.tab.source_ready.set(True)
        self.tab._update_buttons()
        self.assertEqual(str(self.tab.source_button.cget("state")), "normal")
        self.tab.busy = True
        self.tab._update_buttons()
        self.assertEqual(str(self.tab.source_button.cget("state")), "disabled")

    def test_loading_source_requires_separate_destination_confirmation(self):
        blob = fixture()
        config = validate_source_save(blob)
        self.tab.target_ready.set(True)
        self.tab.confirm_replace.set(True)
        self.tab._set_source(blob, Path("source.EZHex"), config)
        self.assertIs(self.tab.source_blob, blob)
        self.assertFalse(self.tab.source_ready.get())
        self.assertFalse(self.tab.target_ready.get())
        self.assertFalse(self.tab.confirm_replace.get())
        self.assertFalse(self.tab.source_requires_disconnect)
        self.assertIn("Connect the remote you want to upload to", self.tab.target_info.get())
        self.assertEqual(config, parse_config(blob))

    def test_edit_candidate_requires_its_original_baseline_and_source_reload_clears_it(self):
        source = fixture()
        baseline = b"original baseline"
        self.tab.load_edit_candidate(source, Path("candidate.EZHex"), baseline)
        self.assertEqual(self.tab.expected_target_blob, baseline)
        self.assertFalse(self.tab.source_requires_disconnect)
        self.assertFalse(self.tab.target_ready.get())
        self.assertFalse(self.tab.confirm_replace.get())
        self.tab._check_edit_baseline(baseline)
        with self.assertRaisesRegex(SaveRestoreError, "exactly match"):
            self.tab._check_edit_baseline(b"different remote")
        self.tab._set_source(source, Path("other.EZHex"), parse_config(source))
        self.assertIsNone(self.tab.expected_target_blob)
        self.tab._check_edit_baseline(b"different remote")

    def test_edit_candidate_rejects_mismatched_target_on_read_and_before_write(self):
        source = fixture()
        for operation in ("read", "write"):
            with self.subTest(operation=operation):
                self.tab.busy = False
                self.tab.load_edit_candidate(source, Path("candidate.EZHex"), b"original")
                self.tab.target_ready.set(True)
                self.tab.confirm_replace.set(True)
                self.tab.target_blob = b"original"
                device, transport = Mock(), Mock()
                with patch(
                    "harmony_one.save_restore_tab.threading.Thread",
                    side_effect=lambda **kwargs: SimpleNamespace(start=kwargs["target"]),
                ), patch(
                    "harmony_one.save_restore_tab.messagebox.askyesno", return_value=True
                ), patch(
                    "harmony_one.save_restore_tab.open_harmony_one",
                    return_value=(device, transport),
                ), patch(
                    "harmony_one.save_restore_tab.read_harmony_one_config",
                    return_value=b"different live remote",
                ), patch(
                    "harmony_one.save_restore_tab._save_validated_backup"
                ) as save, patch(
                    "harmony_one.save_restore_tab.validate_restore_pair"
                ), patch(
                    "harmony_one.save_restore_tab.write_configuration"
                ) as write, patch.object(
                    self.tab, "_backup_directory", return_value=Path(tempfile.gettempdir())
                ), patch.object(self.tab.frame, "after"):
                    if operation == "read":
                        self.tab._read_target()
                    else:
                        self.tab._restore()
                event, message = self.tab.events.get_nowait()
                self.assertEqual(event, "error")
                self.assertIn("exactly match", message)
                self.assertEqual(save.call_args_list[0].args[1], b"different live remote")
                write.assert_not_called()
                device.close.assert_called_once()

    def test_loading_bundled_preset_does_not_require_source_remote_swap(self):
        self.tab._refresh_saved_backups()
        self.tab.saved_selector.set("FQ Preset newer — built-in preset")
        self.tab._load_saved_backup()

        self.assertFalse(self.tab.source_requires_disconnect)
        self.assertEqual(self.tab.source_path.name, "FQ Preset newer.EZHex")
        self.assertFalse(self.tab.source_ready.get())
        self.assertFalse(self.tab.target_ready.get())
        self.assertIn("Connect the remote you want to upload to", self.tab.target_info.get())

    def test_scanned_backup_is_saved_for_later_or_can_continue_to_other_remote(self):
        blob = fixture()
        path = Path("backups/HarmonyOne-transfer-source-test.EZHex")
        self.tab.events.put(("source", (blob, path)))
        self.tab._poll()

        self.assertTrue(self.tab.source_requires_disconnect)
        self.assertIn("disconnect", self.tab.target_info.get())
        self.assertIn("saved-contents list later", self.tab.status.get())
        self.assertIn(str(path), self.tab.status.get())

    def test_saved_backup_list_includes_downloads_and_selects_requested_backup(self):
        blob = fixture()
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "HarmonyOne-transfer-source-test.EZHex"
            backup.write_bytes(blob)
            with patch.object(
                SaveRestoreTab, "_backup_directory", return_value=Path(directory)
            ):
                self.tab._refresh_saved_backups(backup)

            self.assertEqual(
                self.tab.saved_selector.get(),
                "HarmonyOne-transfer-source-test.EZHex — saved backup",
            )
            self.tab._load_saved_backup()
            self.assertEqual(self.tab.source_path, backup)
            self.assertEqual(self.tab.source_blob, blob)


if __name__ == "__main__":
    unittest.main()
