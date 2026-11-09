import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.app import HarmonyOfflineApp


class ActivityCandidateUITests(unittest.TestCase):
    def setUp(self):
        project = {
            "devices": [],
            "activities": [{"id": "a", "name": "Watch VT", "steps": []}],
        }
        self.app = SimpleNamespace(
            root=None, remote_blob=b"source", remote_backup_path=Path("backup.EZHex"),
            remote_project=project, project=copy.deepcopy(project),
        )
        self.app.project["activities"][0]["name"] = "Watch TV"

    def test_candidate_saved_without_calling_remote_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidate.EZHex"
            candidate = SimpleNamespace(image=b"candidate", places_changed=2, changed_bytes=3)
            with patch("harmony_one.app.messagebox.askyesno", return_value=True), \
                 patch("harmony_one.app.messagebox.showinfo") as info, \
                 patch("harmony_one.app.filedialog.asksaveasfilename", return_value=str(path)), \
                 patch("harmony_one.app.prepare_activity_rename", return_value=candidate), \
                 patch("harmony_one.app.write_project_to_remote") as writer:
                HarmonyOfflineApp._preview_write(self.app)
            self.assertEqual(path.read_bytes(), b"candidate")
            writer.assert_not_called()
            self.assertIn("not an upload-ready", info.call_args.args[1])

    def test_other_project_change_stays_blocked(self):
        self.app.project["activities"][0]["volume_device_id"] = "tv"
        with patch("harmony_one.app.messagebox.showwarning") as warning, \
             patch("harmony_one.app.prepare_activity_rename") as encoder:
            HarmonyOfflineApp._preview_write(self.app)
        encoder.assert_not_called()
        self.assertIn("Writing is blocked", warning.call_args.args[1])

    def test_existing_candidate_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidate.EZHex"
            path.write_bytes(b"keep")
            candidate = SimpleNamespace(image=b"candidate", places_changed=2, changed_bytes=3)
            with patch("harmony_one.app.messagebox.askyesno", return_value=True), \
                 patch("harmony_one.app.messagebox.showerror") as error, \
                 patch("harmony_one.app.filedialog.asksaveasfilename", return_value=str(path)), \
                 patch("harmony_one.app.prepare_activity_rename", return_value=candidate):
                HarmonyOfflineApp._preview_write(self.app)
            self.assertEqual(path.read_bytes(), b"keep")
            error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
