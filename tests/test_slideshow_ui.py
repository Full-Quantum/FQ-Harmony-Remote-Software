import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.pictures import Picture
from harmony_one.slideshow_cycle import CycleScreen, SlideshowCycleError
from harmony_one.slideshow_tab import SlideshowTab


class SlideshowUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.tab = SlideshowTab(tk.Frame(self.root), SimpleNamespace(root=self.root))
        self.pictures = [(i, Picture(100 + i, 0, 176, 220, 77445)) for i in range(3)]

    def test_only_cycle_pictures_are_editable_in_playback_order(self):
        cycle = [
            CycleScreen(2, 2, self.pictures[2][1], 0, 5, 0, False),
            CycleScreen(0, 0, self.pictures[0][1], 1, 1, 2, True),
        ]
        with patch("harmony_one.slideshow_tab.full_screen_pictures", return_value=self.pictures), patch(
            "harmony_one.slideshow_tab.read_picture_cycle", return_value=cycle
        ), patch.object(self.tab, "_selected"):
            self.tab.load(b"config")
        self.assertEqual([entry[0] for entry in self.tab.entries], [2, 0])
        self.assertEqual(self.tab.listbox.size(), 2)
        self.assertIn("Slot 1", self.tab.listbox.get(0))
        self.assertIn("2", self.tab.cycle_note)

    def test_unsupported_cycle_does_not_expose_unrelated_pictures(self):
        with patch("harmony_one.slideshow_tab.full_screen_pictures", return_value=self.pictures), patch(
            "harmony_one.slideshow_tab.read_picture_cycle", side_effect=SlideshowCycleError("unsupported")
        ), patch.object(self.tab, "_selected"):
            self.tab.load(b"config")
        self.assertEqual(self.tab.entries, [])
        self.assertEqual(self.tab.listbox.size(), 0)
        self.assertIn("Playback order unavailable", self.tab.status.get())
        with patch("harmony_one.slideshow_tab.messagebox.showinfo"), patch(
            "harmony_one.slideshow_tab.filedialog.askopenfilename"
        ) as open_file:
            self.tab._replace()
        open_file.assert_not_called()
        with patch("harmony_one.slideshow_tab.messagebox.showwarning") as warning:
            self.tab.blob = b"changed"
            self.tab._write()
        warning.assert_called_once()

    def test_shared_cycle_picture_refuses_independent_uploads(self):
        cycle = [
            CycleScreen(2, 2, self.pictures[2][1], 0, 5, 0, False),
            CycleScreen(0, 2, self.pictures[2][1], 1, 1, 2, True),
        ]
        with patch("harmony_one.slideshow_tab.full_screen_pictures", return_value=self.pictures), patch(
            "harmony_one.slideshow_tab.read_picture_cycle", return_value=cycle
        ), patch.object(self.tab, "_selected"):
            self.tab.load(b"config")
        self.assertEqual(self.tab.entries, [])
        self.assertIn("share", self.tab.status.get().lower())

    def test_owner_backups_show_only_cycle_slots(self):
        backups = Path(__file__).resolve().parents[1] / "backups"
        cases = [
            ("HarmonyOne-slideshow-test-5seconds-20261108.EZHex", [6, 3, 4, 7]),
            ("HarmonyOne-slideshow-observed-20261108-134253.EZHex", [5, 2, 3, 1]),
        ]
        from harmony_one.slideshow import full_screen_pictures

        for filename, expected in cases:
            path = backups / filename
            with self.subTest(filename=filename):
                if not path.exists():
                    continue
                blob = path.read_bytes()
                old_numbers = {index: number for number, (index, _) in enumerate(full_screen_pictures(blob), 1)}
                with patch.object(self.tab, "_selected"):
                    self.tab.load(blob)
                self.assertEqual([old_numbers[index] for index, _ in self.tab.entries], expected)
                self.assertEqual(self.tab.listbox.size(), 4)
                self.assertIn("Maximum: 4", self.tab.cycle_info.get())

    def test_replacing_every_cycle_slot_preserves_other_pictures(self):
        path = (
            Path(__file__).resolve().parents[1] / "backups"
            / "HarmonyOne-slideshow-observed-20261108-134253.EZHex"
        )
        if not path.exists():
            self.skipTest("Owner backup is not present")
        from harmony_one.harmony_usb import trailer_checksum
        from harmony_one.slideshow import full_screen_pictures

        blob = path.read_bytes()
        original_pictures = full_screen_pictures(blob)
        with patch.object(self.tab, "_selected"):
            self.tab.load(blob)
            cycle_offsets = [picture.offset for _, picture in self.tab.entries]
            for index in range(len(self.tab.entries)):
                self.tab.listbox.selection_clear(0, "end")
                self.tab.listbox.selection_set(index)
                source = SimpleNamespace(
                    width=lambda: 1, height=lambda: 1,
                    get=lambda _x, _y, i=index: (i * 50 + 25, 30, 200),
                )
                with patch("harmony_one.slideshow_tab.messagebox.askyesno", return_value=True), patch(
                    "harmony_one.slideshow_tab.filedialog.askopenfilename", return_value="image.png"
                ), patch("harmony_one.slideshow_tab.tk.PhotoImage", return_value=source):
                    self.tab._replace()
        for _, picture in original_pictures:
            original = blob[picture.offset:picture.end]
            updated = self.tab.blob[picture.offset:picture.end]
            if picture.offset in cycle_offsets:
                self.assertNotEqual(updated, original)
            else:
                self.assertEqual(updated, original)
        self.assertEqual(int.from_bytes(self.tab.blob[-6:-4], "little"), trailer_checksum(self.tab.blob))

    def test_declining_shared_picture_warning_does_not_open_file(self):
        self.tab.blob = b"config"
        self.tab.entries = self.pictures
        self.tab.listbox.insert("end", "Picture")
        self.tab.listbox.selection_set(0)
        with patch("harmony_one.slideshow_tab.messagebox.askyesno", return_value=False), patch(
            "harmony_one.slideshow_tab.filedialog.askopenfilename"
        ) as open_file:
            self.tab._replace()
        open_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
