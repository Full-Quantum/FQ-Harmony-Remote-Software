import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from harmony_one.app import HarmonyOfflineApp
from harmony_one.harmony_project import load_project, save_project


def device(device_id, name, commands):
    return {
        "id": device_id,
        "name": name,
        "brand": "",
        "category": "",
        "commands": [
            {"name": command, "protocol": "NEC", "address": "01", "command": str(index)}
            for index, command in enumerate(commands)
        ],
    }


class ActivitySettingsUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with patch("harmony_one.app.portable_directory", return_value=Path(self.directory.name)):
            self.app = HarmonyOfflineApp(self.root)
        self.app.project["devices"] = [
            device("tv", "Samsung TV", ["PowerToggle", "InputHDMI1"]),
            device("av", "Sony AV", ["PowerToggle", "InputMediaBox"]),
        ]
        self.activity = {"id": "a", "name": "Watch TV", "steps": []}
        self.app.project["activities"] = [self.activity]
        self.app._refresh_devices()
        self.app._refresh_activities()
        self.app._open_activity_editor("a")
        self.root.update()

    def add_action(self, device_index, command):
        selected = self.app.project["devices"][device_index]
        self.app.step_device.set(self.app._device_label(selected))
        self.app._step_device_changed()
        self.app.step_command.set(command)
        self.app._add_step()

    def test_select_devices_roles_and_persist_ordered_actions(self):
        self.app.activity_device_list.selection_set(0)
        self.app._activity_participation_changed()
        tv_label = self.app._activity_device_label(self.app.project["devices"][0])
        self.assertIn(tv_label, self.app.volume_device_box["values"])
        self.app.volume_device.set(tv_label)
        self.app._save_activity_setup()

        self.add_action(0, "PowerToggle")
        self.add_action(1, "PowerToggle")
        self.add_action(0, "InputHDMI1")
        self.add_action(1, "InputMediaBox")
        self.assertEqual(self.activity["participating_device_ids"], ["tv", "av"])
        self.assertEqual(self.activity["volume_device_id"], "tv")
        self.app.step_tree.selection_set("3")
        self.app._move_step(-1)
        rows = [self.app.step_tree.item(item, "values") for item in self.app.step_tree.get_children()]
        self.assertEqual([row[0] for row in rows], ["1", "2", "3", "4"])
        self.assertEqual([row[2] for row in rows], [
            "PowerToggle", "PowerToggle", "InputMediaBox", "InputHDMI1",
        ])
        path = Path(self.directory.name) / "project.json"
        save_project(path, self.app.project)
        saved = load_project(path)["activities"][0]
        self.assertEqual(saved["participating_device_ids"], ["tv", "av"])
        self.assertEqual(saved["volume_device_id"], "tv")
        self.assertEqual([step["command_name"] for step in saved["steps"]], [
            "PowerToggle", "PowerToggle", "InputMediaBox", "InputHDMI1",
        ])

    def test_imported_states_and_delay_are_shown_in_order(self):
        self.activity["steps"] = [
            {"type": "state", "device_id": "tv", "label": "Power on"},
            {"type": "delay", "ms": 500},
            {"type": "state", "device_id": "av", "label": "Input setting 1"},
        ]
        self.app._show_activity(self.activity)
        rows = [self.app.step_tree.item(item, "values") for item in self.app.step_tree.get_children()]
        self.assertEqual([row[:3] for row in rows], [
            ("1", "Samsung TV", "Power on"),
            ("2", "", "Wait 500 ms"),
            ("3", "Sony AV", "Input setting 1"),
        ])
        self.assertEqual(self.app._selected_participating_device_ids(), ["tv", "av"])

    def test_input_notes_and_deselection_are_saved(self):
        self.app.activity_device_list.selection_set(0, 1)
        self.app._activity_participation_changed()
        self.app.activity_input_vars["tv"].set("HDMI 1")
        self.app._save_activity_setup()
        self.app.activity_device_list.selection_clear(1)
        self.app._activity_participation_changed()
        self.app._close_activity_editor()
        self.assertEqual(self.activity["participating_device_ids"], ["tv"])
        self.assertEqual(self.activity["inputs"], {"tv": "HDMI 1"})
        self.assertEqual(self.activity["steps"], [])

    def test_device_used_by_action_cannot_be_deselected(self):
        self.add_action(0, "PowerToggle")
        self.app.activity_device_list.selection_clear(0)
        with patch("harmony_one.app.messagebox.showinfo") as info:
            self.app._activity_participation_changed()
        info.assert_called_once()
        self.assertEqual(self.app._selected_participating_device_ids(), ["tv"])
        self.assertEqual(self.activity["participating_device_ids"], ["tv"])

    def test_pending_input_note_survives_reordering(self):
        self.add_action(0, "PowerToggle")
        self.add_action(0, "InputHDMI1")
        self.app.activity_input_vars["tv"].set("HDMI 1")
        self.app.step_tree.selection_set("1")
        self.app._move_step(-1)
        self.assertEqual(self.activity["inputs"], {"tv": "HDMI 1"})
        self.assertEqual(self.app.activity_input_vars["tv"].get(), "HDMI 1")

    def test_opening_legacy_settings_preserves_roles_and_does_not_modify_project(self):
        self.activity.update(volume_device_id="av", inputs={"tv": "HDMI 1"})
        self.app._show_activity(self.activity)
        self.assertEqual(self.app._selected_participating_device_ids(), ["tv", "av"])
        self.assertEqual(
            self.app.volume_device.get(),
            self.app._activity_device_label(self.app.project["devices"][1]),
        )
        self.app._close_activity_editor()
        self.assertNotIn("participating_device_ids", self.activity)
        self.assertEqual(self.activity["volume_device_id"], "av")
        self.assertEqual(self.activity["inputs"], {"tv": "HDMI 1"})

    def test_icon_picker_updates_only_selected_activity(self):
        with patch("harmony_one.app.ActivityIconDialog") as dialog:
            self.app._edit_activity_icon()
        apply = dialog.call_args.args[2]
        icon = {"kind": "builtin", "name": "tv"}
        apply(icon)
        self.assertEqual(self.activity["icon"], icon)
        self.assertTrue(self.app.dirty)
        self.assertIsNotNone(self.app.activity_icon_image)
        apply(None)
        self.assertNotIn("icon", self.activity)


if __name__ == "__main__":
    unittest.main()
