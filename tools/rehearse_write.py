"""Rehearsal: write a remote's own backup back unchanged, then verify by read-back.

Usage: python tools/rehearse_write.py <current-backup.EZHex> [new-config.EZHex]
With one argument the backup is written back unchanged (a rehearsal); with two, the second
file is written. Requires HARMONY_ENABLE_WRITES=1. Refuses unless the backup equals the live flash.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harmony_one.flash_writer import write_configuration  # noqa: E402
from harmony_one.harmony_usb_write import open_harmony_one  # noqa: E402
from harmony_one.harmony_version import read_version  # noqa: E402


def main() -> int:
    backup = Path(sys.argv[1]).read_bytes()
    new_config = Path(sys.argv[2]).read_bytes() if len(sys.argv) > 2 else backup
    device, transport = open_harmony_one()
    try:
        print("version:", " | ".join(read_version(device).describe()[:2]), flush=True)
        transport.flush()
        started = time.time()
        last = [0.0]

        def progress(done, total):
            if time.time() - last[0] > 15 or done == total:
                last[0] = time.time()
                print(f"written {done:,}/{total:,} ({done * 100 / total:.1f}%) after {time.time() - started:.0f}s", flush=True)

        write_configuration(transport, new_config, backup, progress)
        print("write + read-back verify OK; remote restarting", flush=True)
    finally:
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
