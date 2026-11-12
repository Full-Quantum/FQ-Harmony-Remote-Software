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
                 patch("harmony_one.app.prepare_activity_rename", return_value=candidate):
                HarmonyOfflineApp._preview_write(self.app)
            self.assertEqual(path.read_bytes(), b"candidate")
            self.assertIn("not upload-ready", info.call_args.args[1])

    def test_other_project_change_routes_to_transfer_workflow_instead_of_upload_error(self):
        self.app.project["activities"][0]["volume_device_id"] = "tv"
        selected = []
        status = []
        self.app.tabs = SimpleNamespace(select=selected.append)
        self.app.backup_transfer_frame = object()
        self.app.restore_save_frame = object()
        self.app.backup_transfer_tabs = SimpleNamespace(
            select=lambda frame: selected.append(frame)
        )
        self.app.save_restore = SimpleNamespace(
            status=SimpleNamespace(set=status.append)
        )
        self.app._open_restore_save = lambda: HarmonyOfflineApp._open_restore_save(self.app)
        with patch("harmony_one.app.prepare_activity_rename") as encoder:
            HarmonyOfflineApp._preview_write(self.app)
        encoder.assert_not_called()
        self.assertEqual(
            selected,
            [self.app.backup_transfer_frame, self.app.restore_save_frame],
        )
        self.assertIn("local project changes cannot be included", status[-1])
        self.assertIn("Transfer Remote workflow", status[-1])

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
