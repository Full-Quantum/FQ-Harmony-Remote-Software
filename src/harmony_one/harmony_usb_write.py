"""USB HID transport shared by the GUI's guarded writers and the rehearsal tool."""

from __future__ import annotations

from harmony_one.harmony_usb import (
    FLASH_CHUNK_DATA,
    HARMONY_ONE_PRODUCT_ID,
    HARMONY_ONE_VENDOR_ID,
    READ_WINDOW_BYTES,
    REPORT_SIZE,
    BackupError,
    HarmonyOneHidTransport,
    _wire_count_for,
)


class HidWriteTransport:
    def __init__(self, device: object) -> None:
        self._device = device
        self._reader = HarmonyOneHidTransport(device)

    def send(self, report: bytes) -> None:
        if len(report) != REPORT_SIZE:
            raise BackupError(f"report must be {REPORT_SIZE} bytes, got {len(report)}")
        written = self._device.write(b"\x00" + report)
        if written not in (REPORT_SIZE, REPORT_SIZE + 1):
            raise BackupError(f"HID write sent {written} bytes")

    def receive(self) -> bytes | None:
        return self._reader._read_report()

    def flush(self) -> None:
        """Discard any stale reports before a new command sequence."""
        idle = 0
        while idle < 2:
            idle = idle + 1 if self._reader._read_report() is None else 0

    def read_flash(self, address: int, count: int) -> bytes:
        out = bytearray()
        while len(out) < count:
            remaining = count - len(out)
            wire = _wire_count_for(min(remaining, READ_WINDOW_BYTES))
            part = self._read_with_retry(address + len(out), wire)
            out += part[: min(remaining, wire)]
        return bytes(out)

    def _read_with_retry(self, address: int, count: int) -> bytes:
        last: Exception | None = None
        for _ in range(3):
            try:
                return self._reader.read_flash(address, count)
            except BackupError as error:
                last = error
        raise BackupError(f"read at 0x{address:06x} failed after retries: {last}")


def open_harmony_one() -> tuple[object, HidWriteTransport]:
    import hid

    devices = hid.enumerate(HARMONY_ONE_VENDOR_ID, HARMONY_ONE_PRODUCT_ID)
    if len(devices) != 1:
        raise BackupError(f"expected exactly one Harmony One, found {len(devices)}")
    device = hid.device()
    device.open_path(devices[0]["path"])
    device.set_nonblocking(False)
    return device, HidWriteTransport(device)
