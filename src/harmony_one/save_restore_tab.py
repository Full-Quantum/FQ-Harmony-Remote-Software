"""Save complete Harmony One backups and transfer them to another remote."""

from __future__ import annotations

import hashlib
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from harmony_one.config_reader import RemoteConfig, parse_config
from harmony_one.flash_writer import WRITE_ENV_VAR, write_configuration
from harmony_one.harmony_usb import _save_validated_backup, read_harmony_one_config
from harmony_one.harmony_usb_write import open_harmony_one
from harmony_one.save_restore import (
    SaveRestoreError,
    validate_restore_pair,
    validate_source_save,
)


FQ_PRESET_FILES = {
    "FQ Preset older": "FQ Preset older.EZHex",
    "FQ Preset newer": "FQ Preset newer.EZHex",
}


class SaveRestoreTab:
    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.source_blob: bytes | None = None
        self.source_path: Path | None = None
        self.source_requires_disconnect = False
        self.expected_target_blob: bytes | None = None
        self.target_blob: bytes | None = None
        self.target_path: Path | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.source_ready = tk.BooleanVar(value=False)
        self.target_ready = tk.BooleanVar(value=False)
        self.confirm_replace = tk.BooleanVar(value=False)
        self.preset_choice = tk.StringVar(value="")
        self.source_info = tk.StringVar(value="No backup selected.")
        self.target_info = tk.StringVar(value="No target backup read.")
        self.status = tk.StringVar(value="No transfer operation has run.")
        self.saved_backups: dict[str, Path] = {}
        self._build()

    def _build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.frame, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(
            self.frame, orient="vertical", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.content = ttk.Frame(self.canvas)
        self.content_window = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )
        self.content.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

        ttk.Label(
            self.content,
            text="Copy a complete remote backup",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self.content,
            text=(
                "Choose a saved .EZHex backup, then verify the destination before replacing "
                "its complete save. This does not transfer local Remote Data JSON."
            ),
            wraplength=850,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.content.columnconfigure(0, weight=1)

        intro = ttk.LabelFrame(
            self.content, text="1. Choose the backup to copy", padding=12
        )
        intro.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            intro,
            text=(
                "Scan and download reads the connected source remote without changing it and saves "
                "its complete configuration as a timestamped .EZHex file. Or select a backup saved "
                "earlier, an FQ preset, or browse for an .EZHex file. Upload replaces the destination "
                "save; it does not merge."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        source_row = ttk.Frame(intro)
        source_row.pack(fill="x", pady=(10, 4))
        self.source_button = ttk.Button(
            source_row,
            text="Scan and download connected remote backup (.EZHex)...",
            command=self._scan_source,
        )
        self.source_button.pack(side="left")
        self.source_file_button = ttk.Button(
            source_row,
            text="Browse for .EZHex...",
            command=self._choose_source,
        )
        self.source_file_button.pack(side="left", padx=(8, 0))
        ttk.Label(source_row, textvariable=self.source_info, wraplength=750).pack(
            side="left", padx=(10, 0), anchor="w"
        )
        self.backup_location = tk.StringVar(
            value=f"Saved backup folder: {self._backup_directory()}"
        )
        ttk.Label(
            intro,
            textvariable=self.backup_location,
            wraplength=850,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        saved_row = ttk.Frame(intro)
        saved_row.pack(fill="x", pady=(6, 2))
        ttk.Label(saved_row, text="Saved remote contents:").pack(side="left")
        self.saved_selector = ttk.Combobox(
            saved_row,
            state="readonly",
            width=56,
        )
        self.saved_selector.pack(side="left", padx=(6, 0))
        self.saved_refresh_button = ttk.Button(
            saved_row,
            text="Refresh",
            command=self._refresh_saved_backups,
        )
        self.saved_refresh_button.pack(side="left", padx=(6, 0))
        self.saved_load_button = ttk.Button(
            saved_row,
            text="Use selected backup",
            command=self._load_saved_backup,
        )
        self.saved_load_button.pack(side="left", padx=(6, 0))
        ttk.Label(
            intro,
            text=(
                "Choose a saved backup above and select “Use selected backup” to continue. "
                "Downloads appear here, along with the built-in FQ presets."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        self.source_ack = ttk.Checkbutton(
            intro,
            variable=self.source_ready,
            command=self._update_buttons,
            text="For scanning only: the source remote to back up is connected by USB and charged.",
        )
        self.source_ack.pack(anchor="w", pady=(4, 0))

        compatibility = ttk.LabelFrame(
            self.content, text="2. Back up and check the destination remote", padding=10
        )
        compatibility.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            compatibility,
            text=(
                "Disconnect the source remote, then connect the same-model Harmony One you intend "
                "to replace. This read-only step saves a fresh destination backup and checks "
                "architecture and screen-skin compatibility. It cannot prove the model or firmware "
                "is safe to use, so keep an independent backup."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        self.target_ack = ttk.Checkbutton(
            compatibility,
            variable=self.target_ready,
            command=self._update_buttons,
            text=(
                "I confirmed the intended destination is connected by USB and charged."
            ),
        )
        self.target_ack.pack(anchor="w", pady=(8, 0))
        destination_actions = ttk.Frame(compatibility)
        destination_actions.pack(anchor="w", pady=(6, 0))
        self.read_button = ttk.Button(
            destination_actions,
            text="Back up destination and check compatibility",
            command=self._read_target,
            state="disabled",
        )
        self.read_button.pack(side="left")
        self.open_backup_folder_button = ttk.Button(
            destination_actions,
            text="Open backup folder",
            command=self._open_backup_folder,
        )
        self.open_backup_folder_button.pack(side="left", padx=(8, 0))
        ttk.Label(compatibility, textvariable=self.target_info, wraplength=850).pack(
            anchor="w", pady=(6, 0)
        )

        writes = ttk.LabelFrame(
            self.content, text="3. Replace the destination", padding=10
        )
        writes.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            writes,
            text=(
                "This erases and rewrites the target's full configuration, including its devices, "
                "activities, settings, and screen data. Keep it connected and powered until "
                "read-back verification and restart finish."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        self.replace_ack = ttk.Checkbutton(
            writes,
            variable=self.confirm_replace,
            command=self._update_buttons,
            text="I understand and want to replace this remote's complete save.",
        )
        self.replace_ack.pack(anchor="w")
        self.restore_button = ttk.Button(
            writes,
            text="Upload selected backup to connected remote",
            command=self._restore,
            state="disabled",
        )
        self.restore_button.pack(anchor="w", pady=(8, 0))
        self.progress = ttk.Progressbar(writes, maximum=100)
        self.progress.pack(fill="x", pady=(10, 0))
        ttk.Label(writes, textvariable=self.status, wraplength=850).pack(
            anchor="w", pady=(6, 0)
        )
        self._update_buttons()
        self._refresh_saved_backups()

    def _update_scroll_region(self, _event: tk.Event[Any]) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event: tk.Event[Any]) -> None:
        self.canvas.itemconfigure(self.content_window, width=event.width)

    def _bind_mousewheel(self, _event: tk.Event[Any]) -> None:
        self.canvas.bind_all("<MouseWheel>", self._scroll_with_mousewheel)

    def _unbind_mousewheel(self, event: tk.Event[Any]) -> None:
        pointer_target = self.canvas.winfo_containing(event.x_root, event.y_root)
        target_path = str(pointer_target) if pointer_target is not None else ""
        content_path = str(self.content)
        if target_path == content_path or target_path.startswith(content_path + "."):
            return
        self.canvas.unbind_all("<MouseWheel>")

    def _scroll_with_mousewheel(self, event: tk.Event[Any]) -> None:
        units = int(-event.delta / 120)
        if units:
            self.canvas.yview_scroll(units, "units")

    @staticmethod
    def _preset_directory() -> Path:
        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS")) / "data" / "presets"
        return Path(__file__).resolve().parent / "data" / "presets"

    def _refresh_saved_backups(self, select_path: Path | None = None) -> None:
        backups = {
            f"{path.name} — saved backup": path
            for path in sorted(
                self._backup_directory().glob("*.EZHex"),
                reverse=True,
            )
        }
        presets = {
            f"{label} — built-in preset": self._preset_directory() / filename
            for label, filename in FQ_PRESET_FILES.items()
        }
        self.saved_backups = {**presets, **backups}
        choices = tuple(self.saved_backups)
        self.saved_selector.configure(values=choices)
        if select_path is not None:
            selected = next(
                (
                    label
                    for label, path in self.saved_backups.items()
                    if path.resolve() == select_path.resolve()
                ),
                "",
            )
            self.saved_selector.set(selected)
        elif self.saved_selector.get() not in self.saved_backups:
            self.saved_selector.set("")

    def _load_saved_backup(self) -> None:
        path = self.saved_backups.get(self.saved_selector.get())
        if path is None:
            messagebox.showwarning(
                "Choose a saved backup",
                "Select a downloaded backup or FQ preset from the list first.",
                parent=self.app.root,
            )
            return
        try:
            blob = path.read_bytes()
            config = validate_source_save(blob)
        except (OSError, SaveRestoreError) as error:
            messagebox.showerror(
                "Could not load saved backup",
                str(error),
                parent=self.app.root,
            )
            return
        self._set_source(blob, path, config)

    def _open_backup_folder(self) -> None:
        directory = self._backup_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(directory))
        except OSError as error:
            messagebox.showerror(
                "Could not open backup folder",
                f"Could not open {directory}:\n{error}",
                parent=self.app.root,
            )

    def _choose_source(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.app.root,
            title="Choose a saved Harmony One backup to upload",
            initialdir=str(self.app.backup_initialdir()),
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            blob = path.read_bytes()
            config = validate_source_save(blob)
        except (OSError, SaveRestoreError) as error:
            messagebox.showerror(
                "Could not load source save",
                str(error),
                parent=self.app.root,
            )
            return
        self.source_blob = blob
        self.source_path = path
        self.target_blob = None
        self.target_path = None
        self._set_source(blob, path, config)

    def _set_source(self, blob: bytes, path: Path, config: RemoteConfig) -> None:
        self.expected_target_blob = None
        self.source_blob = blob
        self.source_path = path
        assistant = getattr(self.app, "remote_assistant", None)
        if assistant is not None:
            assistant.load(blob, path)
        slideshow = getattr(self.app, "slideshow", None)
        slideshow_loader = getattr(slideshow, "load", None)
        if callable(slideshow_loader):
            slideshow_loader(blob)
        self.source_requires_disconnect = False
        self.target_blob = None
        self.target_path = None
        self.source_ready.set(False)
        self.target_ready.set(False)
        self.confirm_replace.set(False)
        self.target_info.set(
            "Connect the remote you want to upload to, confirm it is charged, "
            "and read its backup before replacing it."
        )
        self.source_info.set(
            f"{path.name} — {len(blob):,} bytes, architecture {config.architecture}, "
            f"skin {config.skin}, SHA-256 {hashlib.sha256(blob).hexdigest()[:16]}…"
        )
        self.status.set(
            "Backup selected. Connect the remote you want to upload to, then continue to Step 2."
        )
        memory_map = getattr(self.app, "memory_map", None)
        if memory_map is not None:
            memory_map.load(blob, path, report_error=False)
        self._update_buttons()

    def load_edit_candidate(self, blob: bytes, path: Path, baseline: bytes) -> None:
        """Stage a bounded edit that must only be written over its original backup."""
        self._set_source(blob, path, validate_source_save(blob))
        self.expected_target_blob = baseline
        self.target_info.set(
            "Keep the original remote connected, confirm it is charged, and read its backup. "
            "It must exactly match the backup used to make this edit."
        )
        self.status.set(
            "Remote Assistant candidate selected. Read the original remote in Step 2, "
            "then review and explicitly confirm the rewrite in Step 3."
        )

    def _check_edit_baseline(self, blob: bytes) -> None:
        if self.expected_target_blob is not None and blob != self.expected_target_blob:
            raise SaveRestoreError(
                "The remote does not exactly match the backup used for this edit. "
                "Reload its fresh backup and prepare the change again. No configuration was written."
            )

    def _scan_source(self) -> None:
        if self.busy or not self.source_ready.get():
            return
        if self._remote_busy():
            messagebox.showwarning(
                "Remote busy",
                "Wait for the current USB operation to finish first.",
                parent=self.app.root,
            )
            return
        if not messagebox.askyesno(
            "Download and save remote backup",
            "Read the complete configuration from this connected Harmony One and save a "
            "timestamped .EZHex backup in the shown backup folder? This is read-only and does "
            "not change the remote. Confirm this is the remote you want to archive and that it "
            "is connected by USB and charged.",
            parent=self.app.root,
        ):
            return
        self.busy = True
        self._update_buttons()
        self.progress.configure(value=0)
        self.status.set("Downloading and validating the complete remote backup.")

        def worker() -> None:
            device = None
            failure: str | None = None
            result: tuple[bytes, Path] | None = None
            try:
                device, transport = open_harmony_one()
                transport.flush()
                source = read_harmony_one_config(
                    transport,
                    lambda done, total: self.events.put(("progress", (done, total))),
                )
                validate_source_save(source)
                directory = self._backup_directory()
                directory.mkdir(parents=True, exist_ok=True)
                source_path = directory / f"HarmonyOne-transfer-source-{self._stamp()}.EZHex"
                _save_validated_backup(source_path, source, overwrite=False)
                result = (source, source_path)
            except Exception as error:
                failure = str(error)
            finally:
                if device is not None:
                    try:
                        device.close()
                    except Exception as error:
                        detail = f"Could not close the USB connection cleanly: {error}"
                        failure = f"{failure}\n{detail}" if failure else detail
            if failure is not None:
                self.events.put(("error", failure))
            elif result is not None:
                self.events.put(("source", result))

        threading.Thread(target=worker, name="HarmonyOneTransferSource", daemon=True).start()
        self.frame.after(150, self._poll)

    def _remote_busy(self) -> bool:
        return bool(
            self.app._backup_running
            or getattr(self.app.slideshow, "writing", False)
            or getattr(self.app.clock, "writing", False)
            or getattr(self.app.screen_styles, "writing", False)
            or getattr(self.app.activity_upload_dialog, "busy", False)
        )

    def _update_buttons(self) -> None:
        self.source_button.configure(
            state=(
                "normal"
                if self.source_ready.get() and not self.busy
                else "disabled"
            )
        )
        self.source_file_button.configure(state="disabled" if self.busy else "normal")
        self.saved_selector.configure(state="disabled" if self.busy else "readonly")
        self.saved_refresh_button.configure(state="disabled" if self.busy else "normal")
        self.saved_load_button.configure(state="disabled" if self.busy else "normal")
        self.source_ack.configure(state="disabled" if self.busy else "normal")
        self.target_ack.configure(state="disabled" if self.busy else "normal")
        self.replace_ack.configure(state="disabled" if self.busy else "normal")
        self.read_button.configure(
            state=(
                "normal"
                if self.source_blob is not None and self.target_ready.get() and not self.busy
                else "disabled"
            )
        )
        self.restore_button.configure(
            state=(
                "normal"
                if self.source_blob is not None
                and self.target_blob is not None
                and self.target_ready.get()
                and self.confirm_replace.get()
                and not self.busy
                else "disabled"
            )
        )

    def _read_target(self) -> None:
        if self.busy or self.source_blob is None or not self.target_ready.get():
            return
        if self._remote_busy():
            messagebox.showwarning(
                "Remote busy",
                "Wait for the current USB operation to finish first.",
                parent=self.app.root,
            )
            return
        prompt = (
            "The remote used to download this backup must be disconnected. Confirm that the "
            "different intended Harmony One is now connected by USB and charged. Read and save "
            "its fresh backup? This step is read-only and does not write to the remote."
            if self.source_requires_disconnect
            else "Confirm that the intended Harmony One you want to upload to is connected "
            "by USB and charged. Read and save its fresh backup? This step is read-only and "
            "does not write to the remote."
        )
        if not messagebox.askyesno(
            "Read destination remote",
            prompt,
            parent=self.app.root,
        ):
            return
        self.busy = True
        self._update_buttons()
        self.progress.configure(value=0)
        self.status.set("Reading and validating the destination backup.")
        source = self.source_blob

        def worker() -> None:
            device = None
            failure: str | None = None
            result: tuple[bytes, Path] | None = None
            try:
                device, transport = open_harmony_one()
                transport.flush()
                target = read_harmony_one_config(
                    transport,
                    lambda done, total: self.events.put(("progress", (done, total))),
                )
                if source is None:
                    raise SaveRestoreError("No source save is available for target comparison.")
                directory = self._backup_directory()
                directory.mkdir(parents=True, exist_ok=True)
                target_path = directory / f"HarmonyOne-transfer-target-{self._stamp()}.EZHex"
                _save_validated_backup(target_path, target, overwrite=False)
                self._check_edit_baseline(target)
                validate_restore_pair(source, target)
                result = (target, target_path)
            except Exception as error:
                failure = str(error)
                if "target_path" in locals():
                    failure += f"\nTarget backup: {target_path}"
            finally:
                if device is not None:
                    try:
                        device.close()
                    except Exception as error:
                        detail = f"Could not close the USB connection cleanly: {error}"
                        failure = f"{failure}\n{detail}" if failure else detail
            if failure is not None:
                self.events.put(("error", failure))
            elif result is not None:
                self.events.put(("target", result))

        threading.Thread(target=worker, name="HarmonyOneTransferRead", daemon=True).start()
        self.frame.after(150, self._poll)

    def _restore(self) -> None:
        if (
            self.busy
            or self.source_blob is None
            or self.target_blob is None
            or not self.target_ready.get()
            or not self.confirm_replace.get()
        ):
            return
        if self._remote_busy():
            messagebox.showwarning(
                "Remote busy",
                "Wait for the current USB operation to finish first.",
                parent=self.app.root,
            )
            return
        source_hash = hashlib.sha256(self.source_blob).hexdigest()
        if not messagebox.askyesno(
            "Replace the complete remote save",
            "This will erase and replace the target Harmony One's complete configuration "
            "with the selected source save.\n\n"
            f"Source: {self.source_path}\nSHA-256: {source_hash}\n"
            f"Target baseline: {self.target_path}\n\n"
            "The app takes another fresh target backup and requires an exact live match "
            "before erasing. A failure after erasure can leave the remote unusable. "
            "Proceed only if the intended target is connected, charged, and will remain "
            "connected and powered throughout the write.",
            parent=self.app.root,
        ):
            return
        source = self.source_blob
        self.busy = True
        self._update_buttons()
        self.progress.configure(value=0)
        self.status.set("Rechecking the live destination before the full configuration write.")

        def worker() -> None:
            previous = os.environ.get(WRITE_ENV_VAR)
            device = None
            failure: str | None = None
            result: tuple[bytes, Path, Path] | None = None
            try:
                device, transport = open_harmony_one()
                transport.flush()
                live = read_harmony_one_config(
                    transport,
                    lambda done, total: self.events.put(("progress", (done, total))),
                )
                validate_restore_pair(source, live)
                directory = self._backup_directory()
                directory.mkdir(parents=True, exist_ok=True)
                stamp = self._stamp()
                baseline_path = directory / f"HarmonyOne-transfer-before-{stamp}.EZHex"
                candidate_path = directory / f"HarmonyOne-transfer-source-{stamp}.EZHex"
                _save_validated_backup(baseline_path, live, overwrite=False)
                self._check_edit_baseline(live)
                _save_validated_backup(candidate_path, source, overwrite=False)
                os.environ[WRITE_ENV_VAR] = "1"
                write_configuration(
                    transport,
                    source,
                    live,
                    lambda done, total: self.events.put(
                        ("write-progress", (done, total))
                    ),
                )
                result = (source, baseline_path, candidate_path)
            except Exception as error:
                failure = str(error)
                if "baseline_path" in locals():
                    failure += f"\nFresh target backup: {baseline_path}"
                if "candidate_path" in locals():
                    failure += f"\nSource save copy: {candidate_path}"
            finally:
                if previous is None:
                    os.environ.pop(WRITE_ENV_VAR, None)
                else:
                    os.environ[WRITE_ENV_VAR] = previous
                if device is not None:
                    try:
                        device.close()
                    except Exception as error:
                        detail = f"Could not close the USB connection cleanly: {error}"
                        failure = f"{failure}\n{detail}" if failure else detail
            if failure is not None:
                self.events.put(("error", failure))
            elif result is not None:
                self.events.put(("complete", result))

        threading.Thread(target=worker, name="HarmonyOneTransferWrite", daemon=True).start()
        self.frame.after(150, self._poll)

    def _poll(self) -> None:
        finished = False
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event in {"progress", "write-progress"}:
                done, total = payload
                phase = "Reading destination" if event == "progress" else "Writing and verifying"
                self.progress.configure(value=done * 100 / total if total else 0)
                self.status.set(f"{phase}: {done:,} of {total:,} bytes.")
            elif event == "source":
                finished = True
                blob, path = payload
                config = parse_config(blob)
                self._set_source(blob, path, config)
                self._refresh_saved_backups(path)
                self.source_requires_disconnect = True
                self.target_info.set(
                    "To continue now, disconnect the remote you just backed up, connect "
                    "the different remote you want to update, then read its backup."
                )
                self.source_ready.set(False)
                self.status.set(
                    f"Backup downloaded and saved to:\n{path}\n"
                    "You can stop here and select this file from the saved-contents list later. "
                    "To upload it now, "
                    "disconnect this remote and connect the different remote you want to update."
                )
            elif event == "target":
                finished = True
                blob, path = payload
                self.target_blob = blob
                self.target_path = path
                config = parse_config(blob)
                self.target_info.set(
                    f"Target: {path} — {len(blob):,} bytes, architecture "
                    f"{config.architecture}, skin {config.skin}, SHA-256 "
                    f"{hashlib.sha256(blob).hexdigest()[:16]}…"
                )
                self.status.set(
                    "Fresh target backup saved; source and target passed architecture/skin "
                    "checks. Review the warnings before enabling the write."
                )
            elif event == "complete":
                finished = True
                blob, baseline_path, candidate_path = payload
                self.target_blob = None
                self.target_path = baseline_path
                self.target_ready.set(False)
                self.confirm_replace.set(False)
                self.progress.configure(value=100)
                self.status.set(
                    "Source save written to the destination and verified byte-for-byte; "
                    "the destination is restarting. "
                    f"Fresh target backup: {baseline_path}. Source copy: {candidate_path}. "
                    "Check the remote's menus and operation."
                )
                self.app.remote_blob = blob
                self.app.remote_backup_path = candidate_path
                self.app.screen_styles.load(blob, candidate_path)
                self.app.backup_browser.load(blob, candidate_path)
                messagebox.showinfo(
                    "Transfer verified",
                    self.status.get(),
                    parent=self.app.root,
                )
            elif event == "error":
                finished = True
                self.status.set(f"Transfer failed: {payload}")
                if "expected exactly one Harmony One, found 0" in str(payload):
                    self.app._handle_missing_remote(str(payload))
                else:
                    messagebox.showerror(
                        "Could not transfer remote configuration",
                        payload,
                        parent=self.app.root,
                    )
        if finished:
            self.busy = False
            self._update_buttons()
        if self.busy:
            self.frame.after(100, self._poll)

    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    @staticmethod
    def _backup_directory() -> Path:
        from harmony_one.app import backup_directory

        return backup_directory()
