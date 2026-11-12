"""Activities/Devices custom-style artwork controls and local previews."""

from __future__ import annotations

import base64
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from harmony_one.activity_icons import IconError, import_rgba_image
from harmony_one.custom_styles import MenuDesign, build_custom_style, render_menu_artwork
from harmony_one.screen_styles import (
    BUTTON_ROWS, SCREEN_SIZE, MenuArtwork, RemoteStyle, ScreenStyleError, save_user_style,
)

PreviewRenderer = Callable[[MenuArtwork, str], bytes]


class MenuDesignPage:
    def __init__(self, frame: ttk.Frame, role: str, renderer: PreviewRenderer) -> None:
        self.frame, self.role, self.renderer = frame, role, renderer
        self.image_rgba: bytes | None = None
        self.image_name = tk.StringVar(value="No image uploaded (PNG/GIF; up to 2 MiB).")
        self.preview: tk.PhotoImage | None = None
        self.pending_preview: str | None = None
        defaults = MenuDesign()
        self.background = tk.StringVar(value=defaults.background)
        self.colour = tk.StringVar(value=defaults.colour)
        self.second_colour = tk.StringVar(value=defaults.second_colour)
        self.gradient_direction = tk.StringVar(value=defaults.gradient_direction)
        self.button_colour = tk.StringVar(value=defaults.button_colour)
        self.corners = tk.StringVar(value=defaults.corners)
        self.border = tk.BooleanVar(value=defaults.border)
        self.border_width = tk.StringVar(value=str(defaults.border_width))
        self.border_colour = tk.StringVar(value=defaults.border_colour)
        self.status = tk.StringVar()
        frame.columnconfigure(0, weight=1)
        controls = ttk.Frame(frame)
        controls.grid(row=0, column=0, sticky="nw")
        self._choice(
            controls, 0, "Background", self.background,
            ("Solid colour", "Two-colour gradient", "Image"),
        )
        self._colour_control(controls, 1, "Background / image padding colour", self.colour)
        self._colour_control(controls, 2, "Gradient second colour", self.second_colour)
        self._choice(
            controls, 3, "Gradient direction", self.gradient_direction, ("Vertical", "Horizontal")
        )
        ttk.Button(controls, text="Upload background image...", command=self._browse_image).grid(
            row=4, column=0, sticky="w", pady=4,
        )
        ttk.Label(controls, textvariable=self.image_name, wraplength=290).grid(
            row=4, column=1, columnspan=2, sticky="w", padx=8,
        )
        self._colour_control(controls, 5, "Button fill colour", self.button_colour)
        self._choice(controls, 6, "Button corners", self.corners, ("Rounded", "Sharp"))
        ttk.Checkbutton(controls, text="Show button border", variable=self.border).grid(
            row=7, column=0, sticky="w", pady=4,
        )
        ttk.Label(controls, text="Border thickness (screen pixels)").grid(
            row=8, column=0, sticky="w", pady=4,
        )
        ttk.Spinbox(
            controls, from_=0, to=24, textvariable=self.border_width, width=8,
        ).grid(row=8, column=1, sticky="w", padx=8)
        self._colour_control(controls, 9, "Border colour", self.border_colour)

        text_group = ttk.LabelFrame(controls, text="Remote button text (preserved)", padding=8)
        text_group.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.text_controls: list[ttk.Combobox] = []
        for column, title in enumerate(("Font", "Colour", "Boldness", "Size")):
            ttk.Label(text_group, text=title).grid(row=0, column=column, sticky="w", padx=4)
            field = ttk.Combobox(text_group, values=("Unchanged",), state="disabled", width=11)
            field.set("Unchanged")
            field.grid(row=1, column=column, padx=4)
            self.text_controls.append(field)
        ttk.Label(
            text_group,
            text="Embedded bitmap fonts are shared with other screens; text editing is not available yet.",
            wraplength=470,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        preview_group = ttk.LabelFrame(frame, text="176 x 220 screen preview", padding=8)
        preview_group.grid(row=0, column=1, sticky="n", padx=(16, 0))
        self.canvas = tk.Canvas(
            preview_group, width=176, height=220, highlightthickness=0, background="#101820",
        )
        self.canvas.pack()
        ttk.Label(
            preview_group,
            text="Sample text only.\nRemote text stays unchanged.",
            wraplength=176,
        ).pack(anchor="w", pady=6)
        ttk.Button(preview_group, text="Update preview", command=self.refresh_preview).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status, wraplength=700).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=8,
        )
        frame.bind("<Destroy>", self._on_destroy, add="+")
        for variable in (
            self.background, self.colour, self.second_colour, self.gradient_direction,
            self.button_colour, self.corners, self.border, self.border_width, self.border_colour,
        ):
            variable.trace_add("write", self._schedule_preview)
        self.refresh_preview()

    def _choice(
        self, parent: ttk.Frame, row: int, title: str, variable: tk.StringVar,
        choices: tuple[str, ...],
    ) -> None:
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(parent, textvariable=variable, values=choices, state="readonly", width=24).grid(
            row=row, column=1, columnspan=2, sticky="w", padx=8,
        )

    def _colour_control(
        self, parent: ttk.Frame, row: int, title: str, variable: tk.StringVar,
    ) -> None:
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable, width=12).grid(
            row=row, column=1, sticky="w", padx=8,
        )
        ttk.Button(
            parent, text="Pick colour...", command=lambda: self._pick_colour(variable),
        ).grid(row=row, column=2, sticky="w")

    def _pick_colour(self, variable: tk.StringVar) -> None:
        try:
            _, chosen = colorchooser.askcolor(
                color=variable.get(), title="Choose style colour", parent=self.frame.winfo_toplevel()
            )
        except tk.TclError as error:
            messagebox.showerror("Could not choose colour", str(error), parent=self.frame.winfo_toplevel())
            return
        if chosen:
            variable.set(chosen)

    def _browse_image(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.frame.winfo_toplevel(), title="Choose a screen background",
            filetypes=[("PNG or GIF", "*.png *.gif"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            pixels = import_rgba_image(Path(chosen), self.frame, SCREEN_SIZE)
        except (IconError, OSError, ValueError) as error:
            messagebox.showerror(
                "Could not use background image", str(error), parent=self.frame.winfo_toplevel()
            )
            return
        self.image_rgba = pixels
        self.image_name.set(Path(chosen).name)
        self.background.set("Image")
        self.refresh_preview()

    def design(self) -> MenuDesign:
        try:
            thickness = int(self.border_width.get())
        except ValueError as error:
            raise ScreenStyleError("Border thickness must be a whole number from 0 to 24 pixels.") from error
        return MenuDesign(
            background=self.background.get(), colour=self.colour.get(),
            second_colour=self.second_colour.get(), gradient_direction=self.gradient_direction.get(),
            button_colour=self.button_colour.get(), corners=self.corners.get(),
            border=self.border.get(), border_width=thickness, border_colour=self.border_colour.get(),
        )

    def _schedule_preview(self, *_args: str) -> None:
        if self.pending_preview is not None:
            self.frame.after_cancel(self.pending_preview)
        self.pending_preview = self.frame.after(150, self.refresh_preview)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget == self.frame and self.pending_preview is not None:
            self.frame.after_cancel(self.pending_preview)
            self.pending_preview = None

    def refresh_preview(self) -> None:
        if self.pending_preview is not None:
            self.frame.after_cancel(self.pending_preview)
            self.pending_preview = None
        try:
            artwork = render_menu_artwork(self.design(), self.image_rgba)
            png = self.renderer(artwork, self.role)
            image = tk.PhotoImage(master=self.canvas, data=base64.b64encode(png))
        except (ScreenStyleError, ValueError, tk.TclError) as error:
            self.canvas.delete("all")
            self.preview = None
            self.status.set(f"Preview unavailable: {error}")
            return
        self.preview = image
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=image, anchor="nw")
        title = "Devices" if self.role == "devices" else "My Activities"
        self.canvas.create_text(7, 10, text=title, fill="white", anchor="nw")
        names = ("TV", "Receiver", "Player") if self.role == "devices" else ("Watch TV", "Music", "Movie")
        for y, name in zip(BUTTON_ROWS, names):
            self.canvas.create_text(64, y + 25, text=name, fill="white", anchor="w")
        self.status.set(
            "Artwork preview ready. Images keep their aspect ratio; padding uses the first colour. "
            "Colours are limited to the remote's RGB565 palette."
        )


class CustomStyleEditor:
    def __init__(
        self, frame: ttk.Frame, renderer: PreviewRenderer,
        on_saved: Callable[[str, RemoteStyle], None],
    ) -> None:
        self.frame, self.on_saved = frame, on_saved
        self.name = tk.StringVar(value="My custom style")
        self.status = tk.StringVar(value="Create the Activities and Devices artwork, then save it.")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(frame, highlightthickness=0)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(self.canvas, padding=10)
        window = self.canvas.create_window(0, 0, window=content, anchor="nw")
        content.bind(
            "<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width)
        )
        ttk.Label(
            content,
            text=(
                "Create a style without a donor backup. Activities and Devices have independent "
                "backgrounds and button settings. Save adds it to the existing style library; "
                "it does not write to USB. Generate and review a backup candidate on Apply styles."
            ),
            wraplength=700,
        ).pack(anchor="w", pady=(0, 8))
        self.pages = ttk.Notebook(content)
        self.pages.pack(fill="both", expand=True)
        device_frame, activity_frame = ttk.Frame(self.pages, padding=8), ttk.Frame(self.pages, padding=8)
        self.pages.add(activity_frame, text="Activities")
        self.pages.add(device_frame, text="Devices")
        self.activities = MenuDesignPage(activity_frame, "activities", renderer)
        self.devices = MenuDesignPage(device_frame, "devices", renderer)
        save_row = ttk.Frame(content)
        save_row.pack(fill="x", pady=8)
        ttk.Label(save_row, text="Style name:").pack(side="left")
        ttk.Entry(save_row, textvariable=self.name, width=28).pack(side="left", padx=8)
        self.save_button = ttk.Button(save_row, text="Save custom style", command=self._save)
        self.save_button.pack(side="left")
        ttk.Label(content, textvariable=self.status, wraplength=700).pack(anchor="w")

    def _save(self) -> None:
        try:
            style = build_custom_style(
                self.name.get(), self.activities.design(), self.devices.design(),
                self.activities.image_rgba, self.devices.image_rgba,
            )
            style_id, saved = save_user_style(self.name.get(), style)
        except (ScreenStyleError, ValueError) as error:
            self.status.set(f"Style not saved: {error}")
            messagebox.showerror("Could not save custom style", str(error), parent=self.frame.winfo_toplevel())
            return
        self.status.set(f"Saved {saved.name!r}. It is ready to generate a candidate on Apply styles.")
        self.on_saved(style_id, saved)
