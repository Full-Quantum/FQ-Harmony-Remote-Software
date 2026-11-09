"""Preview and replace pictures in a detected remote picture cycle."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from harmony_one.flash_writer import WRITE_ENV_VAR, write_configuration
from harmony_one.harmony_usb_write import open_harmony_one
from harmony_one.pictures import decode_picture, write_png
from harmony_one.slideshow import (
    PictureReplaceError,
    encode_raw_picture,
    fit_cover,
    full_screen_pictures,
    locate_bank,
    replace_with_encoded,
)
from harmony_one.slideshow_cycle import SlideshowCycleError, read_picture_cycle


class SlideshowTab:
    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.base_blob: bytes | None = None
        self.blob: bytes | None = None
        self.entries: list[tuple[int, Any]] = []
        self.cycle_note = ""
        self.cycle_info = tk.StringVar(value="Read or import a remote backup to find available slots.")
        self.preview: tk.PhotoImage | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.writing = False
        self.allow_writes = tk.BooleanVar(value=False)
        self._build()

    def _build(self) -> None:
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(1, weight=1)
        description = ttk.Frame(self.frame)
        description.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(
            description,
            wraplength=860,
            text=(
                "Only detected cycle pictures are shown, in playback order. Replace one image per slot "
                "(PNG or GIF, fitted to 176 x 220). The first slot may be a charging/system screen; "
                "pictures may also appear on other screens. Screen sleep can interrupt playback."
            ),
        ).pack(anchor="w")
        ttk.Label(description, textvariable=self.cycle_info, wraplength=860).pack(anchor="w", pady=(4, 0))
        self.listbox = tk.Listbox(self.frame, width=40, exportselection=False)
        self.listbox.grid(row=1, column=0, sticky="ns")
        self.listbox.bind("<<ListboxSelect>>", self._selected)
        self.canvas = tk.Label(self.frame, background="#222222", width=176, height=220)
        self.canvas.grid(row=1, column=1, sticky="nw", padx=12)

        buttons = ttk.Frame(self.frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(buttons, text="Replace with image file...", command=self._replace).pack(side="left")
        ttk.Button(buttons, text="Export as PNG...", command=self._export).pack(side="left", padx=4)
        ttk.Button(buttons, text="Discard unwritten changes", command=self._discard).pack(side="left")

        writes = ttk.LabelFrame(self.frame, text="Write to the connected remote", padding=8)
        writes.grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(
            writes,
            variable=self.allow_writes,
            text=(
                "I understand this erases and rewrites the remote's configuration (about 3 minutes) "
                "and the remote must stay connected."
            ),
        ).pack(anchor="w")
        self.write_button = ttk.Button(writes, text="Write slideshow changes", command=self._write)
        self.write_button.pack(anchor="w", pady=(6, 0))
        self.status = tk.StringVar(value="No configuration loaded.")
        ttk.Label(writes, textvariable=self.status, wraplength=840).pack(anchor="w", pady=(6, 0))
        self.progress = ttk.Progressbar(writes, maximum=100)
        self.progress.pack(fill="x", pady=(6, 0))

    def load(self, blob: bytes) -> None:
        self.base_blob = blob
        self.blob = blob
        self._refresh()
        self.status.set(f"{self.cycle_note} No unwritten changes.")

    def _refresh(self) -> None:
        selected = self.listbox.curselection()
        pictures = full_screen_pictures(self.blob) if self.blob else []
        labels: list[str] = []
        self.entries = []
        if self.blob:
            try:
                cycle = read_picture_cycle(self.blob, pictures)
                if len({screen.picture.offset for screen in cycle}) != len(cycle):
                    raise SlideshowCycleError(
                        "Cycle screens share a picture, so independent slots cannot be provided."
                    )
            except SlideshowCycleError as error:
                self.cycle_note = f"Playback order unavailable: {error} Image replacement is disabled."
            else:
                for position, screen in enumerate(cycle, 1):
                    self.entries.append((screen.picture_index, screen.picture))
                    labels.append(f"Slot {position} ({screen.seconds}s timer)")
                self.cycle_note = (
                    f"Maximum: {len(cycle)} cycle image slots for this backup. "
                    "Replacing a slot does not add another slot."
                )
        self.cycle_info.set(self.cycle_note)
        self.listbox.delete(0, "end")
        for label in labels:
            self.listbox.insert("end", label)
        if self.entries:
            self.listbox.selection_set(min(selected[0], len(self.entries) - 1) if selected else 0)
            self._selected()
        else:
            self.preview = None
            self.canvas.configure(image="")

    def _current(self) -> tuple[int, Any] | None:
        selected = self.listbox.curselection()
        return self.entries[selected[0]] if selected and self.blob else None

    def _selected(self, _event: object = None) -> None:
        current = self._current()
        if not current:
            return
        pixels = decode_picture(self.blob, current[1])
        rows = ["{" + " ".join("#%02x%02x%02x" % (p[:3] if p else (0, 0, 0)) for p in row) + "}" for row in pixels]
        image = tk.PhotoImage(width=len(pixels[0]), height=len(pixels))
        image.put(" ".join(rows))
        self.preview = image
        self.canvas.configure(image=image, width=image.width(), height=image.height())

    def _replace(self) -> None:
        current = self._current()
        if not current:
            messagebox.showinfo(
                "Slideshow", "Select a detected cycle slot after loading a supported backup.",
                parent=self.app.root,
            )
            return
        if not messagebox.askyesno(
            "Picture may be shared",
            "This picture may also appear on charging, system, or activity screens. "
            "Replacing it changes every use of this picture. Continue?",
            parent=self.app.root,
        ):
            return
        chosen = filedialog.askopenfilename(
            parent=self.app.root,
            title="Choose a PNG or GIF image",
            filetypes=[("PNG or GIF", "*.png *.gif"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            source = tk.PhotoImage(file=chosen)
            pixels = fit_cover(
                source.width(), source.height(), lambda x, y: tuple(source.get(x, y))[:3]
            )
            self.blob = replace_with_encoded(self.blob, current[1], encode_raw_picture(pixels))
        except (tk.TclError, PictureReplaceError) as error:
            messagebox.showerror("Could not use that image", str(error), parent=self.app.root)
            return
        self._refresh()
        self.status.set("Picture replaced in memory. Nothing has been sent to the remote yet.")

    def _export(self) -> None:
        current = self._current()
        if not current:
            return
        path = filedialog.asksaveasfilename(
            parent=self.app.root, defaultextension=".png", filetypes=[("PNG", "*.png")]
        )
        if path:
            write_png(Path(path), decode_picture(self.blob, current[1]))

    def _discard(self) -> None:
        if self.base_blob:
            self.load(self.base_blob)

    def _write(self) -> None:
        if self.writing:
            return
        if not self.blob or not self.base_blob:
            messagebox.showinfo("Slideshow", "Read the remote first.", parent=self.app.root)
            return
        if not self.entries:
            messagebox.showwarning(
                "Slideshow", "No supported picture cycle is available; writing is disabled.",
                parent=self.app.root,
            )
            return
        if self.blob == self.base_blob:
            messagebox.showinfo("Slideshow", "There are no changes to write.", parent=self.app.root)
            return
        if not self.allow_writes.get():
            messagebox.showwarning(
                "Writing is off", "Tick the acknowledgement box first.", parent=self.app.root
            )
            return
        if not messagebox.askyesno(
            "Write to remote",
            "Write the changed configuration to the connected remote?\n\nThe write is refused "
            "unless the remote still matches the configuration you loaded. Do not unplug it "
            "until it finishes.",
            parent=self.app.root,
        ):
            return
        self.writing = True
        self.write_button.configure(state="disabled")
        base, new = self.base_blob, self.blob
        saved = self.app_backup_dir() / f"HarmonyOne-written-{datetime.now():%Y%m%d-%H%M%S}.EZHex"

        def worker() -> None:
            previous = os.environ.get(WRITE_ENV_VAR)
            device = None
            try:
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(new)
                os.environ[WRITE_ENV_VAR] = "1"
                device, transport = open_harmony_one()
                transport.flush()
                write_configuration(
                    transport, new, base, lambda d, t: self.events.put(("progress", (d, t)))
                )
                self.events.put(("done", saved))
            except Exception as error:
                self.events.put(("error", str(error)))
            finally:
                if previous is None:
                    os.environ.pop(WRITE_ENV_VAR, None)
                else:
                    os.environ[WRITE_ENV_VAR] = previous
                if device is not None:
                    device.close()

        threading.Thread(target=worker, name="HarmonyOneWrite", daemon=True).start()
        self.frame.after(200, self._poll)

    def app_backup_dir(self) -> Path:
        from harmony_one.app import backup_directory

        return backup_directory()

    def _poll(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                done, total = payload
                self.progress.configure(value=done * 100 / total)
                self.status.set(f"Writing {done:,} of {total:,} bytes...")
            elif event == "done":
                self.writing = False
                self.write_button.configure(state="normal")
                self.base_blob = self.blob
                self.app.remote_blob = self.blob
                self.status.set(
                    f"Written and verified by read-back. The remote is restarting. A copy of the "
                    f"written configuration is at {payload}."
                )
                messagebox.showinfo("Write complete", self.status.get(), parent=self.app.root)
            elif event == "error":
                self.writing = False
                self.write_button.configure(state="normal")
                self.status.set(
                    f"Write failed: {payload}. If the remote shows an error screen, the saved copy "
                    "in the backups folder can be written again once it is reachable."
                )
                messagebox.showerror("Write failed", payload, parent=self.app.root)
        if self.writing:
            self.frame.after(200, self._poll)
