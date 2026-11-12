"""Generate display-style candidates and optionally apply them over USB."""

from __future__ import annotations

import base64
import os
import queue
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from harmony_one.flash_writer import WRITE_ENV_VAR, write_configuration
from harmony_one.harmony_usb import _save_validated_backup, read_harmony_one_config
from harmony_one.harmony_usb_write import open_harmony_one
from harmony_one.custom_style_editor import CustomStyleEditor
from harmony_one.pictures import decode_picture, encode_png, picture_at
from harmony_one.screen_styles import (
    BUTTON_ROWS,
    BUTTON_X,
    GENERIC_MENU_ICON,
    ICON_X,
    SCREEN_SIZE,
    MenuArtwork,
    ScreenStyleError,
    StyleTransferResult,
    RemoteStyle,
    create_style_candidate,
    create_style_candidate_from_style,
    inspect_remote_style,
    load_builtin_styles,
    load_user_styles,
    save_user_style,
)


def _preview_png(artwork: MenuArtwork, role: str) -> bytes:
    background = artwork.background
    background_picture = picture_at(background, 0, len(background))
    if background_picture is None or (
        background_picture.width,
        background_picture.height,
    ) != SCREEN_SIZE:
        raise ScreenStyleError(f"Invalid {role} preview background")
    pixels = decode_picture(background, background_picture)

    def composite(image: bytes, x: int, y: int) -> None:
        picture = picture_at(image, 0, len(image))
        if picture is None:
            raise ScreenStyleError(f"Invalid {role} preview image")
        for image_y, row in enumerate(decode_picture(image, picture)):
            for image_x, pixel in enumerate(row):
                if pixel is not None:
                    pixels[y + image_y][x + image_x] = pixel

    for row in BUTTON_ROWS:
        composite(artwork.buttons[row], BUTTON_X, row)
        composite(GENERIC_MENU_ICON, ICON_X, row + 1)

    return encode_png(pixels)


class ScreenStyleTab:
    def __init__(self, frame: ttk.Frame, app: Any) -> None:
        self.frame = frame
        self.app = app
        self.target_blob: bytes | None = None
        self.target_path: Path | None = None
        self.builtin_styles = load_builtin_styles()
        self.user_styles: dict[str, RemoteStyle] = {}
        self.style_library = dict(self.builtin_styles)
        try:
            self.user_styles = load_user_styles()
        except ScreenStyleError as error:
            messagebox.showerror(
                "Could not load custom display styles",
                f"{error}\n\nBuilt-in styles remain available.",
                parent=self.app.root,
            )
        self.style_library.update(self.user_styles)
        self.style_ids = list(self.style_library)
        self.style_names = self._style_names(self.style_library)
        self.selected_style_name = tk.StringVar(value=self.style_names[self.style_ids[0]])
        self.custom_style_blob: bytes | None = None
        self.custom_style_path: Path | None = None
        self.pending_custom_style: RemoteStyle | None = None
        self.result: StyleTransferResult | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.writing = False
        self.allow_writes = tk.BooleanVar(value=False)
        self.target_info = tk.StringVar(value="Read or import a remote backup first.")
        self.style_info = tk.StringVar()
        self.status = tk.StringVar(value="No style candidate generated.")
        self._build()
        self._select_builtin_style()

    def _build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.sections = ttk.Notebook(self.frame)
        self.sections.grid(row=0, column=0, sticky="nsew")
        self.apply_page = ttk.Frame(self.sections, padding=8)
        self.maker_page = ttk.Frame(self.sections)
        self.sections.add(self.apply_page, text="Apply styles")
        self.sections.add(self.maker_page, text="Create custom style")
        self.apply_page.columnconfigure(0, weight=1)
        instructions = ttk.LabelFrame(
            self.apply_page, text="Activities and Devices display style", padding=12
        )
        instructions.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            instructions,
            text=(
                "Choose an included or saved custom style, then generate a separate "
                "local candidate from the currently loaded backup. This transfers only the "
                "Activities and Devices list backgrounds and row-button artwork. It replaces "
                "menu icons with one generic list icon so no old per-item icons mix visually "
                "with the selected style. Labels, data, and order are unchanged."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(instructions, textvariable=self.target_info, wraplength=850).pack(
            anchor="w", pady=(8, 2)
        )
        ttk.Label(instructions, textvariable=self.style_info, wraplength=850).pack(anchor="w")

        actions = ttk.Frame(self.apply_page)
        actions.grid(row=1, column=0, sticky="w")
        self.style_picker = ttk.Combobox(
            actions,
            textvariable=self.selected_style_name,
            values=[self.style_names[style_id] for style_id in self.style_ids],
            state="readonly",
            width=24,
        )
        self.style_picker.pack(side="left")
        self.style_picker.bind("<<ComboboxSelected>>", self._select_builtin_style)
        ttk.Button(
            actions,
            text="Preview all styles...",
            command=self._preview_styles,
        ).pack(side="left", padx=(6, 0))
        self.generate_button = ttk.Button(
            actions,
            text="Generate local candidate",
            command=self._generate,
            state="disabled",
        )
        self.generate_button.pack(side="left", padx=6)
        ttk.Button(
            actions,
            text="Use custom backup style...",
            command=self._choose_style_source,
        ).pack(side="left")
        self.save_style_button = ttk.Button(
            actions,
            text="Save custom style...",
            command=self._save_custom_style,
            state="disabled",
        )
        self.save_style_button.pack(side="left", padx=(6, 0))
        self.save_button = ttk.Button(
            actions, text="Save candidate as...", command=self._save, state="disabled"
        )
        self.save_button.pack(side="left")
        self.discard_button = ttk.Button(
            actions,
            text="Discard candidate",
            command=self._discard,
            state="disabled",
        )
        self.discard_button.pack(side="left", padx=6)

        safety = ttk.LabelFrame(self.apply_page, text="Important", padding=10)
        safety.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(
            safety,
            text=(
                "The candidate keeps the original backup unchanged, but replaces all recognized "
                "menu icons with a generic list icon. Applying it rewrites the remote configuration; "
                "a fresh USB backup must exactly match the loaded target before the writer can begin."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")
        writes = ttk.LabelFrame(self.apply_page, text="Apply style to the connected remote", padding=10)
        writes.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(
            writes,
            variable=self.allow_writes,
            command=self._update_buttons,
            text=(
                "I understand this rewrites the remote's full configuration and will keep it "
                "connected and powered until verification finishes, including replacing its "
                "menu icons with a generic list icon."
            ),
        ).pack(anchor="w")
        self.apply_button = ttk.Button(
            writes,
            text="Apply generated style to remote",
            command=self._apply_to_remote,
            state="disabled",
        )
        self.apply_button.pack(anchor="w", pady=(8, 0))
        self.progress = ttk.Progressbar(writes, maximum=100)
        self.progress.pack(fill="x", pady=(10, 0))
        ttk.Label(writes, textvariable=self.status, wraplength=850).pack(
            anchor="w", pady=(6, 0)
        )
        self.custom_editor = CustomStyleEditor(self.maker_page, _preview_png, self._created_style)

    def _created_style(self, style_id: str, style: RemoteStyle) -> None:
        self.user_styles[style_id] = style
        self._refresh_style_library()
        self.selected_style_name.set(style.name)
        self._select_builtin_style()
        self.status.set(
            f"Saved {style.name!r}. Generate a local candidate to use this artwork. "
            "Remote fonts, text colours, boldness, and sizes stay unchanged."
        )
        self.sections.select(self.apply_page)

    def load(self, blob: bytes, backup_path: Path | None) -> None:
        self.target_blob = blob
        self.target_path = backup_path
        self.custom_style_blob = None
        self.custom_style_path = None
        self.pending_custom_style = None
        self.result = None
        self.target_info.set(
            "Current target: "
            + (str(backup_path) if backup_path else "loaded backup (path not recorded)")
        )
        self._select_builtin_style()
        self.status.set("Choose an included, saved custom, or backup style. The target is unchanged.")
        self._update_buttons()

    def _selected_builtin_id(self) -> str | None:
        return next(
            (
                style_id
                for style_id in self.style_ids
                if self.style_names.get(style_id, style_id) == self.selected_style_name.get()
            ),
            None,
        )

    def _select_builtin_style(self, _event: object = None) -> None:
        style_id = self._selected_builtin_id()
        if style_id is None:
            return
        self.custom_style_blob = None
        self.custom_style_path = None
        self.pending_custom_style = None
        style = self.style_library[style_id]
        self.style_info.set(
            f"Selected style: {self.style_names.get(style_id, style_id)} "
            f"({style.fingerprint[:10]})"
        )
        self.result = None
        self.status.set("Selected style ready. Generate a candidate when ready.")
        self._update_buttons()

    def _preview_styles(self) -> tk.Toplevel:
        window = tk.Toplevel(self.app.root)
        window.title("Display style previews")
        window.minsize(760, 500)
        ttk.Label(
            window,
            text=(
                "Previews show backgrounds, row-button artwork, and the generic icon used for "
                "every menu item. Labels are illustrative. Newer Activities uses a different "
                "bottom button on its final page."
            ),
            wraplength=760,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))
        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        photos: list[tk.PhotoImage] = []
        window._preview_photos = photos

        for style_id in self.style_ids:
            style = self.style_library[style_id]
            page = ttk.Frame(notebook, padding=12)
            notebook.add(page, text=self.style_names[style_id])
            for column, (role, title, artwork) in enumerate(
                (
                    ("activities", "Activities", style.activity_menu),
                    ("devices", "Devices", style.device_menu),
                )
            ):
                group = ttk.LabelFrame(page, text=title, padding=8)
                group.grid(row=0, column=column, padx=8, sticky="n")
                canvas = tk.Canvas(
                    group,
                    width=SCREEN_SIZE[0] * 2,
                    height=SCREEN_SIZE[1] * 2,
                    highlightthickness=0,
                )
                canvas.pack(                )
                photo = tk.PhotoImage(
                    master=canvas,
                    data=base64.b64encode(_preview_png(artwork, role)).decode("ascii"),
                ).zoom(2, 2)
                photos.append(photo)
                canvas.create_image(0, 0, image=photo, anchor="nw")
                screen_title = "My Activities" if role == "activities" else "Devices"
                canvas.create_text(
                    12,
                    18,
                    text=screen_title,
                    fill="white",
                    font=("TkDefaultFont", 9, "bold"),
                    anchor="nw",
                )
                canvas.create_text(
                    12,
                    43,
                    text="1/3 pages" if role == "activities" else "1/2 pages",
                    fill="white",
                    font=("TkDefaultFont", 8),
                    anchor="nw",
                )
                canvas.create_text(
                    344,
                    43,
                    text="Mon 12:08",
                    fill="white",
                    font=("TkDefaultFont", 8),
                    anchor="ne",
                )
                labels = (
                    ("Watch TV", "Listen to Music", "Watch Movie")
                    if role == "activities"
                    else ("Living Room TV", "AV Receiver", "Disc Player")
                )
                for row, label in zip(BUTTON_ROWS, labels):
                    canvas.create_text(
                        126,
                        (row + 25) * 2,
                        text=label,
                        fill="white",
                        font=("TkDefaultFont", 9, "bold"),
                        anchor="w",
                        width=204,
                    )
        return window

    def _update_buttons(self) -> None:
        can_generate = (
            self.target_blob is not None
            and (self.custom_style_blob is not None or self._selected_builtin_id() is not None)
        )
        self.generate_button.configure(
            state="disabled" if self.writing or not can_generate else "normal"
        )
        self.save_style_button.configure(
            state=(
                "normal"
                if not self.writing and self.pending_custom_style is not None
                else "disabled"
            )
        )
        self.save_button.configure(
            state="disabled" if self.writing or not self.result else "normal"
        )
        self.discard_button.configure(
            state="disabled" if self.writing or not self.result else "normal"
        )
        self.apply_button.configure(
            state=(
                "normal"
                if not self.writing and self.result is not None and self.allow_writes.get()
                else "disabled"
            )
        )

    def _style_description(self, fingerprint: str) -> str:
        for style_id, style in self.style_library.items():
            if style.fingerprint == fingerprint:
                return self.style_names.get(style_id, style_id)
        return f"Unrecognized/custom style ({fingerprint[:10]})"

    def _choose_style_source(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.app.root,
            title="Choose a backup with the desired screen style",
            initialdir=str(
                self.target_path.parent if self.target_path else Path.cwd()
            ),
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            blob = path.read_bytes()
            style = inspect_remote_style(blob)
        except (OSError, ScreenStyleError) as error:
            messagebox.showerror(
                "Could not use style-source backup", str(error), parent=self.app.root
            )
            return
        self.custom_style_blob = blob
        self.custom_style_path = path
        self.pending_custom_style = style
        self.result = None
        self.style_info.set(
            f"Custom style source: {path} — {self._style_description(style.fingerprint)}"
        )
        self.status.set("Custom style loaded. Generate a candidate when ready.")
        self._update_buttons()

    @staticmethod
    def _style_names(styles: dict[str, RemoteStyle]) -> dict[str, str]:
        return {style_id: style.name or style_id for style_id, style in styles.items()}

    def _refresh_style_library(self) -> None:
        self.style_library = dict(self.builtin_styles)
        self.style_library.update(self.user_styles)
        self.style_ids = list(self.style_library)
        self.style_names = self._style_names(self.style_library)
        self.style_picker.configure(values=[self.style_names[key] for key in self.style_ids])

    def _save_custom_style(self) -> None:
        if self.pending_custom_style is None:
            return
        name = simpledialog.askstring(
            "Save custom display style",
            "Name this style:",
            parent=self.app.root,
        )
        if name is None:
            return
        if any(saved.casefold() == name.strip().casefold() for saved in self.style_names.values()):
            messagebox.showerror(
                "Could not save custom style",
                f"A style named {name.strip()!r} already exists.",
                parent=self.app.root,
            )
            return
        try:
            style_id, style = save_user_style(name, self.pending_custom_style)
        except ScreenStyleError as error:
            messagebox.showerror(
                "Could not save custom style", str(error), parent=self.app.root
            )
            return
        self.user_styles[style_id] = style
        self._refresh_style_library()
        self.selected_style_name.set(style.name)
        self.custom_style_blob = None
        self.custom_style_path = None
        self.pending_custom_style = None
        self._select_builtin_style()
        self.status.set(
            f"Saved {style.name!r} in the local custom-style library. "
            "It is available for future style candidates on this computer."
        )

    def _generate(self) -> None:
        if self.target_blob is None:
            return
        try:
            if self.custom_style_blob is not None:
                result = create_style_candidate(self.target_blob, self.custom_style_blob)
            else:
                style_id = self._selected_builtin_id()
                if style_id is None:
                    return
                result = create_style_candidate_from_style(
                    self.target_blob, self.style_library[style_id]
                )
        except ScreenStyleError as error:
            messagebox.showerror(
                "Style candidate was not generated", str(error), parent=self.app.root
            )
            self.status.set("No candidate was generated; the target backup remains unchanged.")
            return
        self.result = result
        style_name = self._style_description(result.style_fingerprint)
        self.status.set(
            f"{style_name}: {result.changed_pages} list pages changed, "
            f"{result.changed_references} screen references updated, "
            f"{result.appended_pictures} artwork images added. "
            f"Candidate size: {len(result.candidate):,} bytes. Nothing was written to USB."
        )
        self._update_buttons()

    def _save(self) -> None:
        if self.result is None:
            return
        stem = self.target_path.stem if self.target_path else "HarmonyOne"
        chosen = filedialog.asksaveasfilename(
            parent=self.app.root,
            title="Save offline style candidate",
            initialdir=str(self.target_path.parent if self.target_path else Path.cwd()),
            initialfile=f"{stem}-style-candidate.EZHex",
            defaultextension=".EZHex",
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        destination = Path(chosen)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(self.result.candidate)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        except OSError as error:
            detail = str(error)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    detail += f"\nTemporary file cleanup failed: {cleanup_error}"
            messagebox.showerror(
                "Could not save style candidate", detail, parent=self.app.root
            )
            return
        self.status.set(f"Saved offline candidate: {destination}. It has not been written to a remote.")

    def _discard(self) -> None:
        self.result = None
        self.status.set("Generated candidate discarded. The loaded target backup is unchanged.")
        self._update_buttons()

    def _apply_to_remote(self) -> None:
        if self.writing:
            return
        if self.result is None or self.target_blob is None:
            messagebox.showinfo(
                "Generate a style candidate first",
                "Load a backup and generate the style candidate before applying it.",
                parent=self.app.root,
            )
            return
        if self.result.candidate == self.target_blob:
            messagebox.showinfo(
                "No style changes",
                "The selected style already matches this backup. Nothing will be written.",
                parent=self.app.root,
            )
            return
        if not self.allow_writes.get():
            messagebox.showwarning(
                "Writing is off",
                "Acknowledge the full configuration rewrite first.",
                parent=self.app.root,
            )
            return
        if (
            getattr(self.app, "_backup_running", False)
            or getattr(getattr(self.app, "slideshow", None), "writing", False)
            or getattr(getattr(self.app, "clock", None), "writing", False)
        ):
            messagebox.showwarning(
                "Remote busy",
                "Wait for the current USB operation to finish before applying a style.",
                parent=self.app.root,
            )
            return
        if not messagebox.askyesno(
            "Apply style to Harmony One",
            "Apply the generated display style to the connected Harmony One?\n\n"
            "This rewrites the remote's full configuration and replaces all recognized menu "
            "icons with a generic list icon. It typically takes a few minutes. "
            "A fresh USB backup must exactly match the loaded target before any erase begins. "
            "Confirm that the intended remote is connected, charged, and ready. Keep it "
            "connected and powered until read-back verification finishes.",
            parent=self.app.root,
        ):
            return

        baseline = bytes(self.target_blob)
        candidate = bytes(self.result.candidate)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_dir = self._backup_directory()
        baseline_path = backup_dir / f"HarmonyOne-style-before-{stamp}.EZHex"
        candidate_path = backup_dir / f"HarmonyOne-style-candidate-{stamp}.EZHex"
        self.writing = True
        self.progress.configure(value=0)
        self.status.set("Taking a fresh backup and confirming it matches the loaded target.")
        self._update_buttons()

        def worker() -> None:
            previous = os.environ.get(WRITE_ENV_VAR)
            device = None
            result: tuple[bytes, Path, Path] | None = None
            failure: str | None = None
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                device, transport = open_harmony_one()
                transport.flush()
                fresh = read_harmony_one_config(
                    transport,
                    lambda done, total: self.events.put(
                        ("progress", ("Reading fresh backup", done, total))
                    ),
                )
                _save_validated_backup(baseline_path, fresh, overwrite=False)
                if fresh != baseline:
                    raise ValueError(
                        "The connected remote does not exactly match the loaded target backup. "
                        f"The fresh backup was saved to {baseline_path}; reload it and generate "
                        "the style candidate again. No configuration was written."
                    )
                _save_validated_backup(candidate_path, candidate, overwrite=False)
                os.environ[WRITE_ENV_VAR] = "1"
                write_configuration(
                    transport,
                    candidate,
                    fresh,
                    lambda done, total: self.events.put(
                        ("progress", ("Writing and verifying", done, total))
                    ),
                )
                result = (candidate, baseline_path, candidate_path)
            except Exception as error:
                failure = str(error)
                if baseline_path.exists():
                    failure += f"\nFresh backup: {baseline_path}"
                if candidate_path.exists():
                    failure += f"\nStyle candidate: {candidate_path}"
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

        threading.Thread(target=worker, name="HarmonyOneStyleWrite", daemon=True).start()
        self.frame.after(150, self._poll)

    @staticmethod
    def _backup_directory() -> Path:
        from harmony_one.app import backup_directory

        return backup_directory()

    def _poll(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                phase, done, total = payload
                self.progress.configure(value=done * 100 / total if total else 0)
                self.status.set(f"{phase}: {done:,} of {total:,} bytes.")
            elif event == "complete":
                blob, baseline_path, candidate_path = payload
                self.writing = False
                self.allow_writes.set(False)
                self.app.remote_blob = blob
                self.app.remote_backup_path = candidate_path
                self.app.slideshow.load(blob)
                self.app.backup_browser.load(blob, candidate_path)
                self.load(blob, candidate_path)
                self.progress.configure(value=100)
                self.status.set(
                    "Display style written and verified by full read-back. The remote is "
                    f"restarting. Fresh backup: {baseline_path}. Verified candidate: {candidate_path}."
                )
                messagebox.showinfo(
                    "Display style verified",
                    self.status.get(),
                    parent=self.app.root,
                )
            elif event == "error":
                self.writing = False
                self.allow_writes.set(False)
                self.status.set(f"Style write failed: {payload}")
                messagebox.showerror(
                    "Could not apply display style",
                    payload,
                    parent=self.app.root,
                )
        self._update_buttons()
        if self.writing:
            self.frame.after(150, self._poll)
