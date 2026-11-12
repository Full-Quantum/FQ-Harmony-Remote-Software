"""Harmony One flash writing: request encoding and the erase/write/verify sequence.

Protocol facts come from the MIT-licensed public reference at
github.com/dannybloe/harmony-explorations (reference/usb-protocol/commands.md); no
third-party code is used. ``FlashWriter`` talks to an abstract ``WriteTransport`` so the
sequence can be tested against simulated flash. The slideshow, clock, display-style,
activity, and complete-save workflows use this writer; general project writes remain
blocked until a project-to-container encoder is verified.

Wire summary (reports are 64 bytes, first byte = command | length nibble):
  reset escape       0xE1 <sub>              (0x02 restarts the remote)
  WRITE_MISC         0xA5 sel addr16 val16   -> F0 A0
  ERASE_FLASH        0xD3 addr24 (64 KiB block) -> F0 D0
  WRITE_FLASH        0x35 addr24 count16, then data packets 0x4A + 63 bytes
                     (shorter packets use nibbles 1-7, 15->8, 31->9, 63->A),
                     then 0xF1 0x30            -> F0 30
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Protocol

from harmony_one.harmony_usb import CONFIG_FLASH_LIMIT

REPORT_SIZE = 64
ERASE_BLOCK = 0x10000
CONFIG_BASE = 0x040000
MAX_TRANSFER = 3150
PACKET_SIZES = (63, 31, 15, 7, 6, 5, 4, 3, 2, 1)
LENGTH_NIBBLE = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 15: 8, 31: 9, 63: 0xA}
WRITE_ENV_VAR = "HARMONY_ENABLE_WRITES"

CMD_WRITE_FLASH = 0x30
CMD_WRITE_MISC = 0xA0
CMD_ERASE_FLASH = 0xD0
CMD_ESCAPE = 0xE0
ACK = 0xF0


class WriteError(Exception):
    """A flash write step failed or was refused."""


class WriteTransport(Protocol):
    def send(self, report: bytes) -> None: ...
    def receive(self) -> bytes | None: ...
    def read_flash(self, address: int, count: int) -> bytes: ...


def _pad(report: bytes) -> bytes:
    return report + bytes(REPORT_SIZE - len(report))


def encode_reset(sub_command: int) -> bytes:
    return _pad(bytes([CMD_ESCAPE | 1, sub_command]))


def encode_write_misc(selector: int, address: int, value: int) -> bytes:
    return _pad(
        bytes([CMD_WRITE_MISC | 5, selector]) + address.to_bytes(2, "big") + value.to_bytes(2, "big")
    )


def encode_erase(address: int) -> bytes:
    if address % ERASE_BLOCK:
        raise WriteError(f"erase address 0x{address:06x} is not on a 64 KiB block boundary")
    return _pad(bytes([CMD_ERASE_FLASH | 3]) + address.to_bytes(3, "big"))


def encode_write_announce(address: int, count: int) -> bytes:
    if not 0 < count <= MAX_TRANSFER:
        raise WriteError(f"transfer of {count} bytes is outside 1..{MAX_TRANSFER}")
    return _pad(bytes([CMD_WRITE_FLASH | 5]) + address.to_bytes(3, "big") + count.to_bytes(2, "big"))


def encode_write_done() -> bytes:
    return _pad(bytes([0xF0 | 1, CMD_WRITE_FLASH]))


def split_packets(data: bytes) -> list[bytes]:
    """Split transfer data into packets whose lengths the length nibble can express."""
    packets: list[bytes] = []
    position = 0
    while position < len(data):
        remaining = len(data) - position
        size = next(s for s in PACKET_SIZES if s <= remaining)
        packets.append(data[position : position + size])
        position += size
    return packets


def encode_data_packet(payload: bytes) -> bytes:
    nibble = LENGTH_NIBBLE.get(len(payload))
    if nibble is None:
        raise WriteError(f"a {len(payload)}-byte data packet cannot be expressed on the wire")
    return _pad(bytes([0x40 | nibble]) + payload)


@dataclass(frozen=True)
class WritePlan:
    base: int
    image: bytes  # whole 64 KiB blocks, ready to program

    @property
    def blocks(self) -> list[int]:
        return [self.base + offset for offset in range(0, len(self.image), ERASE_BLOCK)]


def plan_write(new_config: bytes, read_flash: Callable[[int, int], bytes]) -> WritePlan:
    """Pad a configuration to whole erase blocks, keeping whatever follows it in its last block.

    An erase clears a full 64 KiB block, so the bytes after the configuration in its last
    block are read first and written back unchanged.
    """
    if not new_config:
        raise WriteError("refusing to write an empty configuration")
    block_count = -(-len(new_config) // ERASE_BLOCK)
    total = block_count * ERASE_BLOCK
    if CONFIG_BASE + total > CONFIG_FLASH_LIMIT:
        raise WriteError("configuration and preserved tail exceed Harmony One flash capacity")
    tail_length = total - len(new_config)
    tail = read_flash(CONFIG_BASE + len(new_config), tail_length) if tail_length else b""
    if len(tail) != tail_length:
        raise WriteError("could not read the bytes that follow the configuration")
    return WritePlan(CONFIG_BASE, new_config + tail)


def writes_enabled() -> bool:
    return os.environ.get(WRITE_ENV_VAR) == "1"


class FlashWriter:
    def __init__(self, transport: WriteTransport) -> None:
        self._t = transport

    def _expect_ack(self, command: int, what: str) -> None:
        for _ in range(30):
            report = self._t.receive()
            if report is None:
                continue
            if report[0] == ACK and report[1] == command:
                return
            raise WriteError(f"{what}: unexpected reply {report[:2].hex()}")
        raise WriteError(f"{what}: no acknowledgment from the remote")

    def erase_block(self, address: int) -> None:
        self._t.send(encode_erase(address))
        self._expect_ack(CMD_ERASE_FLASH, f"erase 0x{address:06x}")

    def write_transfer(self, address: int, data: bytes) -> None:
        self._t.send(encode_write_announce(address, len(data)))
        for packet in split_packets(data):
            self._t.send(encode_data_packet(packet))
        self._t.send(encode_write_done())
        self._expect_ack(CMD_WRITE_FLASH, f"write 0x{address:06x}")

    def write_image(
        self, plan: WritePlan, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        self._t.send(encode_write_misc(0x02, 0, 0))
        self._expect_ack(CMD_WRITE_MISC, "drop cached region descriptors")
        for block in plan.blocks:
            self.erase_block(block)
        done = 0
        for offset in range(0, len(plan.image), MAX_TRANSFER):
            chunk = plan.image[offset : offset + MAX_TRANSFER]
            self.write_transfer(plan.base + offset, chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, len(plan.image))

    def verify(self, plan: WritePlan) -> None:
        for offset in range(0, len(plan.image), 16_368):
            want = plan.image[offset : offset + 16_368]
            got = self._t.read_flash(plan.base + offset, len(want))
            if got != want:
                raise WriteError(f"read-back differs from the written image near offset {offset}")


def write_configuration(
    transport: WriteTransport,
    new_config: bytes,
    current_backup: bytes,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Rehearsed write path: refuse unless a verified backup of this exact unit matches flash.

    The caller must hold ``HARMONY_ENABLE_WRITES=1`` and pass a backup that equals what the
    remote currently stores; the image is read back and compared afterwards, and the remote
    is restarted last.
    """
    if not writes_enabled():
        raise WriteError(f"writes are disabled; set {WRITE_ENV_VAR}=1 to enable them")
    live = transport.read_flash(CONFIG_BASE, len(current_backup))
    if live != current_backup:
        raise WriteError("the supplied backup does not match what is on the remote right now")
    writer = FlashWriter(transport)
    plan = plan_write(new_config, transport.read_flash)
    writer.write_image(plan, on_progress)
    writer.verify(plan)
    transport.send(encode_reset(0x02))
