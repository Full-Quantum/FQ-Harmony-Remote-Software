import struct
import unittest

from harmony_one.config_reader import (
    ConfigError,
    parse_config,
    parse_name_tree,
    raw_slot_for_base_slot,
)


def build_container(names: list[tuple[int, int, str]]) -> bytes:
    nodes = b"".join(
        bytes([0xA7]) + struct.pack("<HHH", 4 + len(n), level, idx) + n.encode()
        for level, idx, n in names
    )
    tree = b"\xed\xfe" + (5 + len(nodes)).to_bytes(3, "little") + nodes
    tree_len = len(tree) + 2
    arch = bytes([12, 12, 0x3B, 0x0D, 0, 0, 0])
    base = 0x40000
    header_len = 0x0B + 4 * 22 + 4 + 3
    name_addr = base + header_len
    arch_addr = name_addr + tree_len
    pointers = [0] * 22
    pointers[0], pointers[1] = name_addr, arch_addr
    table = b"".join(b"\x00" + p.to_bytes(3, "little") for p in pointers)
    body = tree + b"\xef\xbe" + arch
    data = bytearray(b"GSPM" + b"\0\0\0\0" + bytes([0, 22, 0]) + table)
    data += b"LWJL" + b"\0\0\0" + body + b"\0\0PTYY"
    struct.pack_into("<I", data, 4, base + len(data) - 4)
    return bytes(data)


class ConfigReaderTests(unittest.TestCase):
    def test_slot_mapping_for_harmony_one(self):
        self.assertEqual(raw_slot_for_base_slot(7), 7)
        self.assertEqual(raw_slot_for_base_slot(8), 9)
        self.assertEqual(raw_slot_for_base_slot(17), 19)

    def test_rejects_wrong_cookie(self):
        with self.assertRaises(ConfigError):
            parse_config(b"XXXX" + bytes(200))

    def test_rejects_name_tree_without_terminator(self):
        with self.assertRaises(ConfigError):
            parse_name_tree(b"\xed\xfe\x05\x00\x00\x00\x00", 0)

    def test_parses_names_and_activity_count(self):
        data = build_container(
            [(0, 0, "Root"), (1, 0, "TV_Power_2"), (1, 1, "CurrentActivityState_0_3")]
        )
        config = parse_config(data)
        self.assertEqual(config.architecture, 12)
        self.assertEqual(config.activity_count, 2)
        self.assertEqual(config.device_labels, ["TV"])


if __name__ == "__main__":
    unittest.main()
