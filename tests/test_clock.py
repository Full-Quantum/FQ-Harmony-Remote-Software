import unittest
from datetime import datetime, timezone
from pathlib import Path

from harmony_one.clock import ClockError, prepare_clock_candidate
from harmony_one.config_image import BytePatch, patch_image, validate_image
from harmony_one.config_reader import parse_config, raw_slot_for_base_slot

BACKUPS = Path(__file__).resolve().parents[1] / "backups"
BASELINES = (
    BACKUPS / "HarmonyOne-oldest-style-baseline-20261108-185955.EZHex",
    BACKUPS / "HarmonyOne-older-style-baseline-20261108-190909.EZHex",
    BACKUPS / "HarmonyOne-newest-style-baseline-20261108-191346.EZHex",
)


@unittest.skipUnless(all(path.exists() for path in BASELINES), "owner backups not present")
class ClockCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blobs = [path.read_bytes() for path in BASELINES]

    def test_sets_clock_timestamp_and_state_records_without_other_changes(self):
        when = datetime(2028, 2, 29, 23, 58, 57)
        expected = bytes((57, 58, 23, 28, 2, 1, 28))
        for source in self.blobs:
            with self.subTest(length=len(source)):
                candidate = prepare_clock_candidate(source, when)
                config = parse_config(candidate.image)
                timestamp_start = config.sections[raw_slot_for_base_slot(3)][0]
                self.assertEqual(candidate.image[timestamp_start + 2 : timestamp_start + 9], expected)
                state_start = config.sections[raw_slot_for_base_slot(13)][0]
                count = int.from_bytes(candidate.image[state_start : state_start + 2], "little")
                offsets = [
                    int.from_bytes(
                        candidate.image[state_start + 8 + index * 3 : state_start + 11 + index * 3],
                        "little",
                    ) - config.base_address
                    for index in range(7)
                ]
                self.assertGreaterEqual(count, 7)
                self.assertEqual(
                    [int.from_bytes(candidate.image[offset : offset + 2], "little") for offset in offsets],
                    list(expected),
                )
                self.assertEqual(
                    int.from_bytes(candidate.image[offsets[3] + 2 : offsets[3] + 4], "little"),
                    30,
                )
                self.assertEqual(
                    int.from_bytes(candidate.image[offsets[6] + 2 : offsets[6] + 4], "little"),
                    29,
                )
                validate_image(candidate.image)
                self.assertEqual(len(candidate.image), len(source))
                self.assertEqual(
                    candidate.changed_bytes,
                    sum(before != after for before, after in zip(source, candidate.image)),
                )
                changed_offsets = {
                    index
                    for index, (before, after) in enumerate(zip(source, candidate.image))
                    if before != after
                }
                allowed_offsets = set(range(timestamp_start + 2, timestamp_start + 9))
                allowed_offsets.update(range(len(source) - 6, len(source) - 4))
                for offset in offsets:
                    allowed_offsets.update(range(offset, offset + 4))
                self.assertTrue(changed_offsets <= allowed_offsets)

    def test_rejects_timezone_aware_time(self):
        with self.assertRaisesRegex(ClockError, "without a timezone offset"):
            prepare_clock_candidate(self.blobs[0], datetime(2026, 11, 8, tzinfo=timezone.utc))

    def test_rejects_unrepresentable_year(self):
        with self.assertRaisesRegex(ClockError, "2000 through 2255"):
            prepare_clock_candidate(self.blobs[0], datetime(2256, 1, 1))

    def test_rejects_non_harmony_backup(self):
        with self.assertRaises(ClockError):
            prepare_clock_candidate(b"not a backup", datetime(2026, 11, 8))

    def test_refuses_a_state_pointer_outside_the_configuration(self):
        source = self.blobs[0]
        config = parse_config(source)
        table_start = config.sections[raw_slot_for_base_slot(13)][0]
        pointer = table_start + 8
        malformed = patch_image(
            source,
            [BytePatch(pointer, source[pointer : pointer + 3], bytes(3), "corrupt clock pointer")],
        )
        with self.assertRaisesRegex(ClockError, "points outside"):
            prepare_clock_candidate(malformed, datetime(2026, 11, 8))

    def test_refuses_disagreeing_saved_clock_records(self):
        source = self.blobs[0]
        config = parse_config(source)
        clock_start = config.sections[raw_slot_for_base_slot(3)][0] + 2
        malformed = patch_image(
            source,
            [
                BytePatch(
                    clock_start,
                    source[clock_start : clock_start + 1],
                    bytes((source[clock_start] + 1,)),
                    "corrupt clock timestamp",
                )
            ],
        )
        with self.assertRaisesRegex(ClockError, "disagree"):
            prepare_clock_candidate(malformed, datetime(2026, 11, 8))


if __name__ == "__main__":
    unittest.main()
