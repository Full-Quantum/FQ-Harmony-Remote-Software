import unittest
from pathlib import Path
from unittest.mock import patch

from harmony_one.config_reader import RemoteConfig, raw_slot_for_base_slot
from harmony_one.key_bindings import Instruction, LocatedActionList
from harmony_one.pictures import Picture
from harmony_one.slideshow import full_screen_pictures
from harmony_one.slideshow_cycle import SlideshowCycleError, read_picture_cycle


BACKUPS = Path(__file__).resolve().parents[1] / "backups"


def fixture(next_modes=(1, 2, 0), final_modes=(2,)):
    blob = bytearray(1200)
    count = len(next_modes)
    blob[100:103] = count.to_bytes(3, "little")
    blob[200] = count
    pictures = []
    actions = []
    for mode, next_mode in enumerate(next_modes):
        head = 300 + 50 * mode
        pointer = head + 20
        record = 650 + 7 * mode
        image = 900 + mode
        blob[103 + mode * 3:106 + mode * 3] = pointer.to_bytes(3, "little")
        blob[pointer + 1:pointer + 4] = head.to_bytes(3, "little")
        blob[head:head + 5] = bytes((1, 6, mode, 0, 0x7F))
        blob[head + 5:head + 13] = b"\x02\x00\x00" + image.to_bytes(3, "little") + b"\x17\x00"
        blob[201 + mode * 3:204 + mode * 3] = record.to_bytes(3, "little")
        blob[record:record + 7] = b"\x01\x05\x00\x00" + next_mode.to_bytes(2, "little") + b"\x7e"
        instructions = [
            Instruction(0x07, 0xFFFB), Instruction(0x1F, 0xFE02),
            Instruction(0x1F, 0xE805), Instruction(0x1F, 0xEB00 | mode),
        ]
        if mode in final_modes:
            instructions.append(Instruction(0x1F, 0xF229))
        actions.append(LocatedActionList(mode, 0, tuple(instructions)))
        pictures.append((mode, Picture(image, 0, 176, 220, 77445)))
    config = RemoteConfig(
        len(blob), 0, 12, 54, [],
        {raw_slot_for_base_slot(6): (100, 150), raw_slot_for_base_slot(12): (200, 250)},
    )
    return blob, config, pictures, actions


class CycleTests(unittest.TestCase):
    def decode(self, blob, config, pictures, actions):
        with patch("harmony_one.slideshow_cycle.parse_config", return_value=config), patch(
            "harmony_one.slideshow_cycle.locate_action_lists", return_value=actions
        ):
            return read_picture_cycle(bytes(blob), pictures)

    def test_orders_transition_graph_not_picture_bank(self):
        blob, config, pictures, actions = fixture((2, 0, 1), (1,))
        cycle = self.decode(blob, config, pictures, actions)
        self.assertEqual([screen.picture_index for screen in cycle], [0, 2, 1])
        self.assertEqual([screen.seconds for screen in cycle], [5, 5, 5])

    def test_ambiguous_cycles_are_refused(self):
        args = fixture((1, 0, 3, 2), (1, 3))
        with self.assertRaises(SlideshowCycleError):
            self.decode(*args)

    def test_invalid_mode_pointer_is_refused(self):
        blob, config, pictures, actions = fixture()
        blob[103:106] = (len(blob) + 10).to_bytes(3, "little")
        with self.assertRaisesRegex(SlideshowCycleError, "mode pointer"):
            self.decode(blob, config, pictures, actions)

    def test_unknown_screen_program_is_not_guessed(self):
        blob, config, pictures, actions = fixture()
        blob[305] = 3
        with self.assertRaises(SlideshowCycleError):
            self.decode(blob, config, pictures, actions)

    def test_truncated_timer_pointer_table_is_refused(self):
        blob, config, pictures, actions = fixture()
        blob[200] = 255
        with self.assertRaisesRegex(SlideshowCycleError, "timer pointer table"):
            self.decode(blob, config, pictures, actions)

    def test_cycle_without_final_marker_is_refused(self):
        args = fixture(final_modes=())
        with self.assertRaises(SlideshowCycleError):
            self.decode(*args)


class CapturedCycleTests(unittest.TestCase):
    def check_backup(self, name, expected, durations):
        path = BACKUPS / name
        if not path.exists():
            self.skipTest("Owner backup is not present")
        blob = path.read_bytes()
        pictures = full_screen_pictures(blob)
        numbers = {index: number for number, (index, _) in enumerate(pictures, 1)}
        cycle = read_picture_cycle(blob, pictures)
        self.assertEqual([numbers[screen.picture_index] for screen in cycle], expected)
        self.assertEqual([screen.seconds for screen in cycle], durations)

    def test_spare_backup(self):
        self.check_backup("HarmonyOne-slideshow-test-5seconds-20261108.EZHex", [6, 3, 4, 7], [5, 5, 5, 5])

    def test_observed_backup(self):
        self.check_backup("HarmonyOne-slideshow-observed-20261108-134253.EZHex", [5, 2, 3, 1], [5, 1, 1, 1])


if __name__ == "__main__":
    unittest.main()
