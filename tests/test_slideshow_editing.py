import unittest
from pathlib import Path

from harmony_one.config_image import validate_image
from harmony_one.config_reader import parse_config, raw_slot_for_base_slot
from harmony_one.key_bindings import locate_action_lists
from harmony_one.slideshow import encode_raw_picture, full_screen_pictures, refresh_checksum
from harmony_one.slideshow_cycle import (
    MAX_SLIDE_SECONDS, SlideshowCycleError, read_picture_cycle,
    restore_default_cycle, set_cycle_seconds,
)


PRESETS = Path(__file__).resolve().parents[1] / "src" / "harmony_one" / "data" / "presets"


def timer_offsets(blob):
    config = parse_config(blob)
    start, _ = config.sections[raw_slot_for_base_slot(12)]
    return [
        int.from_bytes(blob[start + 1 + i * 3:start + 4 + i * 3], "little") - config.base_address
        for i in range(blob[start])
    ]


def cycle(blob):
    return read_picture_cycle(blob, full_screen_pictures(blob))


class SlideshowEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backups = {
            name: (PRESETS / f"FQ Preset {name}.EZHex").read_bytes()
            for name in ("older", "newer")
        }
        cls.images = [
            encode_raw_picture([[(i * 60, 16, 200)] * 176 for _ in range(220)])
            for i in range(4)
        ]

    def assert_only_changed(self, before, after, allowed):
        self.assertEqual(len(before), len(after))
        validate_image(after)
        changed = {i for i, (left, right) in enumerate(zip(before, after)) if left != right}
        self.assertTrue(changed <= allowed, f"Unexpected changes at {sorted(changed - allowed)[:10]}")

    def test_each_of_six_slots_has_independent_whole_second_timing(self):
        for name, blob in self.backups.items():
            original = cycle(blob)
            for slot, screen in enumerate(original):
                with self.subTest(preset=name, slot=slot):
                    candidate = set_cycle_seconds(blob, slot, 7 + slot)
                    updated = cycle(candidate)
                    self.assertEqual([s.mode for s in updated], [s.mode for s in original])
                    self.assertEqual(
                        [s.seconds for s in updated],
                        [7 + slot if i == slot else s.seconds for i, s in enumerate(original)],
                    )
                    offset = timer_offsets(blob)[screen.timer]
                    self.assert_only_changed(
                        blob, candidate, set(range(offset + 1, offset + 4)) | set(range(len(blob) - 6, len(blob) - 4)),
                    )

    def test_duration_boundaries_and_noop(self):
        blob = self.backups["newer"]
        for seconds in (1, MAX_SLIDE_SECONDS):
            with self.subTest(seconds=seconds):
                self.assertEqual(cycle(set_cycle_seconds(blob, 0, seconds))[0].seconds, seconds)
        self.assertEqual(set_cycle_seconds(blob, 0, cycle(blob)[0].seconds), blob)

    def test_invalid_times_and_slots_are_rejected(self):
        blob = self.backups["newer"]
        for seconds in (0, -1, MAX_SLIDE_SECONDS + 1, 1.5, True, "3"):
            with self.subTest(seconds=seconds), self.assertRaises(SlideshowCycleError):
                set_cycle_seconds(blob, 0, seconds)
        for slot in (-1, 6, True, 1.5):
            with self.subTest(slot=slot), self.assertRaises(SlideshowCycleError):
                set_cycle_seconds(blob, slot, 3)

    def test_reset_restores_four_images_and_three_seconds_without_other_changes(self):
        for name, blob in self.backups.items():
            with self.subTest(preset=name):
                original = cycle(blob)
                retained = original[:3] + [original[-1]]
                candidate = restore_default_cycle(blob, self.images)
                updated = cycle(candidate)
                self.assertEqual(len(updated), 4)
                self.assertEqual([s.mode for s in updated], [s.mode for s in retained])
                self.assertEqual([s.seconds for s in updated], [3] * 4)
                self.assertEqual([s.final_marker for s in updated], [False, False, False, True])
                allowed = set(range(len(blob) - 6, len(blob) - 4))
                offsets = timer_offsets(blob)
                for screen, image in zip(updated, self.images):
                    self.assertEqual(candidate[screen.picture.offset:screen.picture.end], image)
                    allowed.update(range(screen.picture.offset, screen.picture.end))
                    allowed.update(range(offsets[screen.timer] + 1, offsets[screen.timer] + 6))
                self.assert_only_changed(blob, candidate, allowed)
                for skipped in original[3:-1]:
                    self.assertEqual(
                        candidate[skipped.picture.offset:skipped.picture.end],
                        blob[skipped.picture.offset:skipped.picture.end],
                    )
                self.assertEqual(restore_default_cycle(candidate, self.images), candidate)
                # The shortened cycle remains editable through the same candidate workflow.
                self.assertEqual(cycle(set_cycle_seconds(candidate, 3, 9))[-1].seconds, 9)

    def test_reset_requires_exactly_four_full_screen_raw_images(self):
        blob = self.backups["newer"]
        for images in (self.images[:3], self.images + [self.images[0]], [b"bad"] * 4):
            with self.subTest(count=len(images)), self.assertRaises(SlideshowCycleError):
                restore_default_cycle(blob, images)

    def test_shared_and_overlapping_timer_records_are_rejected(self):
        original = self.backups["newer"]
        config = parse_config(original)
        timer = cycle(original)[0].timer
        used = {screen.timer for screen in cycle(original)}
        other = next(i for i in range(len(timer_offsets(original))) if i not in used)
        start, _ = config.sections[raw_slot_for_base_slot(12)]
        for delta in (0, 2):
            blob = bytearray(original)
            offset = timer_offsets(original)[timer] + delta + config.base_address
            blob[start + 1 + other * 3:start + 4 + other * 3] = offset.to_bytes(3, "little")
            refresh_checksum(blob)
            with self.subTest(delta=delta), self.assertRaisesRegex(SlideshowCycleError, "overlap"):
                set_cycle_seconds(bytes(blob), 0, 8)

    def test_timer_referenced_by_an_unrelated_action_is_rejected(self):
        original = self.backups["newer"]
        config = parse_config(original)
        used = {screen.timer for screen in cycle(original)}
        action = next(
            action for action in locate_action_lists(original, config)
            if action.instructions and not any(
                item.opcode == 0x1F and item.operand in {0xEB00 | timer for timer in used}
                for item in action.instructions
            )
        )
        blob = bytearray(original)
        timer = cycle(original)[0].timer
        blob[action.offset + 1:action.offset + 4] = (0xEB00 | timer).to_bytes(2, "little") + b"\x1f"
        refresh_checksum(blob)
        with self.assertRaisesRegex(SlideshowCycleError, "another action"):
            set_cycle_seconds(bytes(blob), 0, 8)
        with self.assertRaisesRegex(SlideshowCycleError, "another action"):
            restore_default_cycle(bytes(blob), self.images)

    def test_bad_checksum_is_not_repaired_as_a_successful_edit(self):
        blob = bytearray(self.backups["newer"])
        blob[-6] ^= 1
        with self.assertRaises(SlideshowCycleError):
            set_cycle_seconds(bytes(blob), 0, 3)


if __name__ == "__main__":
    unittest.main()
