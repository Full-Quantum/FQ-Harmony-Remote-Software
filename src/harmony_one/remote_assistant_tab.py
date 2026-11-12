"""Remote Assistant prompt controls, using the existing guarded transfer workflow."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from harmony_one.harmony_usb import BackupError, _save_validated_backup
from harmony_one.remote_assistant import (
    RemoteAssistantError,
    inspect_remote_assistant,
    prepare_remote_assistant_candidate,
)
from harmony_one.save_restore import SaveRestoreError


class RemoteAssistantTab:
    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.source_blob: bytes | None = None
        self.source_path: Path | None = None
        self.candidate: bytes | None = None
        self.candidate_path: Path | None = None
        self.enabled: bool | None = None
        self.source_info = tk.StringVar(value="Scan the remote or load a backup first.")
        self.setting_info = tk.StringVar(value="Remote Assistant: not scanned")
        self.status = tk.StringVar(value="Nothing has been written to the remote.")
        self.frame.columnconfigure(0, weight=1)
        intro = ttk.LabelFrame(frame, text="Remote Assistant prompts", padding=12)
        intro.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            intro,
            text=(
                "Turn the built-in Remote Assistant prompts on or off. This retains its screens, "
                "Options-menu entry, Help, devices, activities, and display style; it does not "
                "install the feature in backups that lack a recognized toggle. "
                "The displayed setting belongs to the loaded backup, not a live USB query."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.source_info, wraplength=850).pack(anchor="w", pady=8)
        ttk.Label(intro, textvariable=self.setting_info, wraplength=850).pack(anchor="w")
        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, sticky="w", pady=12)
        self.on_button = ttk.Button(
            actions, text="Turn prompts on...", command=lambda: self._save_change(True),
            state="disabled",
        )
        self.on_button.pack(side="left")
        self.off_button = ttk.Button(
            actions, text="Turn prompts off...", command=lambda: self._save_change(False),
            state="disabled",
        )
        self.off_button.pack(side="left", padx=8)
        self.apply_button = ttk.Button(
            frame, text="Apply saved change to remote...", command=self._apply,
            state="disabled",
        )
        self.apply_button.grid(row=2, column=0, sticky="w")
        ttk.Label(
            frame,
            text=(
                "On/off saves a separate .EZHex candidate without overwriting the original. "
                "Apply opens Backups & Transfer: confirm the intended remote is charged and "
                "USB-connected, read a fresh matching backup, then explicitly confirm the full "
                "configuration rewrite. Read-back verifies bytes, not on-screen behavior. "
                "Assistant-specific behavior has not yet been confirmed on hardware."
            ),
            wraplength=850,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=12)
        ttk.Label(frame, textvariable=self.status, wraplength=850).grid(
            row=4, column=0, sticky="w"
        )

    def load(self, blob: bytes, path: Path | None) -> None:
        self.source_blob = blob
        self.source_path = path
        self.candidate = None
        self.candidate_path = None
        self.enabled = None
        self.source_info.set(f"Loaded backup: {path if path is not None else 'in-memory backup'}")
        self.apply_button.configure(state="disabled")
        self.on_button.configure(state="disabled")
        self.off_button.configure(state="disabled")
        try:
            setting = inspect_remote_assistant(blob)
        except RemoteAssistantError as error:
            self.setting_info.set("Remote Assistant: unsupported backup")
            self.status.set(str(error))
            return
        self.enabled = setting.enabled
        self.setting_info.set(f"Remote Assistant prompts in backup: {'On' if setting.enabled else 'Off'}")
        self.on_button.configure(state="disabled" if setting.enabled else "normal")
        self.off_button.configure(state="normal" if setting.enabled else "disabled")
        self.status.set("Choose on/off to save a separate candidate. No USB write has occurred.")

    def _save_change(self, enabled: bool) -> None:
        if self.source_blob is None or self.enabled is None:
            messagebox.showinfo(
                "Remote Assistant", "Load a supported remote backup first.", parent=self.app.root
            )
            return
        try:
            candidate = prepare_remote_assistant_candidate(self.source_blob, enabled)
        except RemoteAssistantError as error:
            messagebox.showerror("Could not change Remote Assistant", str(error), parent=self.app.root)
            return
        if candidate == self.source_blob:
            messagebox.showinfo(
                "Remote Assistant", "The loaded backup already has this setting.", parent=self.app.root
            )
            return
        state = "on" if enabled else "off"
        source = self.source_path
        chosen = filedialog.asksaveasfilename(
            parent=self.app.root, title="Save Remote Assistant candidate",
            initialdir=str(source.parent if source else self.app.backup_initialdir()),
            initialfile=f"{source.stem if source else 'HarmonyOne'}-assistant-{state}.EZHex",
            defaultextension=".EZHex",
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        destination = Path(chosen)
        try:
            if source is not None and destination.resolve() == source.resolve():
                raise BackupError("Choose a different file so the original backup remains unchanged.")
            _save_validated_backup(destination, candidate, overwrite=False)
        except (BackupError, OSError) as error:
            messagebox.showerror("Could not save assistant candidate", str(error), parent=self.app.root)
            return
        self.candidate = candidate
        self.candidate_path = destination
        self.apply_button.configure(state="normal")
        self.app.save_restore._refresh_saved_backups(destination)
        self.status.set(
            f"Saved candidate with prompts {state}: {destination}. "
            "The original backup and connected remote are unchanged. Use Apply to continue."
        )

    def _apply(self) -> None:
        if self.candidate is None or self.candidate_path is None or self.source_blob is None:
            messagebox.showinfo(
                "Remote Assistant", "Save an on/off candidate first.", parent=self.app.root
            )
            return
        transfer = self.app.save_restore
        if transfer.busy or transfer._remote_busy():
            messagebox.showwarning(
                "Remote busy", "Wait for the current USB operation to finish.", parent=self.app.root
            )
            return
        try:
            transfer.load_edit_candidate(self.candidate, self.candidate_path, self.source_blob)
        except SaveRestoreError as error:
            messagebox.showerror("Could not stage assistant change", str(error), parent=self.app.root)
            return
        self.app.tabs.select(self.app.backup_transfer_frame)
        self.app.backup_transfer_tabs.select(self.app.restore_save_frame)
