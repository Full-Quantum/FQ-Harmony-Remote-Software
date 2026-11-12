"""Clock synchronization page for a connected Harmony One."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from harmony_one.clock import prepare_clock_candidate
from harmony_one.flash_writer import WRITE_ENV_VAR, write_configuration
from harmony_one.harmony_usb import BackupError, _save_validated_backup, read_harmony_one_config
from harmony_one.harmony_usb_write import open_harmony_one
from harmony_one.save_restore import SaveRestoreError, validate_source_save


LOCAL_TIMEZONE = "Computer local time"


class ClockTab:
    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.writing = False
        self.allow_writes = tk.BooleanVar(value=False)
        self.timezone = tk.StringVar(value=LOCAL_TIMEZONE)
        self.pc_time = tk.StringVar()
        self.timezone_error = tk.StringVar(value="")
        self.status = tk.StringVar(value="No clock operation has run.")
        self.source_blob: bytes | None = None
        self.source_path: Path | None = None
        self.source_info = tk.StringVar(value="No backup copy is loaded.")
        self._timezone_options: dict[str, str] = {}
        self.timezones: tuple[str, ...] = ()
        self._refresh_timezone_options()
        self._build()
        self._refresh_pc_time()

    def _build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        intro = ttk.LabelFrame(self.frame, text="Remote display clock (top right)", padding=12)
        intro.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            intro,
            text=(
                "This sets the clock shown at the top right of the Harmony One screen to your "
                "selected time zone's current date and time. The remote stores only the resulting "
                "date and wall-clock time; it does not store a time zone or daylight-saving rule. "
                "Select another zone or type an IANA zone such as Europe/London or "
                "America/Los_Angeles. The zone list shows each zone's current UTC offset."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        timezone_row = ttk.Frame(intro)
        timezone_row.pack(anchor="w", fill="x", pady=(10, 0))
        ttk.Label(timezone_row, text="Time zone:").pack(side="left")
        self.timezone_selector = ttk.Combobox(
            timezone_row,
            textvariable=self.timezone,
            values=self.timezones,
            state="normal",
            width=38,
        )
        self.timezone_selector.pack(side="left", padx=(8, 0))
        self.timezone_selector.bind("<<ComboboxSelected>>", self._refresh_pc_time)
        self.timezone_selector.bind("<Button-1>", self._refresh_timezone_options)
        self.timezone_selector.bind("<FocusOut>", self._refresh_pc_time)
        self.timezone_selector.bind("<KeyRelease>", self._refresh_pc_time)
        ttk.Label(
            timezone_row,
            textvariable=self.timezone_error,
            foreground="#a12622",
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            intro,
            text="Current time in the selected zone:",
            font=("Segoe UI", 10, "bold"),
        ).pack(
            anchor="w", pady=(12, 0)
        )
        ttk.Label(intro, textvariable=self.pc_time, font=("Consolas", 18)).pack(
            anchor="w", pady=(2, 8)
        )
        ttk.Label(
            intro,
            text=(
                "Setting this display clock rewrites the remote configuration (typically a few "
                "minutes). The operation takes a fresh backup first, verifies the live "
                "configuration still matches it, then verifies the complete write before "
                "restarting. Read-back verifies saved data; it cannot confirm the screen itself."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")

        offline = ttk.LabelFrame(
            self.frame, text="Prepare an offline clock candidate from a backup copy", padding=10
        )
        offline.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(
            offline,
            text=(
                "Use the backup loaded from Browse Contents or the Remote Data import workflow "
                "to make a separate .EZHex candidate. This does not write to USB or change the "
                "loaded backup. The candidate can be selected later in Backups & Transfer."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(offline, textvariable=self.source_info, wraplength=850).pack(
            anchor="w", pady=(6, 4)
        )
        self.offline_candidate_button = ttk.Button(
            offline,
            text="Save clock candidate from loaded backup...",
            command=self._save_offline_candidate,
            state="disabled",
        )
        self.offline_candidate_button.pack(anchor="w")

        writes = ttk.LabelFrame(self.frame, text="Write to the connected remote", padding=10)
        writes.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(
            writes,
            variable=self.allow_writes,
            command=self._update_button,
            text=(
                "I understand this rewrites the remote's configuration and will keep the "
                "remote connected and powered until verification finishes."
            ),
        ).pack(anchor="w")
        self.set_button = ttk.Button(
            writes,
            text="Set top-right remote clock to selected time zone",
            command=self._set_clock,
            state="disabled",
        )
        self.set_button.pack(anchor="w", pady=(8, 0))
        self.progress = ttk.Progressbar(writes, maximum=100)
        self.progress.pack(fill="x", pady=(10, 0))
        ttk.Label(writes, textvariable=self.status, wraplength=850).pack(
            anchor="w", pady=(6, 0)
        )

    def load(self, blob: bytes, backup_path: Path | None) -> None:
        self.source_blob = blob
        self.source_path = backup_path
        self.source_info.set(
            "Loaded backup: "
            + (str(backup_path) if backup_path is not None else "in-memory backup")
        )
        self._update_button()

    @staticmethod
    def _time_for_timezone(selected: str) -> tuple[datetime, str]:
        if selected == LOCAL_TIMEZONE:
            now = datetime.now().astimezone()
            return now.replace(tzinfo=None), f"{now.tzname() or LOCAL_TIMEZONE} (computer local)"
        try:
            zone = ZoneInfo(selected)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError(
                f"Unknown time zone {selected!r}. Choose a listed zone or enter a valid IANA name."
            ) from error
        now = datetime.now(zone)
        return now.replace(tzinfo=None), f"{selected} ({now:%Z}, UTC{now:%z})"

    @staticmethod
    def _utc_offset(offset: timedelta | None) -> str:
        if offset is None:
            raise ValueError("The time zone did not provide a UTC offset.")
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        return f"UTC{sign}{hours:02d}:{minutes:02d}"

    def _refresh_timezone_options(self, _event: Any = None) -> None:
        if _event is None:
            self.frame.after(60000, self._refresh_timezone_options)
        selected = self.timezone.get().strip()
        selected_zone = self._timezone_options.get(selected, selected)
        now = datetime.now().astimezone()
        local_offset = self._utc_offset(now.utcoffset())
        options = {f"{LOCAL_TIMEZONE} ({local_offset})": LOCAL_TIMEZONE}
        for name in sorted(available_timezones()):
            zone_now = datetime.now(ZoneInfo(name))
            offset = self._utc_offset(zone_now.utcoffset())
            options[f"{name} ({offset})"] = name
        self._timezone_options = options
        self.timezones = tuple(options)
        if hasattr(self, "timezone_selector"):
            self.timezone_selector.configure(values=self.timezones)
        updated_selection = next(
            (label for label, name in options.items() if name == selected_zone),
            selected,
        )
        self.timezone.set(updated_selection)

    def _selected_time(self) -> tuple[datetime, str]:
        selected = self.timezone.get().strip()
        timezone_id = self._timezone_options.get(selected, selected)
        return self._time_for_timezone(timezone_id)

    def _refresh_pc_time(self, _event: Any = None) -> None:
        if _event is None:
            self.frame.after(1000, self._refresh_pc_time)
        try:
            now, zone = self._selected_time()
        except ValueError as error:
            self.pc_time.set("Select a valid time zone.")
            self.timezone_error.set(str(error))
            self._update_button()
            return
        self.timezone_error.set("")
        self.pc_time.set(f"{now:%A, %B %d, %Y  %H:%M:%S} ({zone})")
        self._update_button()

    def _update_button(self) -> None:
        try:
            self._selected_time()
            timezone_is_valid = True
        except ValueError:
            timezone_is_valid = False
        self.set_button.configure(
            state=(
                "normal"
                if self.allow_writes.get() and not self.writing and timezone_is_valid
                else "disabled"
            )
        )
        self.offline_candidate_button.configure(
            state=(
                "normal"
                if self.source_blob is not None and not self.writing
                else "disabled"
            )
        )

    def _save_offline_candidate(self) -> None:
        if self.source_blob is None:
            messagebox.showinfo(
                "Load a backup first",
                "Open a backup in Browse Contents or import one into the Remote Data editor first.",
                parent=self.app.root,
            )
            return
        try:
            stamp, zone = self._selected_time()
            candidate = prepare_clock_candidate(self.source_blob, stamp.replace(microsecond=0))
        except ValueError as error:
            messagebox.showerror("Could not prepare clock candidate", str(error), parent=self.app.root)
            return

        source_path = self.source_path
        initial_directory = source_path.parent if source_path is not None else self._backup_directory()
        initial_name = (
            f"{source_path.stem}-clock-candidate.EZHex"
            if source_path is not None
            else "HarmonyOne-clock-candidate.EZHex"
        )
        chosen = filedialog.asksaveasfilename(
            parent=self.app.root,
            title="Save offline clock candidate",
            initialdir=str(initial_directory),
            initialfile=initial_name,
            defaultextension=".EZHex",
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        destination = Path(chosen)
        if source_path is not None and destination.resolve() == source_path.resolve():
            messagebox.showwarning(
                "Keep the source backup",
                "Choose a different .EZHex path so the loaded backup remains unchanged.",
                parent=self.app.root,
            )
            return
        try:
            validate_source_save(candidate.image, "Clock")
            _save_validated_backup(destination, candidate.image, overwrite=False)
        except (BackupError, SaveRestoreError, OSError) as error:
            messagebox.showerror(
                "Could not save clock candidate", str(error), parent=self.app.root
            )
            return
        save_restore = getattr(self.app, "save_restore", None)
        if save_restore is not None:
            save_restore._refresh_saved_backups(destination)
        self.status.set(
            f"Offline clock candidate saved to {destination} for {zone}. "
            "The loaded backup and connected remote were not changed."
        )

    def _set_clock(self) -> None:
        if self.writing:
            return
        if self.app._backup_running or getattr(self.app.slideshow, "writing", False):
            messagebox.showwarning(
                "Remote busy",
                "Wait for the current USB operation to finish before setting the clock.",
                parent=self.app.root,
            )
            return
        if not self.allow_writes.get():
            messagebox.showwarning(
                "Writing is off",
                "Acknowledge the configuration rewrite first.",
                parent=self.app.root,
            )
            return
        selected_timezone = self.timezone.get().strip()
        selected_timezone = self._timezone_options.get(selected_timezone, selected_timezone)
        try:
            _selected_time, selected_zone = self._time_for_timezone(selected_timezone)
        except ValueError as error:
            messagebox.showerror(
                "Invalid time zone",
                str(error),
                parent=self.app.root,
            )
            return
        if not messagebox.askyesno(
            "Set Harmony One clock",
            f"Set the connected Harmony One clock using the selected zone's current "
            f"time ({selected_zone}) when the write begins?\n\n"
            f"Preview: {self.pc_time.get()}\n\n"
            "The remote stores no time zone or future daylight-saving rule.\n\n"
            "Confirm that the intended remote is connected by USB, charged and ready. "
            "Keep it connected and powered until the full configuration rewrite and "
            "read-back verification finish.",
            parent=self.app.root,
        ):
            return

        self.writing = True
        self._update_button()
        self.progress.configure(value=0)
        self.status.set("Reading and validating the connected remote before writing.")

        def worker() -> None:
            previous = os.environ.get(WRITE_ENV_VAR)
            device = None
            result: tuple[bytes, Path, Path, datetime, str] | None = None
            failure: str | None = None
            try:
                device, transport = open_harmony_one()
                transport.flush()
                baseline = read_harmony_one_config(
                    transport,
                    lambda done, total: self.events.put(
                        ("progress", ("Reading backup", done, total))
                    ),
                )
                backup_dir = self._backup_directory()
                backup_dir.mkdir(parents=True, exist_ok=True)
                file_stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                baseline_path = backup_dir / f"HarmonyOne-clock-before-{file_stamp}.EZHex"
                candidate_path = backup_dir / f"HarmonyOne-clock-candidate-{file_stamp}.EZHex"
                _save_validated_backup(baseline_path, baseline, overwrite=False)
                stamp, write_zone = self._time_for_timezone(selected_timezone)
                stamp = stamp.replace(microsecond=0)
                if stamp.year < 2000 or stamp.year > 2255:
                    raise ValueError(
                        "The selected time zone's current date is outside the remote's "
                        "supported year range (2000 through 2255)."
                    )
                candidate = prepare_clock_candidate(baseline, stamp)
                _save_validated_backup(candidate_path, candidate.image, overwrite=False)
                os.environ[WRITE_ENV_VAR] = "1"
                write_configuration(
                    transport,
                    candidate.image,
                    baseline,
                    lambda done, total: self.events.put(
                        ("progress", ("Writing and verifying", done, total))
                    ),
                )
                result = (
                    candidate.image,
                    baseline_path,
                    candidate_path,
                    candidate.local_time,
                    write_zone,
                )
            except Exception as error:
                failure = str(error)
                if "baseline_path" in locals():
                    failure += f"\nFresh backup: {baseline_path}"
                if "candidate_path" in locals():
                    failure += f"\nClock candidate: {candidate_path}"
            finally:
                if previous is None:
                    os.environ.pop(WRITE_ENV_VAR, None)
                else:
                    os.environ[WRITE_ENV_VAR] = previous
                if device is not None:
                    try:
                        device.close()
                    except Exception as error:
                        close_detail = f"Could not close the USB connection cleanly: {error}"
                        failure = f"{failure}\n{close_detail}" if failure else close_detail
            if failure is not None:
                self.events.put(("error", failure))
            elif result is not None:
                self.events.put(("complete", result))

        threading.Thread(target=worker, name="HarmonyOneClockWrite", daemon=True).start()
        self.frame.after(150, self._poll)

    @staticmethod
    def _backup_directory() -> Path:
        from harmony_one.app import backup_directory

        return backup_directory()

    def _poll(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                phase, done, total = payload
                self.progress.configure(value=done * 100 / total if total else 0)
                self.status.set(f"{phase}: {done:,} of {total:,} bytes.")
            elif event == "complete":
                blob, baseline_path, candidate_path, stamp, zone = payload
                self.writing = False
                self.progress.configure(value=100)
                self.app.remote_blob = blob
                self.app.remote_backup_path = candidate_path
                self.load(blob, candidate_path)
                self.app.slideshow.load(blob)
                self.app.screen_styles.load(blob, candidate_path)
                self.app.backup_browser.load(blob, candidate_path)
                self.status.set(
                    f"Clock configuration written and read-back verified for "
                    f"{stamp:%Y-%m-%d %H:%M:%S} in {zone}. The remote is restarting; "
                    f"check its top-right clock. Verified backup: {baseline_path}."
                )
                messagebox.showinfo(
                    "Remote clock configuration verified",
                    self.status.get(),
                    parent=self.app.root,
                )
            elif event == "error":
                self.writing = False
                self.status.set(f"Clock update failed: {payload}")
                if str(payload).startswith("expected exactly one Harmony One, found 0"):
                    self.app._handle_missing_remote(
                        "The Harmony One HID interface is not available. "
                        + str(payload)
                    )
                else:
                    messagebox.showerror(
                        "Could not set the remote clock",
                        payload,
                        parent=self.app.root,
                    )
        self._update_button()
        if self.writing:
            self.frame.after(150, self._poll)
