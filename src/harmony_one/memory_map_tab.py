"""Visual, read-only view of the configuration blocks in a saved remote backup."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from harmony_one.config_blocks import ConfigBlock, build_config_blocks
from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config


class MemoryMapTab:
    COLORS = {
        "header": "#315f86",
        "section": "#248a83",
        "trailer": "#c47c26",
    }

    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.blob: bytes | None = None
        self.path: Path | None = None
        self.blocks: list[ConfigBlock] = []
        self.selected_index: int | None = None
        self._build()

    def _build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)
        ttk.Label(
            self.frame,
            text=(
                "Visualize the byte ranges stored in a complete backup. This map uses the "
                "configuration's section pointers and reports raw slot numbers; it does not "
                "claim that every section's internal data is fully decoded. Read-only: no USB "
                "writes are performed."
            ),
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        controls = ttk.Frame(self.frame)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(controls, text="Open backup file...", command=self._open_file).pack(
            side="left"
        )
        ttk.Button(
            controls,
            text="Show latest loaded save",
            command=self._load_latest,
        ).pack(side="left", padx=(8, 0))
        self.summary = tk.StringVar(value="No backup loaded.")
        ttk.Label(controls, textvariable=self.summary, wraplength=700).pack(
            side="left", padx=(12, 0)
        )

        content = ttk.Frame(self.frame)
        content.grid(row=2, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            content, background="white", highlightthickness=1, highlightbackground="#c4d2df"
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Button-1>", self._select_block)

        details = ttk.LabelFrame(self.frame, text="Selected block", padding=8)
        details.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        details.columnconfigure(0, weight=1)
        self.detail_text = tk.StringVar(value="Select a block in the map to inspect its range.")
        ttk.Label(details, textvariable=self.detail_text, wraplength=900, justify="left").grid(
            row=0, column=0, sticky="w"
        )
        self.hex_text = tk.Text(details, height=3, wrap="word", state="disabled")
        self.hex_text.grid(row=1, column=0, sticky="ew", pady=(6, 0))

    def load(
        self, blob: bytes, path: Path | None, *, report_error: bool = True
    ) -> None:
        try:
            validate_image(blob)
            config = parse_config(blob)
            blocks = build_config_blocks(blob)
        except (ConfigError, ValueError) as error:
            source = path.name if path else "Loaded backup"
            self.blob = blob
            self.path = path
            self.blocks = []
            self.selected_index = None
            self.summary.set(f"{source} — map unavailable: {error}")
            self.detail_text.set(
                "The backup remains available to other supported views. "
                f"Memory map detail: {error}"
            )
            self._set_hex("")
            self._draw()
            if report_error:
                messagebox.showerror(
                    "Could not map backup",
                    str(error),
                    parent=self.frame.winfo_toplevel(),
                )
            return
        self.blob = blob
        self.path = path
        self.blocks = blocks
        self.selected_index = None
        source = path.name if path else "loaded backup"
        self.summary.set(
            f"{source} — {len(blob):,} bytes; architecture {config.architecture}; "
            f"skin {config.skin}; {len(blocks)} blocks"
        )
        self.detail_text.set("Select a block in the map to inspect its range.")
        self._set_hex("")
        self._draw()

    def _on_canvas_resize(self, event: tk.Event[Any]) -> None:
        self._draw(event.width)

    def _draw(self, canvas_width: int | None = None) -> None:
        self.canvas.delete("all")
        if not self.blocks:
            self.canvas.configure(scrollregion=(0, 0, 1, 1))
            return
        width = max(canvas_width or self.canvas.winfo_width(), 560)
        bar_width = max(width - 230, 120)
        max_size = max(block.size for block in self.blocks) or 1
        row_height = 30
        for index, block in enumerate(self.blocks):
            y = index * row_height + 8
            scaled = max(3, round(bar_width * block.size / max_size))
            selected = index == self.selected_index
            self.canvas.create_rectangle(
                10,
                y,
                10 + scaled,
                y + 20,
                fill=self.COLORS[block.kind],
                outline="#102f42" if selected else self.COLORS[block.kind],
                width=3 if selected else 1,
                tags=(f"block-{index}",),
            )
            self.canvas.create_text(
                22,
                y + 10,
                text=f"{block.size:,} bytes",
                anchor="w",
                fill="white",
                font=("Segoe UI", 8, "bold"),
                tags=(f"block-{index}",),
            )
            self.canvas.create_text(
                bar_width + 25,
                y + 10,
                text=block.label,
                anchor="w",
                fill="#19384e",
                font=("Segoe UI", 9),
                tags=(f"block-{index}",),
            )
        self.canvas.configure(
            scrollregion=(0, 0, width, len(self.blocks) * row_height + 16)
        )

    def _select_block(self, event: tk.Event[Any]) -> None:
        items = self.canvas.find_overlapping(
            self.canvas.canvasx(event.x),
            self.canvas.canvasy(event.y),
            self.canvas.canvasx(event.x),
            self.canvas.canvasy(event.y),
        )
        for item in items:
            tags = self.canvas.gettags(item)
            block_tag = next((tag for tag in tags if tag.startswith("block-")), None)
            if block_tag is None:
                continue
            self.selected_index = int(block_tag.removeprefix("block-"))
            self._show_block(self.blocks[self.selected_index])
            self._draw()
            return

    def _show_block(self, block: ConfigBlock) -> None:
        if self.blob is None:
            return
        config = parse_config(self.blob)
        start_address = config.base_address + block.start_offset
        end_address = config.base_address + block.end_offset
        self.detail_text.set(
            f"{block.label} | file offsets 0x{block.start_offset:06X}–"
            f"0x{block.end_offset:06X} | flash addresses 0x{start_address:06X}–"
            f"0x{end_address:06X} | {block.size:,} bytes"
        )
        preview = self.blob[block.start_offset : min(block.end_offset, block.start_offset + 64)]
        self._set_hex(" ".join(f"{byte:02X}" for byte in preview))

    def _set_hex(self, value: str) -> None:
        self.hex_text.configure(state="normal")
        self.hex_text.delete("1.0", tk.END)
        self.hex_text.insert("1.0", value)
        self.hex_text.configure(state="disabled")

    def _load_latest(self) -> None:
        blob = getattr(self.app, "remote_blob", None)
        path = getattr(self.app, "remote_backup_path", None)
        if blob is None:
            messagebox.showinfo(
                "No loaded backup",
                "Read a remote or open a backup file first.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        self.load(blob, path)

    def _open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.frame.winfo_toplevel(),
            title="Open a Harmony One backup to map",
            initialdir=str(self.app.backup_initialdir()),
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            blob = Path(chosen).read_bytes()
        except OSError as error:
            messagebox.showerror(
                "Could not read backup",
                str(error),
                parent=self.frame.winfo_toplevel(),
            )
            return
        self.load(blob, Path(chosen))
