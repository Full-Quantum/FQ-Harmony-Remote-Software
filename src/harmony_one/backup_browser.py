"""Backup browser tab: inspect the devices and activities inside a backup."""

from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from harmony_one.config_reader import ConfigError
from harmony_one.harmony_usb import BackupError, _save_validated_backup
from harmony_one.object_library import build_remote_objects
from harmony_one.remote_menu_icon_dialog import RemoteMenuIconDialog
from harmony_one.save_restore import SaveRestoreError, validate_source_save
from harmony_one.screen_styles import (
    MenuIconOccurrence,
    ScreenStyleError,
    create_menu_icon_candidate,
    list_menu_icon_choices,
    list_menu_icon_occurrences,
)


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _device_lines(item: dict[str, Any]) -> list[str]:
    lines = [
        f"Device: {item['name']}",
        f"Object id: {item['object_id']}",
        f"IR group: {item['source_ir_group']}",
    ]
    variables = item.get("source_state_variables") or {}
    if variables:
        lines.append("State variables:")
        for name, index in variables.items():
            lines.append(f"  {name}: variable {index}")
    records = item.get("ir_records") or []
    lines.append(f"Infrared records: {len(records)}")
    for record in records:
        parts = [
            f"  #{record['index']}",
            f"carrier {record['carrier_hz']} Hz" if record["carrier_hz"] else "no carrier",
            f"class {record['record_class']}",
            f"{record['pointer_groups']} pointer group(s)",
        ]
        blocks = record.get("first_pointer_group_blocks_us") or {}
        timings = [
            f"{name} {len(block)} edges"
            for name, block in blocks.items()
            if block is not None
        ]
        if timings:
            parts.append(", ".join(timings))
        lines.append("  ".join(parts))
    return lines


def _activity_lines(item: dict[str, Any]) -> list[str]:
    lines = [
        f"Activity: {item['name']}",
        f"Object id: {item['object_id']}",
        f"Activity number: {item['source_activity_number']}",
        f"Key map: {item['source_key_map']}   Mode: {item['source_mode']}",
    ]
    devices = item.get("devices") or []
    lines.append(f"Participating devices: {len(devices)}")
    for device in devices:
        lines.append(f"  {device['name']}")
    steps = item.get("decoded_state_steps") or []
    lines.append(f"Decoded state steps: {len(steps)}")
    for step in steps:
        detail = f"  {step['kind']}: {step['device']}"
        if step.get("value") is not None:
            detail += f" = {step['value']}"
        if step.get("group") is not None:
            detail += f" (IR group {step['group']}, record {step['record']})"
        lines.append(detail)
    bindings = item.get("key_map_bindings") or []
    ir_bindings = sum(1 for b in bindings if b["direct_action_list_ir_references"])
    lines.append(f"Button bindings: {len(bindings)} ({ir_bindings} with direct IR sends)")
    lines.append(f"Reachable action lists: {len(item.get('source_action_lists') or {})}")
    return lines


class BackupBrowserTab:
    """Browse remote objects and prepare offline per-row menu-icon edits."""

    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.blob: bytes | None = None
        self.bundle: dict[str, Any] | None = None
        self.path: Path | None = None
        self.items_by_node: dict[str, dict[str, Any]] = {}
        self.edited_icon_candidate = False
        self._build()

    def _build(self) -> None:
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(2, weight=1)
        ttk.Label(
            self.frame,
            wraplength=860,
            justify="left",
            text=(
                "A raw .EZHex backup is a complete snapshot of one remote; it is different "
                "from the editable Remote Data JSON. Browse the devices and activities in a "
                "backup from USB or open a saved file. Select an individual item to change "
                "one of its menu-button icons while preserving that backup's display style. "
                "Icon edits create a separate backup candidate; save it as a new .EZHex file "
                "before selecting it on the Transfer page."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        buttons = ttk.Frame(self.frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(buttons, text="Open backup file...", command=self._open_file).pack(
            side="left"
        )
        self.export_button = ttk.Button(
            buttons, text="Export selected object...", command=self._export_selected
        )
        self.export_button.pack(side="left", padx=(8, 0))
        self.edit_icon_button = ttk.Button(
            buttons,
            text="Edit selected menu icon...",
            command=self._edit_selected_menu_icon,
            state="disabled",
        )
        self.edit_icon_button.pack(side="left", padx=(8, 0))
        self.save_icon_candidate_button = ttk.Button(
            buttons,
            text="Save edited backup as...",
            command=self._save_edited_backup,
            state="disabled",
        )
        self.save_icon_candidate_button.pack(side="left", padx=(8, 0))
        self.import_all_button = ttk.Button(
            buttons,
            text="Import all decoded items to Remote Data...",
            command=self._import_all_to_remote_data,
            state="disabled",
        )
        self.import_all_button.pack(side="left", padx=(8, 0))

        self.tree = ttk.Treeview(self.frame, show="tree", selectmode="browse")
        self.tree.grid(row=2, column=0, sticky="nsw")
        self.tree.column("#0", width=280)
        self.tree.bind("<<TreeviewSelect>>", self._selected)

        details_frame = ttk.Frame(self.frame)
        details_frame.grid(row=2, column=1, sticky="nsew", padx=(12, 0))
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)
        self.details = tk.Text(details_frame, wrap="word", state="disabled", height=18)
        self.details.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(details_frame, orient="vertical", command=self.details.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.details.configure(yscrollcommand=scroll.set)

        self.summary = tk.StringVar(value="No backup is loaded.")
        ttk.Label(self.frame, textvariable=self.summary, wraplength=860, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def load(self, blob: bytes, path: Path | None) -> None:
        """Decode a validated backup and populate the browser."""
        if self.edited_icon_candidate and blob != self.blob:
            if not messagebox.askyesno(
                "Discard unsaved icon edits?",
                "Discard the unsaved menu-icon candidate and load another backup?",
                parent=self.frame.winfo_toplevel(),
            ):
                return
        try:
            bundle = build_remote_objects(blob)
        except (ConfigError, ValueError, IndexError) as error:
            messagebox.showerror(
                "Could not browse backup", str(error), parent=self.frame.winfo_toplevel()
            )
            return
        self.blob = blob
        self.bundle = bundle
        self.path = path
        assistant = getattr(self.app, "remote_assistant", None)
        if assistant is not None:
            assistant.load(blob, path)
        self.edited_icon_candidate = False
        self._update_icon_buttons()
        preview_loader = getattr(self.app, "_load_home_preview", None)
        if callable(preview_loader):
            preview_loader(blob)
        slideshow = getattr(self.app, "slideshow", None)
        slideshow_loader = getattr(slideshow, "load", None)
        if callable(slideshow_loader):
            slideshow_loader(blob)
        memory_map = getattr(self.app, "memory_map", None)
        if memory_map is not None:
            memory_map.load(blob, path, report_error=False)
        self.items_by_node.clear()
        self.tree.delete(*self.tree.get_children())
        devices = [item for item in bundle["objects"] if item["object_type"] == "device"]
        activities = [item for item in bundle["objects"] if item["object_type"] == "activity"]
        activity_root = self.tree.insert(
            "", "end", text=f"Activities ({len(activities)})", open=True
        )
        for item in activities:
            node = self.tree.insert(activity_root, "end", text=item["name"])
            self.items_by_node[node] = item
        device_root = self.tree.insert("", "end", text=f"Devices ({len(devices)})", open=True)
        for item in devices:
            node = self.tree.insert(device_root, "end", text=item["name"])
            self.items_by_node[node] = item
        source = bundle["source"]
        origin = str(path) if path else "fresh USB read"
        self.summary.set(
            f"Backup: {origin}   {len(blob):,} bytes   architecture {source['architecture']}, "
            f"skin {source['skin']}   sha256 {source['backup_sha256'][:16]}...   "
            f"{len(devices)} devices, {len(activities)} activities. Names are the decoded "
            "startup titles; an added activity can still show its template's title."
        )
        self._update_icon_buttons()
        self._show_text(
            "Select a device or activity on the left to see its decoded contents."
        )

    def _open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.frame.winfo_toplevel(),
            title="Open a Harmony One backup to browse",
            initialdir=str(self.app.backup_initialdir()),
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            blob = Path(chosen).read_bytes()
        except OSError as error:
            messagebox.showerror(
                "Could not read backup", str(error), parent=self.frame.winfo_toplevel()
            )
            return
        self.load(blob, Path(chosen))

    def _selected(self, _event: object = None) -> None:
        selection = self.tree.selection()
        if not selection:
            self._update_icon_buttons()
            return
        item = self.items_by_node.get(selection[0])
        if item is None:
            self._update_icon_buttons()
            self._show_text(
                "Select an individual device or activity to see its decoded contents."
            )
            return
        self._update_icon_buttons()
        lines = _device_lines(item) if item["object_type"] == "device" else _activity_lines(item)
        if self.edited_icon_candidate:
            lines.extend(
                [
                    "",
                    "Unsaved icon edit candidate:",
                    "Save the edited backup as a new .EZHex file before transferring it.",
                ]
            )
        lines.append("")
        lines.append("Known decoding limitations:")
        lines.extend(f"  {text}" for text in item.get("limitations", []))
        self._show_text("\n".join(lines))

    def _show_text(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _export_selected(self) -> None:
        if self.blob is None:
            messagebox.showinfo(
                "No backup loaded",
                "Back up the remote or open a backup file first.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        selection = self.tree.selection()
        item = self.items_by_node.get(selection[0]) if selection else None
        if item is None:
            messagebox.showinfo(
                "Select an object",
                "Select an individual device or activity to export.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        self.app.export_objects_with_ids(self.blob, [item["object_id"]])

    def _selected_object(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        return self.items_by_node.get(selection[0]) if selection else None

    def _update_icon_buttons(self) -> None:
        has_selected_item = bool(
            self.blob is not None and self._selected_object() is not None
        )
        self.edit_icon_button.configure(
            state="normal" if has_selected_item else "disabled"
        )
        self.save_icon_candidate_button.configure(
            state="normal" if self.edited_icon_candidate else "disabled"
        )
        self.import_all_button.configure(
            state="normal" if self.blob is not None and self.path is not None else "disabled"
        )

    def _import_all_to_remote_data(self) -> None:
        if self.blob is None or self.path is None:
            messagebox.showinfo(
                "Open a backup first",
                "Scan a remote or open a saved .EZHex backup before importing its contents.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        if self.edited_icon_candidate:
            messagebox.showwarning(
                "Save the edited backup first",
                "Save the menu-icon candidate as a new .EZHex file before importing it. "
                "This keeps the project copy linked to a backup on disk.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        self.app._start_editing_backup_copy(self.blob, self.path)

    def _edit_selected_menu_icon(self) -> None:
        item = self._selected_object()
        if item is None:
            messagebox.showinfo(
                "Select a remote item",
                "Load a backup and select an individual device or activity first.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        role = "device" if item["object_type"] == "device" else "activity"
        self.edit_menu_icon_for_item(role, item["name"])

    def edit_menu_icon_for_item(
        self,
        role: str,
        item_name: str,
        *,
        apply_to_all_occurrences: bool = False,
        fallback_to_role_occurrences: bool = False,
    ) -> None:
        if role not in ("device", "activity"):
            raise ValueError(f"Unsupported remote menu item role: {role!r}")
        if self.blob is None:
            messagebox.showinfo(
                "Load a remote backup first",
                "Scan the remote or open its .EZHex backup before changing a remote row icon.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        try:
            all_occurrences = list_menu_icon_occurrences(self.blob)
            role_name = "activities" if role == "activity" else "devices"
            occurrences = tuple(
                occurrence
                for occurrence in all_occurrences
                if occurrence.role == role_name
                and _normalized_name(occurrence.item_name) == _normalized_name(item_name)
            )
            choices = list_menu_icon_choices(self.blob)
        except ScreenStyleError as error:
            messagebox.showerror(
                "Menu icons cannot be edited",
                str(error),
                parent=self.frame.winfo_toplevel(),
            )
            return
        instructions = None
        show_occurrence_list = False
        apply_to_all = apply_to_all_occurrences
        if not occurrences:
            if fallback_to_role_occurrences and role == "activity":
                occurrences = tuple(
                    occurrence
                    for occurrence in all_occurrences
                    if occurrence.role == "activities"
                )
                if occurrences:
                    instructions = (
                        f"The decoded project name {item_name!r} does not match a menu label "
                        "in this backup. Choose the correct activity row from the list."
                    )
                    show_occurrence_list = True
                    apply_to_all = False
            if not occurrences:
                messagebox.showinfo(
                    "No editable menu button",
                    f"No recognizable {role} menu icon was found for {item_name!r} "
                    "in this backup.",
                    parent=self.frame.winfo_toplevel(),
                )
                return
        RemoteMenuIconDialog(
            self.frame.winfo_toplevel(),
            occurrences,
            choices,
            (
                self._apply_menu_icons_for_item
                if apply_to_all
                else self._apply_menu_icon
            ),
            apply_to_all_occurrences=apply_to_all,
            instructions=instructions,
            show_occurrence_list=show_occurrence_list,
        )

    def _apply_menu_icon(
        self, occurrence: MenuIconOccurrence, picture_data: bytes
    ) -> bool:
        return self._apply_menu_icon_occurrences((occurrence,), picture_data)

    def _apply_menu_icons_for_item(
        self, occurrence: MenuIconOccurrence, picture_data: bytes
    ) -> bool:
        try:
            matching_occurrences = tuple(
                item
                for item in list_menu_icon_occurrences(self.blob or b"")
                if item.role == occurrence.role
                and _normalized_name(item.item_name)
                == _normalized_name(occurrence.item_name)
            )
        except ScreenStyleError as error:
            messagebox.showerror(
                "Could not edit menu icons",
                str(error),
                parent=self.frame.winfo_toplevel(),
            )
            return False
        return self._apply_menu_icon_occurrences(matching_occurrences, picture_data)

    def _apply_menu_icon_occurrences(
        self,
        occurrences: tuple[MenuIconOccurrence, ...],
        picture_data: bytes,
    ) -> bool:
        if self.blob is None:
            return False
        candidate = self.blob
        occurrence_keys = tuple(
            (item.role, item.mode, item.page, item.row) for item in occurrences
        )
        try:
            for index, (role, mode, page, row) in enumerate(occurrence_keys):
                current = occurrences[index] if index == 0 else None
                if current is None or (
                    current.role,
                    current.mode,
                    current.page,
                    current.row,
                ) != (role, mode, page, row):
                    current = next(
                        (
                            item
                            for item in list_menu_icon_occurrences(candidate)
                            if (item.role, item.mode, item.page, item.row)
                            == (role, mode, page, row)
                        ),
                        None,
                    )
                if current is None:
                    raise ScreenStyleError(
                        f"Menu item at {role} mode {mode}, page {page}, row {row} "
                        "changed while preparing the icon candidate."
                    )
                candidate = create_menu_icon_candidate(
                    candidate, current, picture_data
                ).candidate
        except ScreenStyleError as error:
            messagebox.showerror(
                "Could not edit menu icon",
                str(error),
                parent=self.frame.winfo_toplevel(),
            )
            return False
        if candidate == self.blob:
            self.summary.set("The matching menu row(s) already use that icon.")
            return True

        self.blob = candidate
        self.edited_icon_candidate = True
        description = (
            f"{len(occurrence_keys)} matching menu row(s)"
            if len(occurrence_keys) > 1
            else occurrences[0].description
        )
        self.summary.set(
            f"Unsaved icon edit candidate for {description} in "
            f"{self.path or 'loaded backup'} — "
            f"{len(self.blob):,} bytes. Save as a new .EZHex file before upload."
        )
        memory_map = getattr(self.app, "memory_map", None)
        if memory_map is not None:
            memory_map.load(self.blob, self.path, report_error=False)
        self._selected()
        self._update_icon_buttons()
        return True

    def _save_edited_backup(self) -> None:
        if self.blob is None or not self.edited_icon_candidate:
            return
        default_name = (
            f"{self.path.stem}-icons.EZHex"
            if self.path is not None
            else "HarmonyOne-edited-icons.EZHex"
        )
        chosen = filedialog.asksaveasfilename(
            parent=self.frame.winfo_toplevel(),
            title="Save edited Harmony One backup",
            initialdir=str(self.app.backup_initialdir()),
            initialfile=default_name,
            defaultextension=".EZHex",
            confirmoverwrite=True,
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        destination = Path(chosen)
        if self.path is not None and destination.resolve() == self.path.resolve():
            messagebox.showwarning(
                "Keep the original backup",
                "Choose a different .EZHex path so the loaded backup remains unchanged.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        try:
            validate_source_save(self.blob, "Edited")
            _save_validated_backup(destination, self.blob, overwrite=True)
        except (BackupError, SaveRestoreError) as error:
            messagebox.showerror(
                "Could not save edited backup",
                str(error),
                parent=self.frame.winfo_toplevel(),
            )
            return
        self.path = destination
        self.edited_icon_candidate = False
        self.summary.set(
            f"Saved edited backup: {destination}   {len(self.blob):,} bytes. "
            "The loaded backup was left unchanged."
        )
        self._update_icon_buttons()
        self._selected()
        save_restore = getattr(self.app, "save_restore", None)
        if save_restore is not None:
            save_restore._refresh_saved_backups(destination)
