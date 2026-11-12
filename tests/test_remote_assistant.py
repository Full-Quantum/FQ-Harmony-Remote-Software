import unittest
from pathlib import Path

from harmony_one.config_image import validate_image
from harmony_one.config_reader import parse_config, raw_slot_for_base_slot
from harmony_one.harmony_usb import trailer_checksum
from harmony_one.remote_assistant import (
    RemoteAssistantError,
    inspect_remote_assistant,
    prepare_remote_assistant_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "src" / "harmony_one" / "data" / "presets"


class RemoteAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = (PRESETS / "FQ Preset older.EZHex").read_bytes()

    def refreshed(self, blob):
        blob[-6:-4] = trailer_checksum(blob).to_bytes(2, "little")
        return bytes(blob)

    def test_on_off_edits_only_prompt_state_and_checksum(self):
        setting = inspect_remote_assistant(self.blob)
        self.assertTrue(setting.enabled)
        off = prepare_remote_assistant_candidate(self.blob, False)
        on = prepare_remote_assistant_candidate(off, True)
        self.assertFalse(inspect_remote_assistant(off).enabled)
        self.assertEqual(inspect_remote_assistant(on).value, 1)
        for before, after in ((self.blob, off), (off, on)):
            validate_image(after)
            self.assertEqual(len(before), len(after))
            changed = {i for i, (a, b) in enumerate(zip(before, after)) if a != b}
            self.assertTrue(changed)
            self.assertTrue(changed <= {
                setting.offset, setting.offset + 1, len(before) - 6, len(before) - 5,
            })

    def test_noop_preserves_existing_enabled_substate(self):
        self.assertEqual(prepare_remote_assistant_candidate(self.blob, True), self.blob)
        off = prepare_remote_assistant_candidate(self.blob, False)
        self.assertEqual(prepare_remote_assistant_candidate(off, False), off)

    def test_backup_without_toggle_is_explicitly_unsupported(self):
        blob = (PRESETS / "FQ Preset newer.EZHex").read_bytes()
        with self.assertRaisesRegex(RemoteAssistantError, "toggle"):
            prepare_remote_assistant_candidate(blob, True)

    def test_invalid_checksum_is_refused(self):
        blob = bytearray(self.blob)
        blob[-6] ^= 1
        with self.assertRaisesRegex(RemoteAssistantError, "checksum"):
            inspect_remote_assistant(bytes(blob))

    def test_invalid_state_layout_is_refused(self):
        setting = inspect_remote_assistant(self.blob)
        for relative, value in ((0, 4), (2, 4), (4, 1), (6, 1)):
            with self.subTest(relative=relative):
                blob = bytearray(self.blob)
                blob[setting.offset + relative] = value
                with self.assertRaisesRegex(RemoteAssistantError, "layout"):
                    inspect_remote_assistant(self.refreshed(blob))

    def test_aliased_state_record_is_refused(self):
        blob = bytearray(self.blob)
        config = parse_config(blob)
        setting = inspect_remote_assistant(self.blob)
        start, _ = config.sections[raw_slot_for_base_slot(13)]
        blob[start + 8:start + 11] = (config.base_address + setting.offset).to_bytes(3, "little")
        with self.assertRaisesRegex(RemoteAssistantError, "overlap"):
            inspect_remote_assistant(self.refreshed(blob))

    def test_truncated_state_table_is_refused(self):
        blob = bytearray(self.blob)
        config = parse_config(blob)
        start, _ = config.sections[raw_slot_for_base_slot(13)]
        blob[start:start + 2] = b"\xff\xff"
        with self.assertRaisesRegex(RemoteAssistantError, "truncated"):
            inspect_remote_assistant(self.refreshed(blob))

    def test_both_captured_backup_layouts_can_toggle(self):
        for filename in (
            "HarmonyOne-StyleB-20261101-120043.EZHex",
            "HarmonyOne-slideshow-test-5seconds-20261108.EZHex",
        ):
            with self.subTest(filename=filename):
                path = ROOT / "backups" / filename
                if not path.exists():
                    continue
                blob = path.read_bytes()
                self.assertFalse(inspect_remote_assistant(
                    prepare_remote_assistant_candidate(blob, False)
                ).enabled)
