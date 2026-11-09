"""Backup browser tab: inspect the devices and activities inside a backup."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from harmony_one.config_reader import ConfigError
from harmony_one.object_library import build_remote_objects


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
    """Read-only viewer for the contents of a validated backup."""

    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.blob: bytes | None = None
        self.bundle: dict[str, Any] | None = None
        self.path: Path | None = None
        self.items_by_node: dict[str, dict[str, Any]] = {}
        self._build()

    def _build(self) -> None:
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(2, weight=1)
        ttk.Label(
            self.frame,
            wraplength=860,
            justify="left",
            text=(
                "Browse the devices and activities stored inside a Harmony One backup. "
                "Take a fresh backup from the connected remote or open a saved .EZHex file, "
                "then select an item to see its decoded contents. Everything here is "
                "read-only; nothing is sent to the remote."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        buttons = ttk.Frame(self.frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(
            buttons,
            text="Back up remote and browse",
            command=lambda: self.app._start_backup(browse_after=True),
        ).pack(side="left")
        ttk.Button(buttons, text="Open backup file...", command=self._open_file).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            buttons, text="Export selected object...", command=self._export_selected
        ).pack(side="left", padx=(8, 0))

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
        self.items_by_node.clear()
        self.tree.delete(*self.tree.get_children())
        devices = [item for item in bundle["objects"] if item["object_type"] == "device"]
        activities = [item for item in bundle["objects"] if item["object_type"] == "activity"]
        device_root = self.tree.insert("", "end", text=f"Devices ({len(devices)})", open=True)
        for item in devices:
            node = self.tree.insert(device_root, "end", text=item["name"])
            self.items_by_node[node] = item
        activity_root = self.tree.insert(
            "", "end", text=f"Activities ({len(activities)})", open=True
        )
        for item in activities:
            node = self.tree.insert(activity_root, "end", text=item["name"])
            self.items_by_node[node] = item
        source = bundle["source"]
        origin = str(path) if path else "fresh USB read"
        self.summary.set(
            f"Backup: {origin}   {len(blob):,} bytes   architecture {source['architecture']}, "
            f"skin {source['skin']}   sha256 {source['backup_sha256'][:16]}...   "
            f"{len(devices)} devices, {len(activities)} activities. Names are the decoded "
            "startup titles; an added activity can still show its template's title."
        )
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
            return
        item = self.items_by_node.get(selection[0])
        if item is None:
            self._show_text(
                "Select an individual device or activity to see its decoded contents."
            )
            return
        lines = _device_lines(item) if item["object_type"] == "device" else _activity_lines(item)
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
