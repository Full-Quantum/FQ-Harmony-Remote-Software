import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.screen_style_tab import ScreenStyleTab
from harmony_one.screen_styles import (
    RemoteStyle,
    StyleTransferResult,
    load_builtin_styles,
)


class ScreenStyleTabTests(unittest.TestCase):
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
        self.app = SimpleNamespace(root=self.root, remote_blob=b"original target")
        with patch("harmony_one.screen_style_tab.load_user_styles", return_value={}):
            self.tab = ScreenStyleTab(tk.Frame(self.root), self.app)

    def test_select_generate_and_save_stays_offline(self):
        target_path = self.path / "target.EZHex"
        donor_path = self.path / "style.EZHex"
        donor_path.write_bytes(b"donor")
        self.tab.load(b"original target", target_path)
        with patch(
            "harmony_one.screen_style_tab.filedialog.askopenfilename",
            return_value=str(donor_path),
        ), patch(
            "harmony_one.screen_style_tab.inspect_remote_style",
            return_value=SimpleNamespace(
                fingerprint=next(iter(load_builtin_styles().values())).fingerprint
            ),
        ):
            self.tab._choose_style_source()

        candidate = StyleTransferResult(b"candidate data", 4, 12, 3, "custom")
        with patch(
            "harmony_one.screen_style_tab.create_style_candidate", return_value=candidate
        ):
            self.tab._generate()
        self.assertEqual(self.tab.result, candidate)
        self.assertEqual(self.app.remote_blob, b"original target")

        destination = self.path / "candidate.EZHex"
        with patch(
            "harmony_one.screen_style_tab.filedialog.asksaveasfilename",
            return_value=str(destination),
        ):
            self.tab._save()
        self.assertEqual(destination.read_bytes(), candidate.candidate)
        self.assertIn("not been written to a remote", self.tab.status.get())
        self.assertEqual(self.app.remote_blob, b"original target")

    def test_included_style_generates_without_selecting_a_donor(self):
        self.tab.load(b"original target", self.path / "target.EZHex")
        self.assertEqual(
            self.tab.style_picker.cget("values"),
            ("Newer", "Older"),
        )
        self.assertIn("Newer", self.tab.style_info.get())
        candidate = StyleTransferResult(b"candidate data", 4, 12, 3, "builtin")
        with patch(
            "harmony_one.screen_style_tab.create_style_candidate_from_style",
            return_value=candidate,
        ) as generate:
            self.tab._generate()
        generate.assert_called_once()
        self.assertEqual(self.tab.result, candidate)
        self.assertEqual(str(self.tab.apply_button.cget("state")), "disabled")

        self.tab.allow_writes.set(True)
        self.tab._update_buttons()
        self.assertEqual(str(self.tab.apply_button.cget("state")), "normal")

    def test_preview_shows_both_builtin_styles_for_both_menus(self):
        window = self.tab._preview_styles()
        self.addCleanup(window.destroy)
        notebook = next(
            child
            for child in window.winfo_children()
            if isinstance(child, ttk.Notebook)
        )
        self.assertEqual(len(notebook.tabs()), 2)
        for tab_id in notebook.tabs():
            page = notebook.nametowidget(tab_id)
            previews = [
                child
                for child in page.winfo_children()
                if isinstance(child, ttk.LabelFrame)
            ]
            self.assertEqual(len(previews), 2)
            self.assertEqual(
                [frame.cget("text") for frame in sorted(
                    previews, key=lambda frame: frame.grid_info()["column"]
                )],
                ["Activities", "Devices"],
            )
            self.assertTrue(
                all(
                    any(isinstance(widget, tk.Canvas) for widget in frame.winfo_children())
                    for frame in previews
                )
            )

    def test_custom_backup_style_can_be_saved_and_reused(self):
        source_path = self.path / "donor.EZHex"
        source_path.write_bytes(b"donor")
        style = next(iter(load_builtin_styles().values()))
        self.tab.load(b"target", self.path / "target.EZHex")
        with patch(
            "harmony_one.screen_style_tab.filedialog.askopenfilename",
            return_value=str(source_path),
        ), patch(
            "harmony_one.screen_style_tab.inspect_remote_style",
            return_value=style,
        ):
            self.tab._choose_style_source()
        self.assertEqual(str(self.tab.save_style_button.cget("state")), "normal")

        saved = RemoteStyle(
            style.activity_menu,
            style.device_menu,
            style.fingerprint,
            "My Custom Look",
        )
        with patch(
            "harmony_one.screen_style_tab.simpledialog.askstring",
            return_value="My Custom Look",
        ), patch(
            "harmony_one.screen_style_tab.save_user_style",
            return_value=("custom-test", saved),
        ):
            self.tab._save_custom_style()

        self.assertEqual(
            self.tab.style_picker.cget("values"),
            ("Newer", "Older", "My Custom Look"),
        )
        self.assertEqual(self.tab._selected_builtin_id(), "custom-test")
        candidate = StyleTransferResult(b"candidate", 2, 4, 1, style.fingerprint)
        with patch(
            "harmony_one.screen_style_tab.create_style_candidate_from_style",
            return_value=candidate,
        ) as generate:
            self.tab._generate()
        generate.assert_called_once_with(b"target", saved)
        self.assertEqual(self.tab.result, candidate)

    def test_apply_requires_acknowledgment_and_explicit_confirmation(self):
        self.tab.load(b"original target", self.path / "target.EZHex")
        self.tab.result = StyleTransferResult(b"candidate data", 4, 12, 3, "builtin")

        with patch("harmony_one.screen_style_tab.messagebox.showwarning") as warning, patch(
            "harmony_one.screen_style_tab.threading.Thread"
        ) as thread:
            self.tab._apply_to_remote()
        warning.assert_called_once()
        thread.assert_not_called()

        self.tab.allow_writes.set(True)
        with patch(
            "harmony_one.screen_style_tab.messagebox.askyesno", return_value=False
        ) as confirm, patch("harmony_one.screen_style_tab.threading.Thread") as thread:
            self.tab._apply_to_remote()
        confirm.assert_called_once()
        self.assertIn("generic list icon", confirm.call_args.args[1])
        thread.assert_not_called()
        self.assertFalse(self.tab.writing)

    def test_apply_refuses_candidate_that_matches_target(self):
        self.tab.load(b"same", self.path / "target.EZHex")
        self.tab.result = StyleTransferResult(b"same", 0, 0, 0, "builtin")
        with patch("harmony_one.screen_style_tab.messagebox.showinfo") as info, patch(
            "harmony_one.screen_style_tab.threading.Thread"
        ) as thread:
            self.tab._apply_to_remote()
        info.assert_called_once()
        thread.assert_not_called()

    def test_apply_refuses_stale_backup_before_calling_writer(self):
        self.tab.load(b"loaded baseline", self.path / "target.EZHex")
        self.tab.result = StyleTransferResult(b"style candidate", 4, 12, 3, "builtin")
        self.tab.allow_writes.set(True)
        device = SimpleNamespace(close=lambda: None)
        transport = SimpleNamespace(flush=lambda: None)
        with patch(
            "harmony_one.screen_style_tab.messagebox.askyesno", return_value=True
        ), patch.object(self.tab, "_backup_directory", return_value=self.path), patch(
            "harmony_one.screen_style_tab.open_harmony_one",
            return_value=(device, transport),
        ), patch(
            "harmony_one.screen_style_tab.read_harmony_one_config",
            return_value=b"different live baseline",
        ), patch(
            "harmony_one.screen_style_tab._save_validated_backup"
        ) as save_backup, patch(
            "harmony_one.screen_style_tab.write_configuration"
        ) as write, patch(
            "harmony_one.screen_style_tab.threading.Thread",
            side_effect=lambda target, **_kwargs: SimpleNamespace(start=target),
        ), patch.object(
            self.tab.frame, "after", side_effect=lambda _delay, callback: callback()
        ), patch(
            "harmony_one.screen_style_tab.messagebox.showerror"
        ) as showerror:
            self.tab._apply_to_remote()

        save_backup.assert_called_once()
        write.assert_not_called()
        showerror.assert_called_once()
        self.assertIn("does not exactly match", self.tab.status.get())
        self.assertFalse(self.tab.writing)

    def test_apply_writes_only_after_fresh_backup_matches(self):
        baseline = b"loaded baseline"
        candidate = b"style candidate"
        self.tab.load(baseline, self.path / "target.EZHex")
        self.tab.result = StyleTransferResult(candidate, 4, 12, 3, "builtin")
        self.tab.allow_writes.set(True)
        device = SimpleNamespace(close=lambda: None)
        transport = SimpleNamespace(flush=lambda: None)
        self.app.slideshow = SimpleNamespace(load=lambda _blob: None)
        self.app.backup_browser = SimpleNamespace(load=lambda _blob, _path: None)
        with patch(
            "harmony_one.screen_style_tab.messagebox.askyesno", return_value=True
        ), patch.object(self.tab, "_backup_directory", return_value=self.path), patch(
            "harmony_one.screen_style_tab.open_harmony_one",
            return_value=(device, transport),
        ), patch(
            "harmony_one.screen_style_tab.read_harmony_one_config",
            return_value=baseline,
        ), patch(
            "harmony_one.screen_style_tab._save_validated_backup"
        ) as save_backup, patch(
            "harmony_one.screen_style_tab.write_configuration"
        ) as write, patch(
            "harmony_one.screen_style_tab.threading.Thread",
            side_effect=lambda target, **_kwargs: SimpleNamespace(start=target),
        ), patch.object(
            self.tab.frame, "after", side_effect=lambda _delay, callback: callback()
        ), patch(
            "harmony_one.screen_style_tab.messagebox.showinfo"
        ):
            self.tab._apply_to_remote()

        self.assertEqual(save_backup.call_count, 2)
        write.assert_called_once()
        self.assertEqual(write.call_args.args[1:3], (candidate, baseline))
        self.assertEqual(self.app.remote_blob, candidate)
        self.assertFalse(self.tab.writing)


if __name__ == "__main__":
    unittest.main()
