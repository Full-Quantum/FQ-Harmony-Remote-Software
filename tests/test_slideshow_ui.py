import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.pictures import Picture
from harmony_one.slideshow_cycle import CycleScreen, SlideshowCycleError, read_picture_cycle
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
        self.assertEqual(self.tab.listbox.size(), 6)
        self.assertEqual(len(self.tab.slot_browse_buttons), 6)
        self.assertTrue(all(button.instate(["!disabled"]) for button in self.tab.slot_browse_buttons[:2]))
        self.assertTrue(all(button.instate(["disabled"]) for button in self.tab.slot_browse_buttons[2:]))
        self.assertIn("Slot 1", self.tab.listbox.get(0))
        self.assertIn("2", self.tab.cycle_note)
        with patch.object(self.tab, "_browse_slot") as browse:
            self.tab.slot_browse_buttons[1].invoke()
        browse.assert_called_once_with(1)

    def test_default_slots_are_shown_before_a_backup_is_loaded(self):
        self.assertEqual(len(self.tab.default_images), 4)
        self.assertEqual(self.tab.listbox.size(), 6)
        self.assertIn("Charging battery", self.tab.listbox.get(0))
        self.assertIn("Blurred colored lights", self.tab.listbox.get(1))
        self.assertEqual(len(self.tab.slot_browse_buttons), 6)
        self.assertEqual(self.tab.listbox.get(4, 5), ("Slot 5 - Empty", "Slot 6 - Empty"))
        self.assertTrue(
            all(button.instate(["disabled"]) for button in self.tab.slot_browse_buttons)
        )
        self.assertEqual([seconds.get() for seconds in self.tab.slot_seconds], ["3"] * 6)
        self.assertTrue(all(widget.instate(["disabled"]) for widget in self.tab.slot_time_inputs))
        self.assertTrue(self.tab.default_button.instate(["disabled"]))

    def load_preset(self, name="newer"):
        path = (
            Path(__file__).resolve().parents[1] / "src" / "harmony_one" / "data"
            / "presets" / f"FQ Preset {name}.EZHex"
        )
        blob = path.read_bytes()
        self.tab.load(blob)
        return blob

    def test_load_preserves_existing_times_and_each_slot_set_edits_only_candidate(self):
        from harmony_one.slideshow import full_screen_pictures

        blob = self.load_preset()
        self.assertEqual(self.tab.blob, blob)
        self.assertEqual([value.get() for value in self.tab.slot_seconds], ["5", "3", "3", "3", "3", "3"])
        self.assertTrue(all(widget.instate(["!disabled"]) for widget in self.tab.slot_time_inputs))
        self.assertTrue(self.tab.default_button.instate(["!disabled"]))
        for slot in range(6):
            self.tab.slot_seconds[slot].set(str(slot + 7))
            self.tab.slot_time_buttons[slot].invoke()
        cycle = read_picture_cycle(self.tab.blob, full_screen_pictures(self.tab.blob))
        self.assertEqual([screen.seconds for screen in cycle], list(range(7, 13)))
        self.assertEqual(self.tab.base_blob, blob)
        self.assertIn("Nothing has been sent", self.tab.status.get())
        self.tab._discard()
        self.assertEqual(self.tab.blob, blob)
        self.assertEqual(self.tab.slot_seconds[0].get(), "5")

    def test_invalid_time_reports_error_without_changing_candidate(self):
        blob = self.load_preset()
        for value in ("0", "-1", "3.5", "invalid", "16777216"):
            self.tab.slot_seconds[0].set(value)
            with patch("harmony_one.slideshow_tab.messagebox.showerror") as error:
                self.tab.slot_time_buttons[0].invoke()
            error.assert_called_once()
            self.assertEqual(self.tab.blob, blob)

    def test_return_to_default_uses_original_bundled_artwork_and_four_slots(self):
        from harmony_one.pictures import decode_picture, encode_png
        from harmony_one.slideshow import full_screen_pictures
        from harmony_one.slideshow_library import bundled_default_slideshow_images

        for name in ("older", "newer"):
            with self.subTest(preset=name):
                blob = self.load_preset(name)
                with patch("harmony_one.slideshow_tab.messagebox.askyesno", return_value=True), patch(
                    "harmony_one.slideshow_tab.open_harmony_one"
                ) as usb:
                    self.tab.default_button.invoke()
                usb.assert_not_called()
                candidate = self.tab.blob
                cycle = read_picture_cycle(candidate, full_screen_pictures(candidate))
                self.assertEqual(len(cycle), 4)
                self.assertEqual([screen.seconds for screen in cycle], [3] * 4)
                self.assertEqual(
                    [encode_png(decode_picture(candidate, screen.picture)) for screen in cycle],
                    [path.read_bytes() for path in bundled_default_slideshow_images()],
                )
                self.assertEqual(self.tab.base_blob, blob)
                self.assertEqual(self.tab.listbox.size(), 6)
                self.assertEqual([value.get() for value in self.tab.slot_seconds], ["3"] * 6)
                self.assertTrue(all(widget.instate(["disabled"]) for widget in self.tab.slot_time_inputs[4:]))
                self.assertTrue(all(widget.instate(["disabled"]) for widget in self.tab.slot_browse_buttons[4:]))
                self.tab._discard()
                self.assertEqual(self.tab.blob, blob)

    def test_default_reset_can_be_cancelled_and_failures_are_atomic(self):
        blob = self.load_preset()
        with patch("harmony_one.slideshow_tab.messagebox.askyesno", return_value=False), patch(
            "harmony_one.slideshow_tab.bundled_default_slideshow_images"
        ) as images:
            self.tab.default_button.invoke()
        images.assert_not_called()
        self.assertEqual(self.tab.blob, blob)
        with patch("harmony_one.slideshow_tab.messagebox.askyesno", return_value=True), patch(
            "harmony_one.slideshow_tab.bundled_default_slideshow_images", return_value=(Path("missing.png"),)
        ), patch("harmony_one.slideshow_tab.messagebox.showerror") as error:
            self.tab.default_button.invoke()
        error.assert_called_once()
        self.assertEqual(self.tab.blob, blob)
        with patch("harmony_one.slideshow_tab.messagebox.askyesno", return_value=True), patch(
            "harmony_one.slideshow_tab.restore_default_cycle", side_effect=SlideshowCycleError("shared timer")
        ), patch("harmony_one.slideshow_tab.messagebox.showerror") as error:
            self.tab.default_button.invoke()
        error.assert_called_once()
        self.assertEqual(self.tab.blob, blob)

    def test_edit_controls_and_handlers_do_not_mutate_during_write(self):
        blob = self.load_preset()
        self.tab.writing = True
        self.tab._update_edit_controls()
        self.assertTrue(self.tab.default_button.instate(["disabled"]))
        self.assertTrue(all(widget.instate(["disabled"]) for widget in self.tab.slot_time_buttons))
        with patch("harmony_one.slideshow_tab.set_cycle_seconds") as timing, patch(
            "harmony_one.slideshow_tab.messagebox.askyesno"
        ) as ask:
            self.tab._set_slot_seconds(0)
            self.tab._restore_defaults()
            self.tab._browse_slot(0)
            self.tab._discard()
        timing.assert_not_called()
        ask.assert_not_called()
        self.assertEqual(self.tab.blob, blob)

    def test_timing_candidate_still_requires_write_acknowledgement_and_confirmation(self):
        self.load_preset()
        self.tab.slot_seconds[0].set("8")
        self.tab.slot_time_buttons[0].invoke()
        with patch("harmony_one.slideshow_tab.messagebox.showwarning") as warning, patch(
            "harmony_one.slideshow_tab.messagebox.askyesno", return_value=False
        ) as confirm, patch("harmony_one.slideshow_tab.open_harmony_one") as usb:
            self.tab._write()
            warning.assert_called_once()
            confirm.assert_not_called()
            self.tab.allow_writes.set(True)
            self.tab._write()
            confirm.assert_called_once()
        usb.assert_not_called()
        self.assertFalse(self.tab.writing)

    def test_open_slideshow_folder_creates_and_opens_user_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "slideshow"
            with patch(
                "harmony_one.slideshow_tab.slideshow_library_directory",
                return_value=directory,
            ), patch("harmony_one.slideshow_tab.os.startfile") as startfile:
                self.tab._open_slideshow_folder()
                self.assertTrue(directory.is_dir())

        startfile.assert_called_once_with(str(directory))

    def test_unsupported_cycle_does_not_expose_unrelated_pictures(self):
        with patch("harmony_one.slideshow_tab.full_screen_pictures", return_value=self.pictures), patch(
            "harmony_one.slideshow_tab.read_picture_cycle", side_effect=SlideshowCycleError("unsupported")
        ), patch.object(self.tab, "_selected"):
            self.tab.load(b"config")
        self.assertEqual(self.tab.entries, [])
        self.assertEqual(self.tab.listbox.size(), 6)
        self.assertTrue(
            all(button.instate(["disabled"]) for button in self.tab.slot_browse_buttons)
        )
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
                self.assertEqual(self.tab.listbox.size(), 6)
                self.assertIn("Maximum: 4", self.tab.cycle_info.get())
                self.assertEqual(self.tab.blob, blob)
                self.assertTrue(all(
                    button.instate(["!disabled"]) for button in self.tab.slot_browse_buttons[:4]
                ))
                self.assertTrue(all(
                    button.instate(["disabled"]) for button in self.tab.slot_browse_buttons[4:]
                ))

    def test_six_image_cycle_enables_all_six_slots(self):
        pictures = [(i, Picture(100 + i, 0, 176, 220, 77445)) for i in range(6)]
        cycle = [
            CycleScreen(i, i, picture, i, 5, (i + 1) % 6, i == 5)
            for i, picture in pictures
        ]
        with patch("harmony_one.slideshow_tab.full_screen_pictures", return_value=pictures), patch(
            "harmony_one.slideshow_tab.read_picture_cycle", return_value=cycle
        ), patch.object(self.tab, "_selected"):
            self.tab.load(b"six-image-config")
        self.assertEqual(self.tab.listbox.size(), 6)
        self.assertEqual(len(self.tab.entries), 6)
        self.assertTrue(all(button.instate(["!disabled"]) for button in self.tab.slot_browse_buttons))
        with patch.object(self.tab, "_browse_slot") as browse:
            self.tab.slot_browse_buttons[5].invoke()
        browse.assert_called_once_with(5)

    def test_sixth_slot_replacement_preserves_first_five_and_can_be_discarded(self):
        from harmony_one.harmony_usb import trailer_checksum
        from harmony_one.slideshow import encode_raw_picture

        encoded = encode_raw_picture([[(0, 0, 0)] * 176 for _ in range(220)])
        blob = encoded * 6 + b"\x00\x00PTYY"
        pictures = [
            (i, Picture(i * len(encoded), 0, 176, 220, len(encoded))) for i in range(6)
        ]
        cycle = [
            CycleScreen(i, i, picture, i, 5, (i + 1) % 6, i == 5)
            for i, picture in pictures
        ]
        source = SimpleNamespace(
            width=lambda: 1, height=lambda: 1, get=lambda _x, _y: (255, 0, 0),
        )
        with patch("harmony_one.slideshow_tab.full_screen_pictures", return_value=pictures), patch(
            "harmony_one.slideshow_tab.read_picture_cycle", return_value=cycle
        ), patch.object(self.tab, "_selected"):
            self.tab.load(blob)
            with patch("harmony_one.slideshow_tab.messagebox.askyesno", return_value=True), patch(
                "harmony_one.slideshow_tab.filedialog.askopenfilename", return_value="image.png"
            ), patch("harmony_one.slideshow_tab.tk.PhotoImage", return_value=source):
                self.tab.slot_browse_buttons[5].invoke()
            self.assertEqual(self.tab.blob[:5 * len(encoded)], blob[:5 * len(encoded)])
            self.assertNotEqual(self.tab.blob[5 * len(encoded):-6], encoded)
            self.assertEqual(
                int.from_bytes(self.tab.blob[-6:-4], "little"), trailer_checksum(self.tab.blob)
            )
            self.assertEqual(self.tab.base_blob, blob)
            self.tab._discard()
            self.assertEqual(self.tab.blob, blob)

    def test_empty_slots_have_no_preview_or_export_and_cannot_be_filled(self):
        cycle = [
            CycleScreen(i, i, picture, i, 5, (i + 1) % 3, i == 2)
            for i, picture in self.pictures
        ]
        with patch("harmony_one.slideshow_tab.full_screen_pictures", return_value=self.pictures), patch(
            "harmony_one.slideshow_tab.read_picture_cycle", return_value=cycle
        ), patch.object(self.tab, "_selected"):
            self.tab.load(b"config")
        for index in (4, 5):
            self.tab.listbox.selection_clear(0, "end")
            self.tab.listbox.selection_set(index)
            self.tab._selected()
            self.assertIsNone(self.tab._current())
            self.assertIsNone(self.tab.preview)
            self.assertFalse(self.tab.canvas.cget("image"))
            with patch("harmony_one.slideshow_tab.filedialog.asksaveasfilename") as save:
                self.tab._export()
            save.assert_not_called()
            with patch("harmony_one.slideshow_tab.messagebox.showinfo") as info, patch(
                "harmony_one.slideshow_tab.filedialog.askopenfilename"
            ) as browse:
                self.tab._browse_slot(index)
            info.assert_called_once()
            browse.assert_not_called()
            self.assertEqual(self.tab.blob, b"config")

    def test_default_empty_slots_have_no_preview(self):
        for index in (4, 5):
            self.tab.listbox.selection_clear(0, "end")
            self.tab.listbox.selection_set(index)
            self.tab._selected()
            self.assertIsNone(self.tab.preview)
            self.assertIsNone(self.tab._current())
            self.assertFalse(self.tab.canvas.cget("image"))

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
