"""Read-only Harmony One configuration backup over the USB HID protocol."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Protocol


HARMONY_ONE_VENDOR_ID = 0x046D
HARMONY_ONE_PRODUCT_ID = 0xC121
CONFIG_FLASH_BASE = 0x040000
CONFIG_FLASH_LIMIT = 0x400000
CONFIG_HEADER_MAGIC = b"GSPM"
CONFIG_END_MARKER = b"PTYY"
TRAILER_CHECKSUM_SEED = 0x4321
TRAILER_CHECKSUM_OFFSET = 6
REPORT_SIZE = 64
READ_FLASH = 0x50
ACK = 0xF0
FLASH_DATA = 0x60
FLASH_CHUNK_DATA = 62
FLASH_SEQUENCE_STEP = 0x11
HEADER_PROBE_BYTES = FLASH_CHUNK_DATA
READ_WINDOW_BYTES = 16_368
HID_READ_TIMEOUT_MS = 1_000
HID_IDLE_POLLS = 3


class BackupError(Exception):
    """A read-only remote backup could not be completed and validated."""


class ReadSilenceError(BackupError):
    """The remote produced no response to a flash read."""


class ReadTransport(Protocol):
    def read_flash(self, address: int, count: int) -> bytes: ...


ProgressCallback = Callable[[int, int], None]


def _read_report_length(nibble: int) -> int:
    if nibble == 0x8:
        return 15
    if nibble == 0x9:
        return 31
    if nibble == 0xA:
        return 63
    return nibble


def _encode_read_flash(address: int, count: int) -> bytes:
    if not 0 <= address <= 0xFFFFFF:
        raise BackupError(f"flash address 0x{address:x} does not fit in 24 bits")
    if not 1 <= count <= 0xFFFF:
        raise BackupError(f"flash byte count {count} is outside the valid range")
    report = bytearray(REPORT_SIZE)
    report[0] = READ_FLASH | 5
    report[1:6] = address.to_bytes(3, "big") + count.to_bytes(2, "big")
    return bytes(report)


class HarmonyOneHidTransport:
    """HID adapter whose only outbound protocol operation is READ_FLASH."""

    def __init__(self, device: object) -> None:
        self._device = device

    def _send_read_flash(self, address: int, count: int) -> None:
        report = _encode_read_flash(address, count)
        written = self._device.write(b"\x00" + report)
        if written not in (REPORT_SIZE, REPORT_SIZE + 1):
            raise BackupError(f"HID write sent {written} bytes; expected a 64-byte report")

    def _read_report(self) -> bytes | None:
        data = self._device.read(REPORT_SIZE + 1, HID_READ_TIMEOUT_MS)
        if not data:
            return None
        report = bytes(data)
        if len(report) == REPORT_SIZE + 1 and report[0] == 0:
            report = report[1:]
        if len(report) != REPORT_SIZE:
            raise BackupError(f"HID returned a {len(report)}-byte report; expected 64 bytes")
        return report

    def _drain_until_ack(self) -> None:
        idle = 0
        while idle < HID_IDLE_POLLS:
            try:
                report = self._read_report()
            except Exception:
                # Draining is cleanup; preserve the transfer error that caused it.
                return
            if report is None:
                idle += 1
                continue
            idle = 0
            if report[0] == ACK and report[1] == READ_FLASH:
                return

    def _read_flash_once(self, address: int, count: int) -> bytes:
        self._send_read_flash(address, count)
        result = bytearray(count)
        filled = 0
        idle = 0
        sequence: int | None = None
        finished = False
        received_report = False
        try:
            while not finished and idle < HID_IDLE_POLLS:
                report = self._read_report()
                if report is None:
                    idle += 1
                    continue
                received_report = True
                idle = 0
                if report[0] == ACK:
                    if report[1] != READ_FLASH:
                        raise BackupError(
                            f"flash read ended with acknowledgment for command 0x{report[1]:02x}"
                        )
                    finished = True
                    break
                if (report[0] & 0xF0) != FLASH_DATA:
                    raise BackupError(
                        f"flash read answered with unexpected report code 0x{report[0]:02x}"
                    )

                payload_length = _read_report_length(report[0] & 0x0F)
                if payload_length < 2:
                    raise BackupError("flash data report has no data after its sequence byte")
                sequence_byte = report[1]
                expected_sequence = (
                    0x01 if sequence is None else (sequence + FLASH_SEQUENCE_STEP) & 0xFF
                )
                if sequence_byte != expected_sequence:
                    raise BackupError(
                        f"flash chunk out of sequence: expected 0x{expected_sequence:02x}, "
                        f"received 0x{sequence_byte:02x} after {filled} bytes"
                    )
                sequence = sequence_byte
                chunk = report[2 : 1 + payload_length]
                if len(chunk) > count - filled:
                    raise BackupError(
                        f"flash read returned more than requested ({filled + len(chunk)} > {count})"
                    )
                result[filled : filled + len(chunk)] = chunk
                filled += len(chunk)

            if filled != count:
                if not received_report:
                    raise ReadSilenceError(f"no response to READ_FLASH at 0x{address:06x}")
                raise BackupError(f"flash read returned {filled} of {count} requested bytes")
            if not finished:
                raise BackupError(f"flash read got {count} bytes but no completion acknowledgment")
            return bytes(result)
        except Exception:
            self._drain_until_ack()
            raise

    def read_flash(self, address: int, count: int) -> bytes:
        return self._read_flash_once(address, count)


def trailer_checksum(blob: bytes) -> int:
    if len(blob) < TRAILER_CHECKSUM_OFFSET:
        raise BackupError("configuration is too short to contain a trailer checksum")
    end = len(blob) - TRAILER_CHECKSUM_OFFSET
    accumulator = TRAILER_CHECKSUM_SEED
    for offset in range(0, end - 1, 2):
        accumulator ^= blob[offset] | (blob[offset + 1] << 8)
    return accumulator


def _configuration_length(header: bytes) -> int:
    if len(header) != HEADER_PROBE_BYTES:
        raise BackupError(f"configuration header read has {len(header)} bytes, expected 62")
    if header[:4] != CONFIG_HEADER_MAGIC:
        raise BackupError(
            f"remote config does not start with GSPM (found {header[:4]!r}); no backup saved"
        )
    end_address = int.from_bytes(header[4:8], "little")
    if (
        not CONFIG_FLASH_BASE <= end_address < CONFIG_FLASH_LIMIT
        or end_address + len(CONFIG_END_MARKER) > CONFIG_FLASH_LIMIT
    ):
        raise BackupError(
            f"GSPM end address 0x{end_address:06x} is outside Harmony One config flash"
        )
    length = end_address - CONFIG_FLASH_BASE + len(CONFIG_END_MARKER)
    if length <= HEADER_PROBE_BYTES + TRAILER_CHECKSUM_OFFSET:
        raise BackupError(f"GSPM config length {length} is too short")
    return length


def _wire_count_for(remaining: int) -> int:
    if remaining <= 0:
        raise BackupError("internal error: requested an empty flash window")
    if remaining <= READ_WINDOW_BYTES:
        # Full responses carry 62 data bytes. Rounding only the final read upward avoids
        # unrepresentable short HID frame lengths; bytes beyond the validated container are
        # discarded and are never written to the backup.
        return ((remaining + FLASH_CHUNK_DATA - 1) // FLASH_CHUNK_DATA) * FLASH_CHUNK_DATA
    return READ_WINDOW_BYTES


def read_harmony_one_config(
    transport: ReadTransport,
    on_progress: ProgressCallback | None = None,
) -> bytes:
    """Read and validate one Harmony One GSPM config without sending any write command."""
    try:
        header = transport.read_flash(CONFIG_FLASH_BASE, HEADER_PROBE_BYTES)
    except ReadSilenceError:
        # A Harmony One can drop the first read after HID open. Retrying this idempotent read is
        # limited to the first command; all later windows fail explicitly.
        header = transport.read_flash(CONFIG_FLASH_BASE, HEADER_PROBE_BYTES)
    length = _configuration_length(header)
    blob = bytearray(length)
    blob[: len(header)] = header
    if on_progress:
        on_progress(len(header), length)

    offset = len(header)
    while offset < length:
        remaining = length - offset
        requested = _wire_count_for(remaining)
        if CONFIG_FLASH_BASE + offset + requested > CONFIG_FLASH_LIMIT:
            raise BackupError("rounded flash read would cross the Harmony One flash boundary")
        part = transport.read_flash(CONFIG_FLASH_BASE + offset, requested)
        if len(part) != requested:
            raise BackupError(
                f"flash read at offset 0x{offset:x} returned {len(part)} of {requested} bytes"
            )
        copied = min(remaining, requested)
        blob[offset : offset + copied] = part[:copied]
        offset += copied
        if on_progress:
            on_progress(offset, length)

    marker = bytes(blob[-len(CONFIG_END_MARKER) :])
    if marker != CONFIG_END_MARKER:
        raise BackupError(
            f"config end marker is {marker!r}, expected PTYY; no backup saved"
        )
    stored = int.from_bytes(blob[-TRAILER_CHECKSUM_OFFSET : -TRAILER_CHECKSUM_OFFSET + 2], "little")
    computed = trailer_checksum(bytes(blob))
    if stored != computed:
        raise BackupError(
            f"config trailer checksum 0x{stored:04x} does not match 0x{computed:04x}; "
            "no backup saved"
        )
    return bytes(blob)


def _save_validated_backup(destination: Path, blob: bytes, overwrite: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent, delete=False,
        ) as temp:
            temp_name = temp.name
            temp.write(blob)
            temp.flush()
            os.fsync(temp.fileno())
        if overwrite:
            os.replace(temp_name, destination)
        else:
            os.rename(temp_name, destination)
    except FileExistsError as error:
        raise BackupError(f"backup already exists and was not replaced: {destination}") from error
    except OSError as error:
        raise BackupError(f"could not save validated backup to {destination}: {error}") from error
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_and_save_backup(
    transport: ReadTransport,
    destination: Path,
    on_progress: ProgressCallback | None,
    overwrite: bool,
) -> bytes:
    blob = read_harmony_one_config(transport, on_progress)
    _save_validated_backup(destination, blob, overwrite)
    return blob


def backup_connected_harmony_one(
    destination: Path,
    on_progress: ProgressCallback | None = None,
    overwrite: bool = False,
    hid_module: object | None = None,
) -> bytes:
    """Back up a uniquely enumerated Harmony One and save only after integrity checks pass."""
    if os.name != "nt":
        raise BackupError("Harmony One USB backup is currently supported on Windows only")
    if hid_module is None:
        try:
            import hid as hid_module
        except ImportError as error:
            raise BackupError(
                "The USB HID dependency is missing. Install it with "
                "'python -m pip install -r requirements.txt'."
            ) from error

    try:
        devices = hid_module.enumerate(HARMONY_ONE_VENDOR_ID, HARMONY_ONE_PRODUCT_ID)
    except Exception as error:
        raise BackupError(f"Could not enumerate Harmony One HID devices: {error}") from error
    if not devices:
        raise BackupError("No connected Logitech Harmony One (VID_046D&PID_C121) was found")
    if len(devices) != 1:
        raise BackupError(
            f"Found {len(devices)} Harmony One HID interfaces; refusing an ambiguous USB target"
        )
    if not isinstance(devices[0], dict):
        raise BackupError("Harmony One HID enumeration returned an invalid device record")
    path = devices[0].get("path")
    if not path:
        raise BackupError("The Harmony One HID interface did not provide an openable device path")

    device = None
    failed = False
    try:
        device = hid_module.device()
        device.open_path(path)
        device.set_nonblocking(False)
        blob = _read_and_save_backup(
            HarmonyOneHidTransport(device), Path(destination), on_progress, overwrite
        )
    except BackupError:
        failed = True
        raise
    except Exception as error:
        failed = True
        raise BackupError(f"Harmony One USB read failed: {error}") from error
    finally:
        if device is not None:
            try:
                device.close()
            except Exception as error:
                if not failed:
                    raise BackupError(
                        f"Backup was saved to {destination}, but the USB handle could not "
                        f"be closed: {error}"
                    ) from error

    return blob
