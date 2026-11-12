"""Choose one backup or imported image for one activity/device menu occurrence."""

from __future__ import annotations

import base64
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from harmony_one.activity_icons import IconError
from harmony_one.icon_library import icon_library_directory, load_user_menu_icons
from harmony_one.pictures import decode_picture, encode_png, picture_at
from harmony_one.remote_menu_icons import import_remote_menu_icon
from harmony_one.screen_styles import MenuIconOccurrence


class RemoteMenuIconDialog:
    def __init__(
        self,
        parent: tk.Misc,
        occurrences: tuple[MenuIconOccurrence, ...],
        icon_choices: tuple[bytes, ...],
        on_apply: Callable[[MenuIconOccurrence, bytes], bool],
        *,
        apply_to_all_occurrences: bool = False,
        instructions: str | None = None,
        show_occurrence_list: bool = False,
    ) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("Choose remote menu icon")
        self.window.transient(parent)
        self.window.geometry("620x420")
        self.window.minsize(520, 360)
        self.occurrences = occurrences
        self.on_apply = on_apply
        self.apply_to_all_occurrences = apply_to_all_occurrences
        self.occurrence_by_label = {
            occurrence.description: occurrence for occurrence in occurrences
        }
        self.icon_by_label = {
            f"Backup image {index}": data
            for index, data in enumerate(icon_choices, start=1)
        }
        user_icons, self.user_icon_errors = load_user_menu_icons(self.window)
        for label, data in user_icons:
            self.icon_by_label[label] = data
        self.occurrence = tk.StringVar(value=occurrences[0].description)
        self.icon_choice = tk.StringVar()
        self.preview: tk.PhotoImage | None = None
        self.occurrence_listbox: tk.Listbox | None = None

        body = ttk.Frame(self.window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=instructions
            or (
                "Choose an icon for this item. It will be applied to all matching menu "
                "rows in the backup; other items and the existing display style are preserved."
                if apply_to_all_occurrences
                else "Choose one menu-page occurrence and its icon. Changes affect only this "
                "occurrence; other pages and the backup's existing display style are preserved."
            ),
            wraplength=570,
            justify="left",
        ).pack(anchor="w")
        self.occurrence_selector: ttk.Combobox | None = None
        if show_occurrence_list:
            ttk.Label(body, text="Activity rows in this backup:").pack(
                anchor="w", pady=(12, 4)
            )
            list_frame = ttk.Frame(body)
            list_frame.pack(fill="x", pady=(0, 8))
            scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
            self.occurrence_listbox = tk.Listbox(
                list_frame,
                height=min(5, len(occurrences)),
                exportselection=False,
                yscrollcommand=scrollbar.set,
            )
            scrollbar.configure(command=self.occurrence_listbox.yview)
            self.occurrence_listbox.pack(side="left", fill="x", expand=True)
            scrollbar.pack(side="right", fill="y")
            self.occurrence_listbox.insert(
                "end", *(occurrence.description for occurrence in occurrences)
            )
            self.occurrence_listbox.selection_set(0)
            self.occurrence_listbox.bind(
                "<<ListboxSelect>>", self._occurrence_changed
            )
        elif not apply_to_all_occurrences:
            occurrence_row = ttk.Frame(body)
            occurrence_row.pack(fill="x", pady=(12, 8))
            ttk.Label(occurrence_row, text="Menu button:").pack(side="left")
            self.occurrence_selector = ttk.Combobox(
                occurrence_row,
                textvariable=self.occurrence,
                values=tuple(self.occurrence_by_label),
                state="readonly",
            )
            self.occurrence_selector.pack(side="left", fill="x", expand=True, padx=(8, 0))
            self.occurrence_selector.bind("<<ComboboxSelected>>", self._occurrence_changed)

        icon_row = ttk.Frame(body)
        icon_row.pack(fill="x")
        ttk.Label(icon_row, text="Icon:").pack(side="left")
        self.icon_selector = ttk.Combobox(
            icon_row,
            textvariable=self.icon_choice,
            values=tuple(self.icon_by_label),
            state="readonly",
            width=24,
        )
        self.icon_selector.pack(side="left", padx=(8, 0))
        self.icon_selector.bind("<<ComboboxSelected>>", self._icon_changed)
        ttk.Button(
            icon_row,
            text="Import PNG/GIF...",
            command=self._import_icon,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            body,
            text=f"User icons folder: {icon_library_directory()}",
            wraplength=570,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        if self.user_icon_errors:
            ttk.Label(
                body,
                text=(
                    "Some images in the user icons folder could not be loaded: "
                    + "; ".join(self.user_icon_errors)
                ),
                wraplength=570,
                justify="left",
            ).pack(anchor="w", pady=(4, 0))
        self.preview_label = ttk.Label(body, text="No icon images found in this backup.")
        self.preview_label.pack(expand=True, pady=10)
        ttk.Label(
            body,
            text="Preview uses this row's current button artwork; transparent icon pixels show the style underneath.",
            wraplength=570,
            justify="left",
        ).pack(anchor="w")

        ttk.Label(
            body,
            text=(
                "Remote images already in this backup are shown first. PNG/GIF imports are "
                "fitted to the remote's 51 x 48 icon size; GIF uses its first frame. "
                "The edited backup must be saved as a new .EZHex file before upload."
            ),
            wraplength=570,
            justify="left",
        ).pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right")
        apply_text = "Apply to matching rows" if apply_to_all_occurrences else "Apply to this button"
        self.apply_button = ttk.Button(buttons, text=apply_text, command=self._apply)
        self.apply_button.pack(side="right", padx=(0, 6))
        self.apply_button.configure(state="normal" if self.icon_by_label else "disabled")

        self._occurrence_changed()
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.grab_set()

    def _occurrence_changed(self, _event: Any = None) -> None:
        occurrence = self._selected_occurrence()
        if occurrence is None:
            return
        matching = next(
            (
                label
                for label, data in self.icon_by_label.items()
                if data == occurrence.picture_data
            ),
            None,
        )
        if matching is not None:
            self.icon_choice.set(matching)
            self._show_preview(occurrence, occurrence.picture_data)
        elif self.icon_choice.get() in self.icon_by_label:
            self._icon_changed()

    def _icon_changed(self, _event: Any = None) -> None:
        picture_data = self.icon_by_label.get(self.icon_choice.get())
        occurrence = self._selected_occurrence()
        if picture_data is not None and occurrence is not None:
            self._show_preview(occurrence, picture_data)

    def _selected_occurrence(self) -> MenuIconOccurrence | None:
        if self.apply_to_all_occurrences:
            return self.occurrences[0] if self.occurrences else None
        if self.occurrence_listbox is not None:
            selection = self.occurrence_listbox.curselection()
            return self.occurrences[selection[0]] if selection else None
        return self.occurrence_by_label.get(self.occurrence.get())

    def _show_preview(
        self, occurrence: MenuIconOccurrence, picture_data: bytes
    ) -> None:
        button = picture_at(occurrence.button_data, 0, len(occurrence.button_data))
        icon = picture_at(picture_data, 0, len(picture_data))
        if (
            button is None
            or button.end != len(occurrence.button_data)
            or icon is None
            or icon.end != len(picture_data)
        ):
            messagebox.showerror(
                "Invalid backup icon",
                "The selected button or icon is not a valid complete picture.",
                parent=self.window,
            )
            return
        pixels = decode_picture(occurrence.button_data, button)
        icon_pixels = decode_picture(picture_data, icon)
        for y, row in enumerate(icon_pixels):
            for x, pixel in enumerate(row):
                if pixel is not None:
                    pixels[y + 1][x + 5] = pixel
        png = encode_png(pixels)
        self.preview = tk.PhotoImage(master=self.window, data=base64.b64encode(png))
        self.preview_label.configure(image=self.preview, text="")

    def _import_icon(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.window,
            title="Import a remote menu icon",
            initialdir=str(icon_library_directory()),
            filetypes=[("PNG or GIF", "*.png *.gif"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            picture = import_remote_menu_icon(Path(chosen), self.window)
        except (IconError, OSError, ValueError) as error:
            messagebox.showerror(
                "Could not import icon",
                str(error),
                parent=self.window,
            )
            return
        label = f"Custom image: {Path(chosen).name}"
        suffix = 2
        while label in self.icon_by_label and self.icon_by_label[label] != picture:
            label = f"Custom image: {Path(chosen).name} ({suffix})"
            suffix += 1
        self.icon_by_label[label] = picture
        self.icon_selector.configure(values=tuple(self.icon_by_label))
        self.icon_choice.set(label)
        occurrence = self._selected_occurrence()
        if occurrence is None:
            return
        self._show_preview(occurrence, picture)
        self.apply_button.configure(state="normal")

    def _apply(self) -> None:
        picture = self.icon_by_label.get(self.icon_choice.get())
        occurrence = self._selected_occurrence()
        if picture is None or occurrence is None:
            messagebox.showwarning(
                "Choose a menu icon",
                "Select a backup image or import a PNG/GIF icon first.",
                parent=self.window,
            )
            return
        if self.on_apply(occurrence, picture):
            self.window.destroy()
