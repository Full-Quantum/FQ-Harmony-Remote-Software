"""Modal, acknowledgement-gated experimental activity upload workflow."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from harmony_one.activities import read_activities
from harmony_one.activity_upload import ActivityCandidate, prepare_activity_addition
from harmony_one.config_reader import parse_config
from harmony_one.flash_writer import WRITE_ENV_VAR, write_configuration
from harmony_one.harmony_usb import backup_connected_harmony_one
from harmony_one.harmony_usb_write import open_harmony_one


class ActivityUploadDialog:
    def __init__(self, parent: tk.Tk, baseline: bytes, directory: Path) -> None:
        names = [activity.name for activity in read_activities(baseline, parse_config(baseline))]
        if not names:
            raise ValueError("Read a backup containing an activity before adding one")
        self.window = tk.Toplevel(parent)
        self.window.title("Experimental activity addition")
        self.window.geometry("760x600")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.directory = directory
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.candidate: ActivityCandidate | None = None
        self.label = tk.StringVar(value="Test DVD")
        self.template = tk.StringVar(value="Watch a DVD" if "Watch a DVD" in names else names[0])
        form = ttk.Frame(self.window, padding=12)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="Adds an activity using devices already on this remote.").pack(anchor="w")
        ttk.Label(form, text="New menu name").pack(anchor="w", pady=(8, 0))
        self.name_entry = ttk.Entry(form, textvariable=self.label)
        self.name_entry.pack(fill="x")
        ttk.Label(form, text="Copy device targets and hard keys from").pack(anchor="w", pady=(8, 0))
        self.template_entry = ttk.Combobox(
            form, textvariable=self.template, values=names, state="readonly"
        )
        self.template_entry.pack(fill="x")
        ttk.Label(
            form, wraplength=720,
            text="Extra startup commands/delays are NOT copied. The template's startup title "
            "and working screen, including its touch commands, are reused. "
            "Structural uploads are experimental.",
        ).pack(anchor="w", pady=8)
        self.prepare_button = ttk.Button(form, text="Read fresh backup and prepare", command=self._prepare)
        self.prepare_button.pack(anchor="w")
        self.status = tk.StringVar(value="Nothing has been written to the remote.")
        ttk.Label(form, textvariable=self.status, wraplength=720).pack(anchor="w", pady=8)
        self.report = tk.Text(form, height=15, wrap="word", state="disabled")
        self.report.pack(fill="both", expand=True)
        self.write_button = ttk.Button(
            form, text="Upload inspected candidate...", command=self._write, state="disabled"
        )
        self.write_button.pack(anchor="e", pady=(8, 0))

    def _close(self) -> None:
        if self.busy:
            messagebox.showwarning(
                "Operation in progress", "Keep the remote connected until the operation ends.",
                parent=self.window,
            )
            return
        self.window.destroy()

    def _start(self, worker) -> None:
        self.busy = True
        self.prepare_button.configure(state="disabled")
        self.write_button.configure(state="disabled")
        self.name_entry.configure(state="disabled")
        self.template_entry.configure(state="disabled")
        threading.Thread(target=worker, name="HarmonyActivityUpload", daemon=False).start()
        self.window.after(100, self._poll)

    def _prepare(self) -> None:
        label, template = self.label.get(), self.template.get()
        destination = self.directory / f"Activity-{datetime.now():%Y%m%d-%H%M%S-%f}"
        self.candidate = None
        self.status.set("Reading a fresh backup, then composing offline. No flash erase or upload.")

        def worker() -> None:
            try:
                destination.mkdir(parents=True, exist_ok=False)
                baseline = backup_connected_harmony_one(destination / "before.EZHex")
                candidate = prepare_activity_addition(baseline, label, template, destination)
                self.events.put(("prepared", candidate))
            except Exception as error:
                self.events.put(("error", str(error)))

        self._start(worker)

    def _write(self) -> None:
        candidate = self.candidate
        if candidate is None:
            return
        if not messagebox.askyesno(
            "Experimental structural upload",
            f"Write {candidate.label!r} to the connected spare Harmony One?\n\n"
            "This erases and rewrites the configuration. A failure after erasure may leave "
            "the remote unusable; recovery is not guaranteed. Keep USB connected and the "
            "battery charged. Extra startup sends/delays are not copied; the template's "
            "working screen and its touch commands are reused.\n\n"
            f"Backup and report: {candidate.path.parent}\n\n"
            "Proceed only after inspecting the report. The live backup must still match.",
            parent=self.window,
        ):
            return
        self.status.set("Uploading and verifying. DO NOT disconnect the remote.")

        def worker() -> None:
            previous = os.environ.get(WRITE_ENV_VAR)
            device = None
            try:
                os.environ[WRITE_ENV_VAR] = "1"
                device, transport = open_harmony_one()
                transport.flush()
                write_configuration(
                    transport, candidate.image, candidate.baseline,
                    lambda done, total: self.events.put(("progress", (done, total))),
                )
                self.events.put(("written", candidate.label))
            except Exception as error:
                self.events.put(("error", str(error)))
            finally:
                if device is not None:
                    device.close()
                if previous is None:
                    os.environ.pop(WRITE_ENV_VAR, None)
                else:
                    os.environ[WRITE_ENV_VAR] = previous

        self._start(worker)

    def _poll(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                done, total = payload
                self.status.set(f"Written {done:,}/{total:,} bytes; keep USB connected.")
                continue
            self.busy = False
            if event == "prepared":
                self.candidate = payload
                self.report.configure(state="normal")
                self.report.delete("1.0", "end")
                self.report.insert("end", self.candidate.report)
                self.report.configure(state="disabled")
                self.status.set(f"Candidate saved to {self.candidate.path}. Inspect before uploading.")
                self.write_button.configure(state="normal")
            elif event == "written":
                self.candidate = None
                self.status.set(f"{payload}: flash read-back matched; remote restarting. Check its menu.")
                messagebox.showinfo(
                    "Upload verified",
                    "Flash read-back matched and reset was sent. Check the remote's activity menu "
                    "and operation; this does not verify infrared behavior.",
                    parent=self.window,
                )
            else:
                self.candidate = None
                self.status.set("Operation failed. See error details; do not assume the remote is unchanged.")
                messagebox.showerror("Activity operation failed", str(payload), parent=self.window)
            self.prepare_button.configure(state="normal")
            self.name_entry.configure(state="normal")
            self.template_entry.configure(state="readonly")
        if self.busy:
            self.window.after(100, self._poll)
