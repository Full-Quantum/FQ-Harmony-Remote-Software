import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.backup_browser import BackupBrowserTab, _activity_lines, _device_lines
from harmony_one.screen_styles import MenuIconOccurrence

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
        self.previews = []
        self.slideshow_loads = []
        self.assistant_loads = []
        app = SimpleNamespace(
            _start_backup=lambda **kwargs: None,
            backup_initialdir=lambda: Path("."),
            export_objects_with_ids=lambda blob, ids: self.exports.append((blob, ids)),
            _start_editing_backup_copy=lambda blob, path: self.exports.append((blob, path)),
            _load_home_preview=lambda blob: self.previews.append(blob),
            slideshow=SimpleNamespace(load=lambda blob: self.slideshow_loads.append(blob)),
            remote_assistant=SimpleNamespace(
                load=lambda blob, path: self.assistant_loads.append((blob, path))
            ),
        )
        self.tab = BackupBrowserTab(ttk.Frame(self.root), app)

    def test_load_populates_tree_and_summary(self):
        with patch("harmony_one.backup_browser.build_remote_objects", return_value=BUNDLE):
            self.tab.load(b"blob", Path("backup.EZHex"))
        roots = self.tab.tree.get_children()
        self.assertEqual(len(roots), 2)
        self.assertEqual(self.tab.tree.item(roots[0], "text"), "Activities (1)")
        self.assertEqual(self.tab.tree.item(roots[1], "text"), "Devices (1)")
        device_node = self.tab.tree.get_children(roots[1])[0]
        self.assertEqual(self.tab.tree.item(device_node, "text"), "TV")
        self.assertIn("1 devices, 1 activities", self.tab.summary.get())
        self.assertEqual(str(self.tab.import_all_button.cget("state")), "normal")
        self.assertEqual(self.previews, [b"blob"])
        self.assertEqual(self.slideshow_loads, [b"blob"])
        self.assertEqual(self.assistant_loads, [(b"blob", Path("backup.EZHex"))])

    def test_import_all_decoded_items_starts_copy_from_loaded_backup(self):
        source = Path("backup.EZHex")
        with patch("harmony_one.backup_browser.build_remote_objects", return_value=BUNDLE):
            self.tab.load(b"backup", source)

        self.tab._import_all_to_remote_data()

        self.assertEqual(self.exports, [(b"backup", source)])

    def test_import_all_requires_saving_an_edited_icon_candidate_first(self):
        self.tab.blob = b"edited"
        self.tab.path = Path("backup.EZHex")
        self.tab.edited_icon_candidate = True
        self.tab._update_icon_buttons()
        with patch("harmony_one.backup_browser.messagebox.showwarning") as warning:
            self.tab._import_all_to_remote_data()

        warning.assert_called_once()
        self.assertEqual(self.exports, [])

    def test_selection_shows_details_and_export_uses_object_id(self):
        with patch("harmony_one.backup_browser.build_remote_objects", return_value=BUNDLE):
            self.tab.load(b"blob", None)
        roots = self.tab.tree.get_children()
        activity_node = self.tab.tree.get_children(roots[0])[0]
        self.tab.tree.selection_set(activity_node)
        self.tab._selected()
        text = self.tab.details.get("1.0", tk.END)
        self.assertIn("Activity: Watch TV", text)
        self.assertIn("Known decoding limitations:", text)
        self.tab._export_selected()
        self.assertEqual(self.exports, [(b"blob", ["abcd-activity-000"])])

    def test_selected_device_opens_per_occurrence_icon_picker(self):
        occurrence = MenuIconOccurrence(
            "devices", 4, 1, 38, "TV", b"picture", b"button"
        )
        with patch("harmony_one.backup_browser.build_remote_objects", return_value=BUNDLE):
            self.tab.load(b"blob", Path("backup.EZHex"))
        device_node = self.tab.tree.get_children(self.tab.tree.get_children()[1])[0]
        self.tab.tree.selection_set(device_node)
        with patch(
            "harmony_one.backup_browser.list_menu_icon_occurrences",
            return_value=(occurrence,),
        ), patch(
            "harmony_one.backup_browser.list_menu_icon_choices",
            return_value=(b"picture", b"other"),
        ), patch("harmony_one.backup_browser.RemoteMenuIconDialog") as dialog:
            self.tab._edit_selected_menu_icon()

        dialog.assert_called_once()
        self.assertEqual(dialog.call_args.args[1], (occurrence,))
        self.assertEqual(dialog.call_args.args[2], (b"picture", b"other"))

    def test_device_card_icon_picker_hides_occurrence_selector_and_targets_all_rows(self):
        occurrence = MenuIconOccurrence(
            "devices", 4, 1, 38, "TV", b"picture", b"button"
        )
        self.tab.blob = b"blob"
        with patch(
            "harmony_one.backup_browser.list_menu_icon_occurrences",
            return_value=(occurrence,),
        ), patch(
            "harmony_one.backup_browser.list_menu_icon_choices",
            return_value=(b"picture",),
        ), patch("harmony_one.backup_browser.RemoteMenuIconDialog") as dialog:
            self.tab.edit_menu_icon_for_item(
                "device", "TV", apply_to_all_occurrences=True
            )

        self.assertIs(dialog.call_args.args[3].__self__, self.tab)
        self.assertEqual(
            dialog.call_args.args[3].__func__,
            self.tab._apply_menu_icons_for_item.__func__,
        )
        self.assertTrue(dialog.call_args.kwargs["apply_to_all_occurrences"])

    def test_activity_card_uses_backup_menu_rows_when_decoded_name_has_no_match(self):
        first = MenuIconOccurrence(
            "activities", 50, 0, 38, "Watch TV", b"picture1", b"button"
        )
        second = MenuIconOccurrence(
            "activities", 50, 1, 38, "Watch a DVD", b"picture2", b"button"
        )
        self.tab.blob = b"blob"
        with patch(
            "harmony_one.backup_browser.list_menu_icon_occurrences",
            return_value=(first, second),
        ), patch(
            "harmony_one.backup_browser.list_menu_icon_choices",
            return_value=(b"picture1",),
        ), patch("harmony_one.backup_browser.RemoteMenuIconDialog") as dialog:
            self.tab.edit_menu_icon_for_item(
                "activity",
                "HDMI Switcher Port 2",
                apply_to_all_occurrences=True,
                fallback_to_role_occurrences=True,
            )

        dialog.assert_called_once()
        self.assertEqual(dialog.call_args.args[1], (first, second))
        self.assertFalse(dialog.call_args.kwargs["apply_to_all_occurrences"])
        self.assertIn("does not match a menu label", dialog.call_args.kwargs["instructions"])
        self.assertTrue(dialog.call_args.kwargs["show_occurrence_list"])
        self.assertEqual(
            dialog.call_args.args[3].__func__,
            self.tab._apply_menu_icon.__func__,
        )

    def test_activity_card_matching_name_hides_row_selector_and_targets_all_matches(self):
        occurrence = MenuIconOccurrence(
            "activities", 50, 0, 38, "Watch TV", b"picture", b"button"
        )
        self.tab.blob = b"blob"
        with patch(
            "harmony_one.backup_browser.list_menu_icon_occurrences",
            return_value=(occurrence,),
        ), patch(
            "harmony_one.backup_browser.list_menu_icon_choices",
            return_value=(b"picture",),
        ), patch("harmony_one.backup_browser.RemoteMenuIconDialog") as dialog:
            self.tab.edit_menu_icon_for_item(
                "activity", "Watch TV", apply_to_all_occurrences=True
            )

        self.assertTrue(dialog.call_args.kwargs["apply_to_all_occurrences"])
        self.assertFalse(dialog.call_args.kwargs["show_occurrence_list"])
        self.assertEqual(
            dialog.call_args.args[3].__func__,
            self.tab._apply_menu_icons_for_item.__func__,
        )

    def test_applying_card_icon_updates_each_matching_occurrence(self):
        first = MenuIconOccurrence("devices", 4, 1, 38, "TV", b"old1", b"button")
        second = MenuIconOccurrence("devices", 4, 2, 38, "TV", b"old2", b"button")
        first_updated = MenuIconOccurrence(
            "devices", 4, 1, 38, "TV", b"updated1", b"button"
        )
        second_updated = MenuIconOccurrence(
            "devices", 4, 2, 38, "TV", b"updated2", b"button"
        )
        self.tab.blob = b"original"
        with patch(
            "harmony_one.backup_browser.list_menu_icon_occurrences",
            side_effect=[
                (first, second),
                (first_updated, second_updated),
            ],
        ), patch(
            "harmony_one.backup_browser.create_menu_icon_candidate",
            side_effect=[
                SimpleNamespace(candidate=b"after first"),
                SimpleNamespace(candidate=b"after both"),
            ],
        ) as create:
            applied = self.tab._apply_menu_icons_for_item(first, b"new")

        self.assertTrue(applied)
        self.assertEqual(self.tab.blob, b"after both")
        self.assertTrue(self.tab.edited_icon_candidate)
        self.assertEqual(
            [call.args[1] for call in create.call_args_list],
            [first, second_updated],
        )

    def test_icon_edit_updates_only_the_offline_candidate(self):
        occurrence = MenuIconOccurrence(
            "activities", 4, 1, 92, "Watch TV", b"old", b"button"
        )
        self.tab.blob = b"original"
        result = SimpleNamespace(candidate=b"edited", occurrence=occurrence)
        with patch(
            "harmony_one.backup_browser.create_menu_icon_candidate",
            return_value=result,
        ):
            applied = self.tab._apply_menu_icon(occurrence, b"new")

        self.assertTrue(applied)
        self.assertEqual(self.tab.blob, b"edited")
        self.assertTrue(self.tab.edited_icon_candidate)
        self.assertIn("Save as a new .EZHex", self.tab.summary.get())
        self.assertEqual(str(self.tab.save_icon_candidate_button.cget("state")), "normal")

    def test_save_edited_backup_uses_new_default_name_and_keeps_candidate(self):
        self.tab.blob = b"edited"
        self.tab.path = Path("original.EZHex")
        self.tab.edited_icon_candidate = True
        destination = Path("original-icons.EZHex")
        with patch(
            "harmony_one.backup_browser.filedialog.asksaveasfilename",
            return_value=str(destination),
        ) as dialog, patch(
            "harmony_one.backup_browser.validate_source_save"
        ), patch("harmony_one.backup_browser._save_validated_backup") as save:
            self.tab._save_edited_backup()

        self.assertEqual(dialog.call_args.kwargs["initialfile"], "original-icons.EZHex")
        save.assert_called_once_with(destination, b"edited", overwrite=True)
        self.assertEqual(self.tab.path, destination)
        self.assertFalse(self.tab.edited_icon_candidate)

    def test_save_edited_backup_cannot_overwrite_loaded_backup(self):
        source = Path("original.EZHex")
        self.tab.blob = b"edited"
        self.tab.path = source
        self.tab.edited_icon_candidate = True
        with patch(
            "harmony_one.backup_browser.filedialog.asksaveasfilename",
            return_value=str(source),
        ), patch("harmony_one.backup_browser.messagebox.showwarning") as warning, patch(
            "harmony_one.backup_browser._save_validated_backup"
        ) as save:
            self.tab._save_edited_backup()

        warning.assert_called_once()
        save.assert_not_called()
        self.assertEqual(self.tab.path, source)
        self.assertTrue(self.tab.edited_icon_candidate)

    def test_load_error_is_reported_not_raised(self):
        with patch(
            "harmony_one.backup_browser.build_remote_objects",
            side_effect=ValueError("bad backup"),
        ), patch("harmony_one.backup_browser.messagebox.showerror") as error:
            self.tab.load(b"junk", None)
        error.assert_called_once()
        self.assertIsNone(self.tab.blob)

    def test_loading_another_backup_can_be_cancelled_to_keep_unsaved_icons(self):
        self.tab.blob = b"edited candidate"
        self.tab.edited_icon_candidate = True
        with patch("harmony_one.backup_browser.messagebox.askyesno", return_value=False) as ask, patch(
            "harmony_one.backup_browser.build_remote_objects"
        ) as build:
            self.tab.load(b"another backup", Path("other.EZHex"))

        ask.assert_called_once()
        build.assert_not_called()
        self.assertEqual(self.tab.blob, b"edited candidate")
        self.assertTrue(self.tab.edited_icon_candidate)


if __name__ == "__main__":
    unittest.main()
