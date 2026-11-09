import tempfile
import unittest
from pathlib import Path

from harmony_one.harmony_usb import (
    CONFIG_FLASH_BASE,
    CONFIG_HEADER_MAGIC,
    CONFIG_END_MARKER,
    FLASH_CHUNK_DATA,
    READ_FLASH,
    BackupError,
    HarmonyOneHidTransport,
    _encode_read_flash,
    _read_and_save_backup,
    _save_validated_backup,
    read_harmony_one_config,
    trailer_checksum,
)


def _report_nibble(payload_length):
    for nibble in range(16):
        decoded = {8: 15, 9: 31, 10: 63}.get(nibble, nibble)
        if decoded == payload_length:
            return nibble
    raise AssertionError(f"unencodable test payload length {payload_length}")


class FakeHidDevice:
    def __init__(self, memory, corrupt_sequence=False, report_id=False):
        self.memory = memory
        self.reports = []
        self.requests = []
        self.corrupt_sequence = corrupt_sequence
        self.report_id = report_id

    def write(self, request):
        self.requests.append(request)
        if len(request) != 65 or request[0] != 0 or request[1] != (READ_FLASH | 5):
            raise AssertionError("test expected only a padded READ_FLASH request")
        address = int.from_bytes(request[2:5], "big")
        count = int.from_bytes(request[5:7], "big")
        data = self.memory[address - CONFIG_FLASH_BASE : address - CONFIG_FLASH_BASE + count]
        if len(data) != count:
            raise AssertionError("fake flash does not cover request")
        sequence = 0x01
        offset = 0
        first_chunk = True
        while offset < count:
            chunk = data[offset : offset + FLASH_CHUNK_DATA]
            payload = bytes([sequence]) + chunk
            report = bytearray(64)
            report[0] = 0x60 | _report_nibble(len(payload))
            report[1 : 1 + len(payload)] = payload
            if self.corrupt_sequence and first_chunk:
                report[1] = 0x12
            self.reports.append(bytes(report))
            sequence = (sequence + 0x11) & 0xFF
            offset += len(chunk)
            first_chunk = False
        ack = bytearray(64)
        ack[0:2] = b"\xf0\x50"
        self.reports.append(bytes(ack))
        return 65

    def read(self, _size, _timeout):
        if not self.reports:
            return []
        report = self.reports.pop(0)
        return b"\x00" + report if self.report_id else report


def make_config(length):
    if length < 62:
        raise ValueError("test config must include the full header probe")
    blob = bytearray((index * 13 + 7) & 0xFF for index in range(length))
    blob[:4] = CONFIG_HEADER_MAGIC
    end_address = CONFIG_FLASH_BASE + length - len(CONFIG_END_MARKER)
    blob[4:8] = end_address.to_bytes(4, "little")
    blob[-len(CONFIG_END_MARKER) :] = CONFIG_END_MARKER
    checksum = trailer_checksum(bytes(blob))
    blob[-6:-4] = checksum.to_bytes(2, "little")
    return bytes(blob)


class HarmonyUsbTests(unittest.TestCase):
    def test_read_flash_request_is_the_only_five_byte_read_command(self):
        report = _encode_read_flash(0x040000, 62)
        self.assertEqual(len(report), 64)
        self.assertEqual(report[:6], b"\x55\x04\x00\x00\x00\x3e")
        self.assertEqual(set(report[6:]), {0})

    def test_transport_reads_sequence_checked_data_until_ack(self):
        source = bytes(range(62))
        device = FakeHidDevice(source, report_id=True)
        result = HarmonyOneHidTransport(device).read_flash(CONFIG_FLASH_BASE, len(source))
        self.assertEqual(result, source)
        self.assertEqual(len(device.requests), 1)

    def test_transport_rejects_out_of_sequence_flash_data(self):
        device = FakeHidDevice(bytes(range(62)), corrupt_sequence=True)
        with self.assertRaisesRegex(BackupError, "out of sequence"):
            HarmonyOneHidTransport(device).read_flash(CONFIG_FLASH_BASE, 62)

    def test_reads_validates_and_returns_exact_container_bytes(self):
        expected = make_config(134)
        fake_memory = expected + bytes(62)
        device = FakeHidDevice(fake_memory)
        progress = []
        result = read_harmony_one_config(
            HarmonyOneHidTransport(device),
            lambda done, total: progress.append((done, total)),
        )
        self.assertEqual(result, expected)
        self.assertEqual(progress[-1], (len(expected), len(expected)))
        self.assertEqual(
            [int.from_bytes(request[5:7], "big") for request in device.requests],
            [62, 124],
        )

    def test_rejects_wrong_config_magic(self):
        bad_header = bytearray(make_config(134))
        bad_header[:4] = b"NOPE"
        device = FakeHidDevice(bytes(bad_header) + bytes(62))
        with self.assertRaisesRegex(BackupError, "does not start with GSPM"):
            read_harmony_one_config(HarmonyOneHidTransport(device))

    def test_rejects_invalid_end_marker(self):
        damaged = bytearray(make_config(134))
        damaged[-4:] = b"FAIL"
        checksum = trailer_checksum(bytes(damaged))
        damaged[-6:-4] = checksum.to_bytes(2, "little")
        device = FakeHidDevice(bytes(damaged) + bytes(62))
        with self.assertRaisesRegex(BackupError, "end marker"):
            read_harmony_one_config(HarmonyOneHidTransport(device))

    def test_rejects_invalid_trailer_checksum(self):
        damaged = bytearray(make_config(134))
        damaged[20] ^= 0x01
        device = FakeHidDevice(bytes(damaged) + bytes(62))
        with self.assertRaisesRegex(BackupError, "trailer checksum"):
            read_harmony_one_config(HarmonyOneHidTransport(device))

    def test_failed_integrity_check_does_not_create_backup(self):
        damaged = bytearray(make_config(134))
        damaged[20] ^= 0x01
        device = FakeHidDevice(bytes(damaged) + bytes(62))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "HarmonyOne-current.EZHex"
            with self.assertRaises(BackupError):
                _read_and_save_backup(HarmonyOneHidTransport(device), target, None, False)
            self.assertFalse(target.exists())

    def test_validated_backup_is_saved_as_exact_bare_container(self):
        expected = make_config(134)
        device = FakeHidDevice(expected + bytes(62))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "HarmonyOne-current.EZHex"
            result = _read_and_save_backup(
                HarmonyOneHidTransport(device), target, None, False
            )
            self.assertEqual(result, expected)
            self.assertEqual(target.read_bytes(), expected)

    def test_backup_save_does_not_replace_existing_file_without_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "HarmonyOne-current.EZHex"
            target.write_bytes(b"keep existing")
            with self.assertRaisesRegex(BackupError, "already exists"):
                _save_validated_backup(target, make_config(134), overwrite=False)
            self.assertEqual(target.read_bytes(), b"keep existing")


if __name__ == "__main__":
    unittest.main()
