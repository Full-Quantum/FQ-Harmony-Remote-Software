import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from harmony_one.clock_tab import LOCAL_TIMEZONE, ClockTab


class ClockTabTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        app = SimpleNamespace(
            root=self.root,
            _backup_running=False,
            slideshow=SimpleNamespace(writing=False),
            _handle_missing_remote=Mock(),
        )
        self.tab = ClockTab(tk.Frame(self.root), app)

    def test_clock_write_requires_explicit_acknowledgment(self):
        self.assertEqual(str(self.tab.set_button.cget("state")), "disabled")
        self.tab.allow_writes.set(True)
        self.tab._update_button()
        self.assertEqual(str(self.tab.set_button.cget("state")), "normal")
        self.tab.writing = True
        self.tab._update_button()
        self.assertEqual(str(self.tab.set_button.cget("state")), "disabled")

    def test_displays_computer_local_time_by_default(self):
        self.assertTrue(self.tab.pc_time.get())
        self.assertIn("(", self.tab.pc_time.get())
        self.assertTrue(self.tab.timezone.get().startswith(f"{LOCAL_TIMEZONE} (UTC"))
        labels = [
            str(widget.cget("text"))
            for widget in self.tab.frame.winfo_children()
            if "text" in widget.keys()
        ]
        self.assertIn("Remote display clock (top right)", labels)
        self.assertEqual(
            self.tab.set_button.cget("text"),
            "Set top-right remote clock to selected time zone",
        )

    def test_iana_timezone_uses_current_wall_time_and_shows_its_offset(self):
        timezone_name = "Asia/Tokyo"
        self.tab.timezone.set(timezone_name)
        self.tab._refresh_pc_time()

        wall_time, zone_label = self.tab._selected_time()
        expected = datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)
        self.assertLessEqual(abs((wall_time - expected).total_seconds()), 1)
        self.assertTrue(wall_time.tzinfo is None)
        self.assertIn(timezone_name, zone_label)
        self.assertIn("UTC+", zone_label)
        self.assertIn("Tokyo", self.tab.pc_time.get())

    def test_timezone_options_include_current_utc_offsets_and_preserve_zone_id(self):
        timezone_name = "America/Los_Angeles"
        option = next(
            label
            for label, name in self.tab._timezone_options.items()
            if name == timezone_name
        )
        current_offset = self.tab._utc_offset(datetime.now(ZoneInfo(timezone_name)).utcoffset())
        self.assertEqual(option, f"{timezone_name} ({current_offset})")
        self.assertIn(option, self.tab.timezones)

        self.tab.timezone.set(option)
        wall_time, zone_label = self.tab._selected_time()
        expected = datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)
        self.assertLessEqual(abs((wall_time - expected).total_seconds()), 1)
        self.assertIn(timezone_name, zone_label)

    def test_invalid_timezone_disables_clock_write(self):
        self.tab.allow_writes.set(True)
        self.tab.timezone.set("Not/A_Timezone")
        self.tab._refresh_pc_time()

        self.assertEqual(str(self.tab.set_button.cget("state")), "disabled")
        self.assertIn("Unknown time zone", self.tab.timezone_error.get())

    def test_offline_candidate_uses_loaded_backup_and_does_not_write_remote(self):
        source = Path("source.EZHex")
        destination = Path("source-clock-candidate.EZHex")
        stamp = datetime(2026, 11, 10, 14, 30)
        self.tab.load(b"baseline", source)
        self.assertEqual(str(self.tab.offline_candidate_button.cget("state")), "normal")
        with patch(
            "harmony_one.clock_tab.filedialog.asksaveasfilename",
            return_value=str(destination),
        ), patch.object(self.tab, "_selected_time", return_value=(stamp, "test zone")), patch(
            "harmony_one.clock_tab.prepare_clock_candidate",
            return_value=SimpleNamespace(image=b"candidate"),
        ) as prepare, patch(
            "harmony_one.clock_tab.validate_source_save"
        ) as validate, patch(
            "harmony_one.clock_tab._save_validated_backup"
        ) as save:
            self.tab._save_offline_candidate()

        prepare.assert_called_once_with(b"baseline", stamp)
        validate.assert_called_once_with(b"candidate", "Clock")
        save.assert_called_once_with(destination, b"candidate", overwrite=False)
        self.assertIn(str(destination), self.tab.status.get())
        self.assertEqual(self.tab.source_blob, b"baseline")

    def test_offline_candidate_cannot_overwrite_loaded_backup(self):
        source = Path("source.EZHex")
        self.tab.load(b"baseline", source)
        with patch(
            "harmony_one.clock_tab.filedialog.asksaveasfilename",
            return_value=str(source),
        ), patch.object(
            self.tab,
            "_selected_time",
            return_value=(datetime(2026, 11, 10, 14, 30), "test zone"),
        ), patch(
            "harmony_one.clock_tab.prepare_clock_candidate",
            return_value=SimpleNamespace(image=b"candidate"),
        ), patch("harmony_one.clock_tab.messagebox.showwarning") as warning, patch(
            "harmony_one.clock_tab._save_validated_backup"
        ) as save:
            self.tab._save_offline_candidate()

        warning.assert_called_once()
        save.assert_not_called()

    def test_missing_hid_interface_uses_usb_repair_flow(self):
        error = "expected exactly one Harmony One, found 0"
        self.tab.events.put(("error", error))
        with patch("harmony_one.clock_tab.messagebox.showerror") as showerror:
            self.tab._poll()
        self.tab.app._handle_missing_remote.assert_called_once()
        self.assertIn(error, self.tab.app._handle_missing_remote.call_args.args[0])
        showerror.assert_not_called()


if __name__ == "__main__":
    unittest.main()
