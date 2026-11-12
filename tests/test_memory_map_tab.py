import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.memory_map_tab import MemoryMapTab
from test_config_image import fixture


class MemoryMapTabTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        app = SimpleNamespace(backup_initialdir=lambda: Path("."))
        self.tab = MemoryMapTab(tk.Frame(self.root), app)

    def test_load_displays_sections_and_selected_block_preview(self):
        blob = fixture()
        self.tab.load(blob, Path("sample.EZHex"))
        self.root.update_idletasks()
        self.assertIn("sample.EZHex", self.tab.summary.get())
        self.assertEqual(len(self.tab.blocks), 4)
        self.tab.selected_index = 1
        self.tab._show_block(self.tab.blocks[1])
        self.assertIn("raw slot 00", self.tab.detail_text.get())
        self.assertTrue(self.tab.hex_text.get("1.0", tk.END).strip())

    def test_automatic_load_reports_map_problem_inline_without_popup(self):
        with patch("harmony_one.memory_map_tab.messagebox.showerror") as showerror:
            self.tab.load(b"not a backup", Path("unsupported.EZHex"), report_error=False)

        showerror.assert_not_called()
        self.assertIn("unsupported.EZHex", self.tab.summary.get())
        self.assertIn("map unavailable", self.tab.summary.get())
        self.assertEqual(self.tab.blocks, [])


if __name__ == "__main__":
    unittest.main()
