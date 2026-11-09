import struct
import unittest
from pathlib import Path

from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.device_encoder import locate_ir_records, prepare_carrier_change
from harmony_one.harmony_usb import trailer_checksum
from harmony_one.ir_records import read_ir_groups
from test_config_reader import build_container


def fixture(shared=False):
    base = 0x40000
    data = bytearray(build_container([(1, 0, "TV_Power_2")])[:-6])
    start = len(data)
    group = start + 4
    block = group + (9 if shared else 6)
    header = block + 6

    def addr(offset):
        return (base + offset).to_bytes(3, "little")

    data += bytes([1]) + addr(group)
    data += bytes([0, 2 if shared else 1, 0]) + addr(header + 7)
    if shared:
        data += addr(header + 7)
    data += b"\x64\x80\xc8\x00\x00\x00"
    data += b"\x00" + (26315).to_bytes(3, "little") + (13157).to_bytes(3, "little")
    data += b"\x01" + addr(header) + b"\x01" + addr(block) + b"\x00" * 6
    data += b"\x00\x00PTYY"
    data[0x0C + 4 * 5:0x0F + 4 * 5] = addr(start)
    struct.pack_into("<I", data, 4, base + len(data) - 4)
    data[-6:-4] = trailer_checksum(data).to_bytes(2, "little")
    return bytes(data)


class DeviceEncoderTests(unittest.TestCase):
    def test_candidate_changes_only_carrier_and_checksum(self):
        blob = fixture()
        location = locate_ir_records(blob)[0]
        old = read_ir_groups(blob, parse_config(blob))[0].records[0]
        candidate = prepare_carrier_change(blob, 0, 0, 40000)
        validate_image(candidate.image)
        new = read_ir_groups(candidate.image, parse_config(candidate.image))[0].records[0]
        self.assertEqual(new.carrier_hz, 40000)
        self.assertEqual(old.blocks, new.blocks)
        changed = {i for i, (a, b) in enumerate(zip(blob, candidate.image)) if a != b}
        self.assertTrue(changed <= set(range(location.header + 1, location.header + 7))
                        | {len(blob) - 6, len(blob) - 5})
        self.assertEqual(len(blob), len(candidate.image))

    def test_invalid_inputs_rejected(self):
        blob = fixture()
        for group, record, hz in ((2, 0, 40000), (0, 1, 40000), (0, 0, 0),
                                  (0, 0, True), (0, 0, 1_000_000)):
            with self.subTest(group=group, record=record, hz=hz), self.assertRaises(ConfigError):
                prepare_carrier_change(blob, group, record, hz)

    def test_rejects_bad_self_pointer(self):
        blob = bytearray(fixture())
        header = locate_ir_records(bytes(blob))[0].header
        blob[header + 8] ^= 1
        blob[-6:-4] = trailer_checksum(blob).to_bytes(2, "little")
        with self.assertRaisesRegex(ConfigError, "self pointer"):
            locate_ir_records(bytes(blob))

    def test_rejects_unsupported_record_class(self):
        blob = bytearray(fixture())
        header = locate_ir_records(bytes(blob))[0].header
        blob[header + 7] = 5
        blob[-6:-4] = trailer_checksum(blob).to_bytes(2, "little")
        with self.assertRaisesRegex(ConfigError, "class-1"):
            prepare_carrier_change(bytes(blob), 0, 0, 40000)

    def test_refuses_shared_record(self):
        blob = fixture(shared=True)
        with self.assertRaisesRegex(ConfigError, "overlaps another IR record"):
            prepare_carrier_change(blob, 0, 0, 40000)

    def test_refuses_timing_block_overlap(self):
        blob = bytearray(fixture())
        header = locate_ir_records(bytes(blob))[0].header
        blob[header + 12:header + 15] = (0x40000 + header + 1).to_bytes(3, "little")
        blob[-6:-4] = trailer_checksum(blob).to_bytes(2, "little")
        with self.assertRaisesRegex(ConfigError, "timing block overlaps"):
            prepare_carrier_change(bytes(blob), 0, 0, 40000)


BACKUP = Path(__file__).resolve().parents[1] / "backups" / "HarmonyOne-SPARE-20261106-before-development.EZHex"


@unittest.skipUnless(BACKUP.exists(), "spare backup not present")
class SpareDeviceEncoderTests(unittest.TestCase):
    def test_candidate_from_spare_backup(self):
        blob = BACKUP.read_bytes()
        records = locate_ir_records(blob)
        self.assertEqual(len(records), 252)
        candidate = prepare_carrier_change(blob, 0, 0, 40000)
        self.assertEqual(len(blob), len(candidate.image))
        validate_image(candidate.image)


if __name__ == "__main__":
    unittest.main()
