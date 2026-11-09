"""Picker for local activity icon artwork."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from harmony_one.activity_icons import BUILTIN_ICONS, IconError, import_icon, make_preview


class ActivityIconDialog:
    def __init__(
        self, parent: tk.Misc, icon: dict[str, Any] | None,
        on_apply: Callable[[dict[str, Any] | None], None],
    ) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("Activity icon (local Remote Data only)")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.icon = icon
        self.on_apply = on_apply
        self.preview: tk.PhotoImage | None = None

        body = ttk.Frame(self.window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                "Local preview only: changing the icon on the remote is not yet supported. "
                "Imported remote icons are not decoded."
            ),
            wraplength=480,
            justify="left",
        ).pack(anchor="w")
        self.preview_label = ttk.Label(body, text="No local icon")
        self.preview_label.pack(pady=(12, 4))
        self.icon_description = tk.StringVar()
        ttk.Label(body, textvariable=self.icon_description).pack()

        choices = ttk.LabelFrame(body, text="Choose a local icon", padding=8)
        choices.pack(fill="x", pady=(12, 0))
        for index, (key, label) in enumerate(BUILTIN_ICONS.items()):
            ttk.Button(
                choices, text=label,
                command=lambda name=key: self._select({"kind": "builtin", "name": name}),
            ).grid(row=0, column=index, padx=2)
        ttk.Button(choices, text="Choose PNG or GIF...", command=self._browse).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Button(choices, text="Clear local icon", command=lambda: self._select(None)).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            body,
            text=(
                "PNG or GIF, up to 2 MiB and 1024 x 1024 pixels. Images are fitted "
                "to a 32 x 32 preview; GIF uses its first frame. Fully transparent "
                "pixels remain transparent; partial transparency becomes opaque. "
                "32 x 32 is a preview size, not a verified remote requirement."
            ),
            wraplength=480,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(buttons, text="Apply", command=self._apply).pack(side="right", padx=5)
        self._select(icon)
        self.window.grab_set()

    def _select(self, icon: dict[str, Any] | None) -> None:
        self.icon = icon
        if icon is None:
            self.preview = None
            self.preview_label.configure(image="", text="No local icon")
            self.icon_description.set("Remote icon unchanged / unknown")
            return
        self.preview = make_preview(self.window, icon)
        self.preview_label.configure(image=self.preview, text="")
        self.icon_description.set(
            BUILTIN_ICONS[icon["name"]] if icon["kind"] == "builtin" else "Custom image"
        )

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.window, title="Choose a PNG or GIF",
            filetypes=[("PNG or GIF", "*.png *.gif"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._select(import_icon(Path(path), self.window))
        except IconError as error:
            messagebox.showerror("Could not use that image", str(error), parent=self.window)

    def _apply(self) -> None:
        self.on_apply(self.icon)
        self.window.destroy()
