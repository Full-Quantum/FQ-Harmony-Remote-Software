import os
import unittest
from unittest import mock

from harmony_one import flash_writer as fw


class SimRemote:
    """Simulated flash: erase sets a 64 KiB block to 0xFF; programming only clears bits."""

    def __init__(self, size=0x400000, config=b""):
        self.flash = bytearray(b"\xff" * size)
        self.flash[fw.CONFIG_BASE : fw.CONFIG_BASE + len(config)] = config
        self.replies = []
        self.pending = None
        self.log = []

    def send(self, report):
        self.log.append(report[0])
        first = report[0]
        if self.pending is not None:
            if first & 0xF0 == 0x40:
                nibble = first & 0x0F
                length = {v: k for k, v in fw.LENGTH_NIBBLE.items()}[nibble]
                self.pending["data"] += report[1 : 1 + length]
                return
            assert report[:2] == bytes([0xF1, 0x30]), "stray command during write"
            address, data, count = self.pending["address"], self.pending["data"], self.pending["count"]
            assert len(data) == count
            for i, byte in enumerate(data):
                self.flash[address + i] &= byte
            self.pending = None
            self.replies.append(bytes([0xF0, 0x30]))
        elif first == 0x35:
            self.pending = {
                "address": int.from_bytes(report[1:4], "big"),
                "count": int.from_bytes(report[4:6], "big"),
                "data": bytearray(),
            }
        elif first == 0xD3:
            address = int.from_bytes(report[1:4], "big")
            self.flash[address : address + fw.ERASE_BLOCK] = b"\xff" * fw.ERASE_BLOCK
            self.replies.append(bytes([0xF0, 0xD0]))
        elif first == 0xA5:
            self.replies.append(bytes([0xF0, 0xA0]))

    def receive(self):
        return self.replies.pop(0) if self.replies else None

    def read_flash(self, address, count):
        return bytes(self.flash[address : address + count])


class EncodingTests(unittest.TestCase):
    def test_request_bytes(self):
        self.assertEqual(fw.encode_erase(0x040000)[:4], bytes([0xD3, 0x04, 0x00, 0x00]))
        self.assertEqual(fw.encode_write_announce(0x040000, 3150)[:6], bytes([0x35, 4, 0, 0, 0x0C, 0x4E]))
        self.assertEqual(fw.encode_write_done()[:2], bytes([0xF1, 0x30]))
        self.assertEqual(fw.encode_write_misc(2, 0, 0)[:6], bytes([0xA5, 2, 0, 0, 0, 0]))
        self.assertEqual(fw.encode_data_packet(bytes(63))[0], 0x4A)
        self.assertEqual(len(fw.encode_data_packet(b"\x01")), 64)

    def test_unaligned_erase_and_oversized_transfer_refused(self):
        with self.assertRaises(fw.WriteError):
            fw.encode_erase(0x040001)
        with self.assertRaises(fw.WriteError):
            fw.encode_write_announce(0x040000, 3151)

    def test_split_packets_only_uses_expressible_sizes(self):
        for size in (1, 8, 14, 16, 62, 64, 100, 3150, 65536 % 3150):
            data = bytes(range(256)) * 13
            data = data[:size]
            packets = fw.split_packets(data)
            self.assertEqual(b"".join(packets), data)
            self.assertTrue(all(len(p) in fw.LENGTH_NIBBLE for p in packets))


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.old = bytes((i * 7) & 0xFF for i in range(70_000))
        self.new = bytes((i * 11 + 3) & 0xFF for i in range(70_500))

    def test_roundtrip_on_simulated_flash(self):
        remote = SimRemote(config=self.old)
        remote.flash[fw.CONFIG_BASE + 140_000 : fw.CONFIG_BASE + 140_010] = b"keepme1234"
        with mock.patch.dict(os.environ, {fw.WRITE_ENV_VAR: "1"}):
            fw.write_configuration(remote, self.new, self.old)
        self.assertEqual(remote.read_flash(fw.CONFIG_BASE, len(self.new)), self.new)
        self.assertEqual(remote.log[-1], 0xE1)
        # bytes after the configuration within its last block are preserved
        self.assertEqual(remote.read_flash(fw.CONFIG_BASE + 140_000, 10), b"keepme1234")

    def test_disabled_by_default(self):
        remote = SimRemote(config=self.old)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(fw.WRITE_ENV_VAR, None)
            with self.assertRaises(fw.WriteError):
                fw.write_configuration(remote, self.new, self.old)
        self.assertEqual(remote.log, [])

    def test_refuses_when_backup_does_not_match_remote(self):
        remote = SimRemote(config=self.old)
        with mock.patch.dict(os.environ, {fw.WRITE_ENV_VAR: "1"}):
            with self.assertRaises(fw.WriteError):
                fw.write_configuration(remote, self.new, self.old[:-1] + b"\x00")
        self.assertEqual(remote.log, [])

    def test_verify_catches_bad_programming(self):
        remote = SimRemote(config=self.old)
        plan = fw.plan_write(self.new, remote.read_flash)
        writer = fw.FlashWriter(remote)
        writer.write_image(plan)
        remote.flash[fw.CONFIG_BASE + 5] ^= 0x01
        with self.assertRaises(fw.WriteError):
            writer.verify(plan)


if __name__ == "__main__":
    unittest.main()
