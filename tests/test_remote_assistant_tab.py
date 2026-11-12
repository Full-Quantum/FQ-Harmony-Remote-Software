import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harmony_one.remote_assistant import RemoteAssistantError, RemoteAssistantSetting
from harmony_one.remote_assistant_tab import RemoteAssistantTab


class RemoteAssistantTabTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = SimpleNamespace(
            root=self.root, backup_initialdir=lambda: Path("."),
            save_restore=SimpleNamespace(
                busy=False, _remote_busy=lambda: False, _refresh_saved_backups=Mock(),
                load_edit_candidate=Mock(),
            ),
            tabs=Mock(), backup_transfer_tabs=Mock(),
            backup_transfer_frame="transfers", restore_save_frame="transfer",
        )
        self.tab = RemoteAssistantTab(tk.Frame(self.root), self.app)

    def load(self, enabled=True):
        with patch(
            "harmony_one.remote_assistant_tab.inspect_remote_assistant",
            return_value=RemoteAssistantSetting(23, 100, 0 if enabled else 3),
        ):
            self.tab.load(b"baseline", Path("original.EZHex"))

    def test_no_backup_disables_on_off_and_apply(self):
        for button in (self.tab.on_button, self.tab.off_button, self.tab.apply_button):
            self.assertTrue(button.instate(["disabled"]))

    def test_loaded_state_enables_only_relevant_action(self):
        self.load()
        self.assertTrue(self.tab.on_button.instate(["disabled"]))
        self.assertTrue(self.tab.off_button.instate(["!disabled"]))
        self.assertIn("On", self.tab.setting_info.get())
        self.load(False)
        self.assertTrue(self.tab.on_button.instate(["!disabled"]))
        self.assertTrue(self.tab.off_button.instate(["disabled"]))
        self.assertIn("Off", self.tab.setting_info.get())

    def test_unsupported_backup_disables_actions_and_reports_reason(self):
        self.load()
        with patch(
            "harmony_one.remote_assistant_tab.inspect_remote_assistant",
            side_effect=RemoteAssistantError("Unknown toggle"),
        ):
            self.tab.load(b"unsupported", None)
        self.assertIn("Unknown toggle", self.tab.status.get())
        for button in (self.tab.on_button, self.tab.off_button, self.tab.apply_button):
            self.assertTrue(button.instate(["disabled"]))

    def test_saving_separate_candidate_does_not_change_loaded_backup_or_use_usb(self):
        self.load()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "assistant-off.EZHex"
            with patch(
                "harmony_one.remote_assistant_tab.prepare_remote_assistant_candidate",
                return_value=b"candidate",
            ) as prepare, patch(
                "harmony_one.remote_assistant_tab.filedialog.asksaveasfilename",
                return_value=str(destination),
            ), patch("harmony_one.remote_assistant_tab._save_validated_backup") as save:
                self.tab.off_button.invoke()
            prepare.assert_called_once_with(b"baseline", False)
            save.assert_called_once_with(destination, b"candidate", overwrite=False)
            self.assertEqual(self.tab.candidate, b"candidate")
            self.assertTrue(self.tab.apply_button.instate(["!disabled"]))
            self.assertEqual(self.tab.source_blob, b"baseline")
            self.app.save_restore.load_edit_candidate.assert_not_called()
            self.tab.apply_button.invoke()
            self.app.save_restore.load_edit_candidate.assert_called_once_with(
                b"candidate", destination, b"baseline"
            )
            self.app.tabs.select.assert_called_once_with("transfers")
            self.app.backup_transfer_tabs.select.assert_called_once_with("transfer")

    def test_cannot_overwrite_original_and_failed_save_does_not_enable_apply(self):
        self.load()
        for destination, error in (("original.EZHex", None), ("new.EZHex", OSError("disk full"))):
            with self.subTest(destination=destination), patch(
                "harmony_one.remote_assistant_tab.prepare_remote_assistant_candidate",
                return_value=b"candidate",
            ), patch(
                "harmony_one.remote_assistant_tab.filedialog.asksaveasfilename",
                return_value=destination,
            ), patch(
                "harmony_one.remote_assistant_tab._save_validated_backup", side_effect=error
            ) as save, patch(
                "harmony_one.remote_assistant_tab.messagebox.showerror"
            ) as show_error:
                self.tab._save_change(False)
                show_error.assert_called_once()
                self.assertTrue(self.tab.apply_button.instate(["disabled"]))
                self.assertIsNone(self.tab.candidate)
                if error is None:
                    save.assert_not_called()

    def test_cancel_or_load_another_backup_clears_staged_change(self):
        self.load()
        with patch(
            "harmony_one.remote_assistant_tab.prepare_remote_assistant_candidate",
            return_value=b"candidate",
        ), patch(
            "harmony_one.remote_assistant_tab.filedialog.asksaveasfilename", return_value=""
        ), patch("harmony_one.remote_assistant_tab._save_validated_backup") as save:
            self.tab._save_change(False)
        save.assert_not_called()
        self.tab.candidate = b"old candidate"
        self.tab.candidate_path = Path("old.EZHex")
        self.load(False)
        self.assertIsNone(self.tab.candidate)
        self.assertIsNone(self.tab.candidate_path)
        self.assertTrue(self.tab.apply_button.instate(["disabled"]))

    def test_busy_transfer_cannot_be_replaced(self):
        self.load()
        self.tab.candidate = b"candidate"
        self.tab.candidate_path = Path("new.EZHex")
        self.app.save_restore.busy = True
        with patch("harmony_one.remote_assistant_tab.messagebox.showwarning") as warn:
            self.tab._apply()
        warn.assert_called_once()
        self.app.save_restore.load_edit_candidate.assert_not_called()
