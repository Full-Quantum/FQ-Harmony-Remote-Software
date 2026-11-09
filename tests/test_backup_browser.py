import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.backup_browser import BackupBrowserTab, _activity_lines, _device_lines

DEVICE = {
    "object_type": "device",
    "object_id": "abcd-device-000",
    "name": "TV",
    "source_ir_group": 1,
    "source_state_variables": {"Power": 4},
    "ir_records": [
        {
            "index": 0,
            "carrier_hz": 38000,
            "record_class": 2,
            "pointer_groups": 2,
            "first_pointer_group_blocks_us": {"once": [100, -200], "held": None, "tail": None},
        }
    ],
    "limitations": ["Device identity is not decoded."],
}

ACTIVITY = {
    "object_type": "activity",
    "object_id": "abcd-activity-000",
    "name": "Watch TV",
    "source_activity_number": 0,
    "source_key_map": 7,
    "source_mode": 9,
    "devices": [{"name": "TV", "candidate_device_object_ids": ["abcd-device-000"]}],
    "decoded_state_steps": [
        {"kind": "power", "device": "TV", "value": 1, "group": None, "record": None}
    ],
    "key_map_bindings": [
        {"tag": 0x81, "operand": 0, "opcode": 0x7F,
         "scan_code": None, "direct_action_list_ir_references": [{"source_ir_group": 1}]}
    ],
    "source_action_lists": {"1": []},
    "limitations": ["Steps are partially decoded."],
}

BUNDLE = {
    "format": "fq-harmony-object",
    "schema_version": 1,
    "source": {"backup_sha256": "ab" * 32, "architecture": 12, "skin": 54},
    "transfer_ready": False,
    "objects": [DEVICE, ACTIVITY],
}


class FormattingTests(unittest.TestCase):
    def test_device_lines(self):
        text = "\n".join(_device_lines(DEVICE))
        self.assertIn("Device: TV", text)
        self.assertIn("IR group: 1", text)
        self.assertIn("Power: variable 4", text)
        self.assertIn("carrier 38000 Hz", text)
        self.assertIn("once 2 edges", text)

    def test_activity_lines(self):
        text = "\n".join(_activity_lines(ACTIVITY))
        self.assertIn("Activity: Watch TV", text)
        self.assertIn("Key map: 7   Mode: 9", text)
        self.assertIn("power: TV = 1", text)
        self.assertIn("Button bindings: 1 (1 with direct IR sends)", text)


class BackupBrowserTabTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:  # pragma: no cover - headless environment
            self.skipTest(f"Tk is unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.exports = []
        app = SimpleNamespace(
            _start_backup=lambda **kwargs: None,
            backup_initialdir=lambda: Path("."),
            export_objects_with_ids=lambda blob, ids: self.exports.append((blob, ids)),
        )
        self.tab = BackupBrowserTab(ttk.Frame(self.root), app)

    def test_load_populates_tree_and_summary(self):
        with patch("harmony_one.backup_browser.build_remote_objects", return_value=BUNDLE):
            self.tab.load(b"blob", Path("backup.EZHex"))
        roots = self.tab.tree.get_children()
        self.assertEqual(len(roots), 2)
        self.assertEqual(self.tab.tree.item(roots[0], "text"), "Devices (1)")
        self.assertEqual(self.tab.tree.item(roots[1], "text"), "Activities (1)")
        device_node = self.tab.tree.get_children(roots[0])[0]
        self.assertEqual(self.tab.tree.item(device_node, "text"), "TV")
        self.assertIn("1 devices, 1 activities", self.tab.summary.get())

    def test_selection_shows_details_and_export_uses_object_id(self):
        with patch("harmony_one.backup_browser.build_remote_objects", return_value=BUNDLE):
            self.tab.load(b"blob", None)
        roots = self.tab.tree.get_children()
        activity_node = self.tab.tree.get_children(roots[1])[0]
        self.tab.tree.selection_set(activity_node)
        self.tab._selected()
        text = self.tab.details.get("1.0", tk.END)
        self.assertIn("Activity: Watch TV", text)
        self.assertIn("Known decoding limitations:", text)
        self.tab._export_selected()
        self.assertEqual(self.exports, [(b"blob", ["abcd-activity-000"])])

    def test_load_error_is_reported_not_raised(self):
        with patch(
            "harmony_one.backup_browser.build_remote_objects",
            side_effect=ValueError("bad backup"),
        ), patch("harmony_one.backup_browser.messagebox.showerror") as error:
            self.tab.load(b"junk", None)
        error.assert_called_once()
        self.assertIsNone(self.tab.blob)


if __name__ == "__main__":
    unittest.main()
