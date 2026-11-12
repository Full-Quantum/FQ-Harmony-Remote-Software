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

from harmony_one.app_paths import slideshow_library_directory
from harmony_one.flash_writer import WRITE_ENV_VAR, write_configuration
from harmony_one.harmony_usb_write import open_harmony_one
from harmony_one.pictures import decode_picture, write_png
from harmony_one.slideshow import (
    PictureReplaceError,
    encode_raw_picture,
    fit_cover,
    full_screen_pictures,
    replace_with_encoded,
)
from harmony_one.slideshow_cycle import (
    DEFAULT_SLIDE_SECONDS, MAX_SLIDE_SECONDS, SlideshowCycleError, read_picture_cycle,
    restore_default_cycle, set_cycle_seconds,
)
from harmony_one.slideshow_library import (
    SlideshowLibraryError,
    bundled_default_slideshow_images,
    seed_default_slideshow_images,
)

DEFAULT_SLOT_COUNT = 6


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
        try:
            self.default_images = seed_default_slideshow_images()
            self.library_error = ""
        except SlideshowLibraryError as error:
            self.default_images = ()
            self.library_error = str(error)
        self._build()
        self._refresh()
        if self.library_error:
            self.status.set(self.library_error)

    def _build(self) -> None:
        self.frame.columnconfigure(2, weight=1)
        self.frame.rowconfigure(1, weight=1)
        description = ttk.Frame(self.frame)
        description.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(
            description,
            wraplength=860,
            text=(
                "Each slideshow slot has its own Browse button. Add PNG/GIF files to the slideshow "
                "folder or choose another image (fitted to 176 x 220). Images are applied only to a "
                "loaded backup; the app never writes until you explicitly use the guarded write action. "
                "The default has four images and two empty slots. Empty slots are not played; "
                "Browse is enabled only for slots supported by the loaded backup. "
                "Set each image's time in whole seconds; new/default slots use 3 seconds. "
                "Return to default restores four images at 3 seconds each. "
                "The first slot may also be used as a charging/system screen."
            ),
        ).pack(anchor="w")
        ttk.Label(description, textvariable=self.cycle_info, wraplength=860).pack(
            anchor="w", pady=(4, 0)
        )
        self.listbox = tk.Listbox(self.frame, width=30, exportselection=False)
        self.listbox.grid(row=1, column=0, sticky="ns")
        self.listbox.bind("<<ListboxSelect>>", self._selected)
        self.slot_browse_frame = ttk.Frame(self.frame)
        self.slot_browse_frame.grid(row=1, column=1, sticky="n", padx=(4, 8))
        self.slot_browse_buttons: list[ttk.Button] = []
        self.slot_seconds: list[tk.StringVar] = []
        self.slot_time_inputs: list[ttk.Spinbox] = []
        self.slot_time_buttons: list[ttk.Button] = []
        self.canvas = tk.Label(self.frame, background="#222222", width=176, height=220)
        self.canvas.grid(row=1, column=2, sticky="nw", padx=8)

        buttons = ttk.Frame(self.frame)
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Button(buttons, text="Export as PNG...", command=self._export).pack(side="left", padx=4)
        ttk.Button(buttons, text="Discard unwritten changes", command=self._discard).pack(side="left")
        self.default_button = ttk.Button(
            buttons, text="Return to default slideshow", command=self._restore_defaults,
        )
        self.default_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="Open slideshow folder", command=self._open_slideshow_folder).pack(
            side="left", padx=4
        )

        writes = ttk.LabelFrame(self.frame, text="Write to the connected remote", padding=8)
        writes.grid(row=3, column=0, columnspan=3, sticky="ew")
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
        durations: list[int] = []
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
                    durations.append(screen.seconds)
                self.cycle_note = (
                    f"Maximum: {len(cycle)} cycle image slots for this backup. "
                    "Replacing a slot does not add another slot."
                )
                if len(cycle) < DEFAULT_SLOT_COUNT:
                    self.cycle_note += " Extra empty placeholders are disabled for this backup."
                if len(cycle) not in (4, 6):
                    self.cycle_note += " Default reset requires a four- or six-image cycle."
        if not labels:
            labels = [
                f"Slot {index} - {self._default_image_label(path)}"
                for index, path in enumerate(self.default_images, 1)
            ]
            if not self.blob:
                self.cycle_note = (
                    "Default slots from the newer remote profile. Load a backup to browse "
                    "images into its slideshow."
                )
            elif self.entries == [] and self.cycle_note:
                self.cycle_note += " Default reference images are shown below."
        while len(labels) < DEFAULT_SLOT_COUNT:
            label = f"Slot {len(labels) + 1} - Empty"
            if self.blob:
                label += " (not supported by this backup)"
            labels.append(label)
        self.cycle_info.set(self.cycle_note)
        self.listbox.delete(0, "end")
        for label in labels:
            self.listbox.insert("end", label)
        for widget in self.slot_browse_frame.winfo_children():
            widget.destroy()
        self.slot_browse_buttons = []
        self.slot_seconds = []
        self.slot_time_inputs = []
        self.slot_time_buttons = []
        for index in range(len(labels)):
            button = ttk.Button(
                self.slot_browse_frame,
                text="Browse...",
                command=lambda slot=index: self._browse_slot(slot),
                state="normal" if index < len(self.entries) else "disabled",
                padding=(4, 1),
            )
            button.grid(row=index, column=0, sticky="ew", pady=(0, 2))
            self.slot_browse_buttons.append(button)
            seconds = tk.StringVar(value=str(
                durations[index] if index < len(durations) else DEFAULT_SLIDE_SECONDS
            ))
            timing = ttk.Spinbox(
                self.slot_browse_frame, from_=1, to=MAX_SLIDE_SECONDS,
                textvariable=seconds, width=5,
            )
            timing.grid(row=index, column=1, padx=(4, 2), pady=(0, 2))
            ttk.Label(self.slot_browse_frame, text="sec").grid(row=index, column=2)
            apply = ttk.Button(
                self.slot_browse_frame, text="Set", padding=(4, 1),
                command=lambda slot=index: self._set_slot_seconds(slot),
            )
            apply.grid(row=index, column=3, padx=(4, 0), pady=(0, 2))
            self.slot_seconds.append(seconds)
            self.slot_time_inputs.append(timing)
            self.slot_time_buttons.append(apply)
        self._update_edit_controls()
        if labels:
            self.listbox.selection_set(min(selected[0], len(labels) - 1) if selected else 0)
            self._selected()
        else:
            self.preview = None
            self.canvas.configure(image="")

    @staticmethod
    def _default_image_label(path: Path) -> str:
        labels = {
            "01-charging-battery.png": "Charging battery",
            "02-blurred-lights-1.png": "Blurred colored lights 1",
            "03-blurred-lights-2.png": "Blurred colored lights 2",
            "04-blurred-lights-3.png": "Blurred colored lights 3",
        }
        return labels.get(path.name, path.name)

    def _current(self) -> tuple[int, Any] | None:
        selected = self.listbox.curselection()
        if selected and self.blob and selected[0] < len(self.entries):
            return self.entries[selected[0]]
        return None

    def _selected(self, _event: object = None) -> None:
        current = self._current()
        selection = self.listbox.curselection()
        if current:
            pixels = decode_picture(self.blob, current[1])
            rows = [
                "{" + " ".join(
                    "#%02x%02x%02x" % (pixel[:3] if pixel else (0, 0, 0))
                    for pixel in row
                ) + "}"
                for row in pixels
            ]
            image = tk.PhotoImage(width=len(pixels[0]), height=len(pixels))
            image.put(" ".join(rows))
        elif not self.entries and selection and selection[0] < len(self.default_images):
            try:
                image = tk.PhotoImage(
                    master=self.app.root, file=str(self.default_images[selection[0]])
                )
            except tk.TclError as error:
                self.status.set(f"Could not preview default slideshow image: {error}")
                return
        else:
            self.preview = None
            self.canvas.configure(image="")
            return
        self.preview = image
        self.canvas.configure(image=image, width=image.width(), height=image.height())

    def _replace(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo(
                "Slideshow", "Select a slideshow slot first.",
                parent=self.app.root,
            )
            return
        self._browse_slot(selection[0])

    def _browse_slot(self, index: int) -> None:
        if self.writing:
            return
        if not self.blob or not self.entries:
            messagebox.showinfo(
                "Slideshow", "Load a remote backup before replacing a slideshow image.",
                parent=self.app.root,
            )
            return
        if not 0 <= index < len(self.entries):
            messagebox.showinfo(
                "Slideshow", "This empty slot is not supported by the loaded backup.",
                parent=self.app.root,
            )
            return
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        try:
            slideshow_library_directory().mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror(
                "Could not open slideshow folder",
                f"Could not prepare {slideshow_library_directory()}:\n{error}",
                parent=self.app.root,
            )
            return
        current = self.entries[index]
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
            initialdir=str(slideshow_library_directory()),
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

    def _update_edit_controls(self) -> None:
        for index, (browse, timing, apply) in enumerate(zip(
            self.slot_browse_buttons, self.slot_time_inputs, self.slot_time_buttons,
        )):
            state = "normal" if index < len(self.entries) and not self.writing else "disabled"
            for widget in (browse, timing, apply):
                widget.configure(state=state)
        self.default_button.configure(
            state="normal" if len(self.entries) in (4, 6) and not self.writing else "disabled"
        )

    def _set_slot_seconds(self, index: int) -> None:
        if self.writing:
            return
        if not self.blob or not 0 <= index < len(self.entries):
            messagebox.showinfo(
                "Slideshow", "Load a backup with this slideshow slot before setting its time.",
                parent=self.app.root,
            )
            return
        try:
            try:
                seconds = int(self.slot_seconds[index].get())
            except ValueError as error:
                raise SlideshowCycleError(
                    f"Image time must be a whole number from 1 to {MAX_SLIDE_SECONDS} seconds."
                ) from error
            candidate = set_cycle_seconds(self.blob, index, seconds)
        except ValueError as error:
            messagebox.showerror("Could not set image time", str(error), parent=self.app.root)
            return
        self.blob = candidate
        self._refresh()
        self.status.set(
            f"Slot {index + 1} set to {seconds} seconds in memory. Nothing has been sent to the remote."
        )

    def _restore_defaults(self) -> None:
        if self.writing:
            return
        if not self.blob or len(self.entries) not in (4, 6):
            messagebox.showinfo(
                "Slideshow", "Load a backup with a supported four- or six-image slideshow first.",
                parent=self.app.root,
            )
            return
        if not messagebox.askyesno(
            "Return to default slideshow",
            "Restore the four bundled images at 3 seconds each in the loaded candidate? "
            "Slots 5 and 6 will be empty and not played.\n\n"
            "These pictures may also appear on charging or system screens. "
            "The original backup and connected remote will not be changed.",
            parent=self.app.root,
        ):
            return
        try:
            images = []
            for path in bundled_default_slideshow_images():
                source = tk.PhotoImage(master=self.app.root, file=str(path))
                if (source.width(), source.height()) != (176, 220):
                    raise SlideshowLibraryError(f"Default image has an invalid size: {path}")
                pixels = fit_cover(
                    source.width(), source.height(), lambda x, y: tuple(source.get(x, y))[:3]
                )
                images.append(encode_raw_picture(pixels))
            candidate = restore_default_cycle(self.blob, images)
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showerror("Could not restore default slideshow", str(error), parent=self.app.root)
            return
        self.blob = candidate
        self._refresh()
        self.status.set(
            "Default four-image slideshow restored at 3 seconds per image in memory. "
            "Slots 5 and 6 are empty. Nothing has been sent to the remote."
        )

    def _open_slideshow_folder(self) -> None:
        directory = slideshow_library_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(directory))
        except OSError as error:
            messagebox.showerror(
                "Could not open slideshow folder",
                f"Could not open {directory}:\n{error}",
                parent=self.app.root,
            )

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
        if self.base_blob and not self.writing:
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
        self._update_edit_controls()
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
                self._update_edit_controls()
                self.write_button.configure(state="normal")
                self.base_blob = self.blob
                self.app.remote_blob = self.blob
                style_tab = getattr(self.app, "screen_styles", None)
                if style_tab is not None:
                    style_tab.load(self.blob, self.app.remote_backup_path)
                self.status.set(
                    f"Written and verified by read-back. The remote is restarting. A copy of the "
                    f"written configuration is at {payload}."
                )
                messagebox.showinfo("Write complete", self.status.get(), parent=self.app.root)
            elif event == "error":
                self.writing = False
                self._update_edit_controls()
                self.write_button.configure(state="normal")
                self.status.set(
                    f"Write failed: {payload}. If the remote shows an error screen, the saved copy "
                    "in the backups folder can be written again once it is reachable."
                )
                messagebox.showerror("Write failed", payload, parent=self.app.root)
        if self.writing:
            self.frame.after(200, self._poll)
