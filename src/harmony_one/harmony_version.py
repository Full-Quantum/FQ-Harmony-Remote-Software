"""Read-only GET_VERSION for a Harmony One (request 0x10, reply 0x28 + 12 bytes)."""

from __future__ import annotations

from dataclasses import dataclass

from harmony_one.harmony_usb import (
    HARMONY_ONE_PRODUCT_ID,
    HARMONY_ONE_VENDOR_ID,
    REPORT_SIZE,
    BackupError,
    HarmonyOneHidTransport,
)

GET_VERSION = 0x10
VERSION_REPLY = 0x28


@dataclass(frozen=True)
class VersionBlock:
    raw: bytes

    @staticmethod
    def _nibbles(value: int) -> str:
        return f"{value >> 4}.{value & 0xF}"

    @property
    def firmware(self) -> str:
        return self._nibbles(self.raw[0])

    @property
    def hardware(self) -> str:
        return self._nibbles(self.raw[1])

    @property
    def flash_chip_id(self) -> str:
        return f"{self.raw[3]:02X}:{self.raw[2]:02X}"

    @property
    def architecture(self) -> int:
        return self.raw[4] >> 4

    @property
    def software_type(self) -> int:
        return self.raw[4] & 0xF

    @property
    def skin(self) -> int:
        return self.raw[5]

    def describe(self) -> list[str]:
        mode = {0: "application", 4: "safe mode"}.get(self.software_type, f"type {self.software_type}")
        return [
            f"Firmware {self.firmware}, hardware {self.hardware}",
            f"Flash chip id {self.flash_chip_id}, architecture {self.architecture}, skin {self.skin}",
            f"Running: {mode}",
            f"Raw block: {self.raw.hex(' ')}",
        ]


def parse_version_reply(report: bytes) -> VersionBlock:
    if len(report) < 13 or report[0] != VERSION_REPLY:
        raise BackupError(f"unexpected version reply {report[:2].hex()}")
    return VersionBlock(bytes(report[1:13]))


def read_version(device: object) -> VersionBlock:
    """Send GET_VERSION on an open HID handle and parse the reply. Sends no write command."""
    transport = HarmonyOneHidTransport(device)
    request = bytes([GET_VERSION]) + bytes(REPORT_SIZE - 1)
    device.write(b"\x00" + request)
    for _ in range(5):
        report = transport._read_report()
        if report is not None and report[0] == VERSION_REPLY:
            return parse_version_reply(report)
    raise BackupError("the remote did not answer GET_VERSION")


def read_connected_version() -> VersionBlock:
    import hid

    devices = hid.enumerate(HARMONY_ONE_VENDOR_ID, HARMONY_ONE_PRODUCT_ID)
    if len(devices) != 1:
        raise BackupError(f"expected exactly one Harmony One, found {len(devices)}")
    device = hid.device()
    device.open_path(devices[0]["path"])
    try:
        device.set_nonblocking(False)
        return read_version(device)
    finally:
        device.close()
