#!/usr/bin/env python3
"""Portable, offline editor for a local Harmony device/activity project."""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk
from typing import Any

from harmony_one import __version__
from harmony_one.app_paths import (
    backup_directory as app_backup_directory,
    portable_directory as app_portable_directory,
)
from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.activity_encoder import prepare_activity_rename
from harmony_one.activity_icon_dialog import ActivityIconDialog
from harmony_one.activity_icons import make_preview
from harmony_one.activity_upload_dialog import ActivityUploadDialog
from harmony_one.harmony_project import (
    ProjectError,
    MAX_DELAY_MS,
    MIN_DELAY_MS,
    device_from_catalog,
    load_project,
    move_item,
    new_project,
    project_from_remote,
    save_project,
    validate_project,
)
from harmony_one.import_ir import parse_ir
from harmony_one.harmony_usb import BackupError, backup_connected_harmony_one
from harmony_one.backup_browser import BackupBrowserTab
from harmony_one.clock_tab import ClockTab
from harmony_one.icon_library import (
    IconLibraryError,
    export_remote_menu_icons,
    icon_library_directory,
    seed_default_icons,
)
from harmony_one.object_library import build_remote_objects, export_remote_objects
from harmony_one.project_objects import export_project_object, import_project_object
from harmony_one.remote_writer import plan_changes
from harmony_one.screen_style_tab import ScreenStyleTab
from harmony_one.screen_styles import load_builtin_styles
from harmony_one.slideshow_tab import SlideshowTab
from harmony_one.remote_assistant_tab import RemoteAssistantTab
from harmony_one.save_restore import validate_source_save
from harmony_one.save_restore_tab import FQ_PRESET_FILES, SaveRestoreTab
from harmony_one.feature_tracker import load_feature_catalog
from harmony_one.remote_preview import (
    RemotePreview,
    RemotePreviewPage,
    read_remote_preview,
    render_remote_preview_page,
)


APP_NAME = "FQ's Harmony Remote Project"
DEFAULT_PROJECT_NAME = "HarmonyProject.json"
ITEMS_PER_PAGE = 3
CARD_PAGE_SIZE = 12


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def bundled_project() -> dict[str, Any]:
    """Return a project containing the bundled IR library devices.

    Used by the library picker and the self-test; new user projects start blank.
    """
    project = new_project()
    catalog_path = resource_path("data/catalogs.json")
    if catalog_path.exists():
        catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalogs = catalog_data.get("catalogs") if isinstance(catalog_data, dict) else None
        if not isinstance(catalogs, list):
            raise ValueError(f"Bundled IR library is malformed: {catalog_path}")
        for catalog in catalogs:
            project["devices"].append(device_from_catalog(catalog))
    return validate_project(project)


def portable_directory() -> Path:
    return app_portable_directory()


def backup_directory() -> Path:
    return app_backup_directory(portable_directory())


def list_logitech_usb_devices() -> list[tuple[str, str]]:
    if os.name != "nt":
        raise OSError("USB device discovery is currently available only on Windows")

    script = (
        "$ErrorActionPreference = 'Stop'; "
        "Get-PnpDevice -PresentOnly | "
        "Where-Object { $_.InstanceId -match '^(USB|HID)\\\\VID_046D' } | "
        "Select-Object -Property FriendlyName,InstanceId | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"PowerShell exited with code {result.returncode}"
        raise OSError(f"Could not query Windows Plug and Play devices: {detail}")
    if not result.stdout.strip():
        return []

    payload = json.loads(result.stdout)
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise OSError("Windows Plug and Play returned an unexpected device list")
    devices: list[tuple[str, str]] = []
    for device in payload:
        if isinstance(device, dict):
            name = device.get("FriendlyName")
            instance_id = device.get("InstanceId")
            if isinstance(name, str) and isinstance(instance_id, str):
                devices.append((name, instance_id))
    return devices


def find_failed_harmony_usb_device() -> tuple[str, str] | None:
    """Return (instance_id, status) for a present but non-working Harmony One, if any."""
    if os.name != "nt":
        return None
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "Get-PnpDevice -PresentOnly | "
        "Where-Object { $_.InstanceId -match 'VID_046D&PID_C121' -and $_.Status -ne 'OK' } | "
        "Select-Object -Property InstanceId,Status | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return None
    for device in payload:
        if isinstance(device, dict):
            instance_id = device.get("InstanceId")
            status = device.get("Status")
            if isinstance(instance_id, str) and instance_id:
                return instance_id, status if isinstance(status, str) else "Unknown"
    return None


def restart_usb_device_elevated(instance_id: str) -> bool:
    """Restart a PnP device with an elevated pnputil call; shows a UAC prompt."""
    if os.name != "nt":
        return False
    if any(ch in instance_id for ch in "'\";`$"):
        return False
    script = (
        "$p = Start-Process -FilePath pnputil.exe -ArgumentList "
        f"'/restart-device \"{instance_id}\"' -Verb RunAs -Wait -PassThru; "
        "exit $p.ExitCode"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class HarmonyOfflineApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1040x730")
        self.root.minsize(820, 580)
        self.project = validate_project(new_project())
        self.project_path = portable_directory() / DEFAULT_PROJECT_NAME
        self.project_saved = False
        self.dirty = False
        self.remote_preview: RemotePreview | None = None
        self.home_preview_page_labels: dict[str, RemotePreviewPage] = {}
        self.selected_device_id: str | None = None
        self.selected_activity_id: str | None = None
        self.device_tree_iids: dict[str, str] = {}
        self._backup_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._backup_running = False
        self._import_after_backup = False
        self._export_after_backup = False
        self._browse_after_backup = False
        self._export_icons_after_backup = False
        self.remote_blob: bytes | None = None
        self.remote_backup_path: Path | None = None
        self.remote_project: dict[str, Any] | None = None
        self.activity_upload_dialog: ActivityUploadDialog | None = None
        self.device_card_page = 0
        self.activity_card_page = 0

        self._configure_styles()
        if self.project_path.exists():
            try:
                self.project = load_project(self.project_path)
                self.project_saved = True
            except ProjectError as error:
                messagebox.showerror("Remote Data could not be opened", str(error))
                self.project_path = portable_directory() / "HarmonyProject-new.json"

        self._build_ui()
        self._refresh_devices()
        self._refresh_activities()
        self._update_title()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Harmony.Header.TFrame", background="#087f82")
        style.configure(
            "Harmony.Brand.TLabel",
            background="#087f82",
            foreground="white",
            font=("Segoe UI", 18, "bold"),
        )
        style.configure(
            "Harmony.Subtitle.TLabel",
            background="#087f82",
            foreground="#d8f3f1",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Harmony.TNotebook",
            background="#dce8f6",
            borderwidth=0,
            tabmargins=(6, 5, 6, 0),
        )
        style.configure(
            "Harmony.TNotebook.Tab",
            background="#d4e4f7",
            foreground="#1b4e7a",
            padding=(16, 8),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Harmony.TNotebook.Tab",
            background=[("selected", "#ffffff"), ("active", "#eaf3fc")],
            foreground=[("selected", "#087f82")],
        )
        style.configure(
            "Harmony.Card.TLabelframe",
            background="#eaf3fc",
            bordercolor="#9bb9d8",
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Harmony.Card.TLabelframe.Label",
            background="#eaf3fc",
            foreground="#164e78",
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Harmony.Primary.TButton",
            background="#4c91ce",
            foreground="white",
            padding=(10, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Harmony.Primary.TButton",
            background=[("active", "#367cb9"), ("pressed", "#286aa3")],
        )
        style.configure(
            "Harmony.USBActivity.TButton",
            background="#ffe184",
            foreground="#332800",
            padding=(8, 4),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Harmony.USBActivity.TButton",
            background=[("active", "#ffcf49"), ("pressed", "#f0bd32")],
        )
        style.configure("Harmony.Muted.TLabel", foreground="#536b83")

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, style="Harmony.Header.TFrame", padding=(18, 8))
        header.pack(fill="x")
        brand = ttk.Frame(header, style="Harmony.Header.TFrame")
        brand.pack(side="left")
        ttk.Label(brand, text=APP_NAME, style="Harmony.Brand.TLabel").pack(anchor="w")
        ttk.Label(
            brand,
            text="Model: Harmony One",
            style="Harmony.Subtitle.TLabel",
        ).pack(anchor="w")
        self.header_status = ttk.Label(
            header, style="Harmony.Subtitle.TLabel", justify="right", wraplength=360
        )
        self.header_status.pack(side="right")
        self.usb_activity_text = tk.StringVar(value="")
        self.usb_activity_button = ttk.Button(
            header,
            textvariable=self.usb_activity_text,
            style="Harmony.USBActivity.TButton",
            command=lambda: self.tabs.select(self.usb_tab),
        )

        self.tabs = ttk.Notebook(self.root, style="Harmony.TNotebook")
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.home_tab = ttk.Frame(self.tabs, padding=10)
        self.device_tab = ttk.Frame(self.tabs, padding=10)
        self.activity_tab = ttk.Frame(self.tabs, padding=10)
        self.usb_tab = ttk.Frame(self.tabs, padding=10)
        self.slideshow_frame = ttk.Frame(self.tabs, padding=10)
        self.clock_frame = ttk.Frame(self.tabs, padding=10)
        self.remote_assistant_frame = ttk.Frame(self.tabs, padding=10)
        self.screen_style_frame = ttk.Frame(self.tabs, padding=10)
        self.icons_frame = ttk.Frame(self.tabs, padding=10)
        self.backup_transfer_frame = ttk.Frame(self.tabs, padding=10)
        self.backup_transfer_tabs = ttk.Notebook(
            self.backup_transfer_frame, style="Harmony.TNotebook"
        )
        self.backup_transfer_tabs.pack(fill="both", expand=True)
        self.backup_frame = ttk.Frame(self.backup_transfer_tabs, padding=10)
        self.restore_save_frame = ttk.Frame(self.backup_transfer_tabs, padding=10)
        self.tabs.add(self.home_tab, text="Home")
        self.tabs.add(self.activity_tab, text="Activities")
        self.tabs.add(self.device_tab, text="Devices")
        self.tabs.add(self.backup_transfer_frame, text="Backups & Transfer")
        self.backup_transfer_tabs.add(self.restore_save_frame, text="Transfer")
        self.backup_transfer_tabs.add(self.backup_frame, text="Browse Contents")
        self.tabs.add(self.slideshow_frame, text="Slideshow")
        self.tabs.add(self.clock_frame, text="Clock")
        self.tabs.add(self.remote_assistant_frame, text="Remote Assistant")
        self.tabs.add(self.screen_style_frame, text="Display Style")
        self.tabs.add(self.icons_frame, text="Icons")
        self.tabs.add(self.usb_tab, text="USB")
        self._build_home_tab()
        self._build_device_tab()
        self._build_activity_tab()
        self._build_usb_tab()
        self.slideshow = SlideshowTab(self.slideshow_frame, self)
        self.clock = ClockTab(self.clock_frame, self)
        self.remote_assistant = RemoteAssistantTab(self.remote_assistant_frame, self)
        self.screen_styles = ScreenStyleTab(self.screen_style_frame, self)
        self._build_icons_page()
        self.backup_browser = BackupBrowserTab(self.backup_frame, self)
        self.save_restore = SaveRestoreTab(self.restore_save_frame, self)
        self.status = tk.StringVar(value="Offline Remote Data editor")
        ttk.Label(self.root, textvariable=self.status, anchor="w", relief="sunken").pack(
            fill="x", side="bottom"
        )

    def _show_usb_backup_activity(self, percent: float | None = None) -> None:
        detail = "Reading" if percent is None else f"Reading {percent:.0f}%"
        self.tabs.tab(self.usb_tab, text=f"USB - {detail}")
        self.usb_activity_text.set(f"USB: {detail} (view)")
        if not self.usb_activity_button.winfo_manager():
            self.usb_activity_button.pack(side="right", padx=(8, 0))

    def _clear_usb_backup_activity(self) -> None:
        self.tabs.tab(self.usb_tab, text="USB")
        self.usb_activity_button.pack_forget()
        self.usb_activity_text.set("")

    def _build_home_tab(self) -> None:
        self.home_tab.columnconfigure(0, weight=0)
        self.home_tab.columnconfigure(1, weight=1)
        self.home_tab.rowconfigure(1, weight=1)
        ttk.Label(
            self.home_tab,
            text="Welcome to Harmony Remote Manager",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(4, 10))

        workspace_card = ttk.LabelFrame(
            self.home_tab,
            text="Connected remote",
            style="Harmony.Card.TLabelframe",
            padding=12,
        )
        workspace_card.grid(row=1, column=0, sticky="new", padx=(0, 12))
        ttk.Label(
            workspace_card,
            style="Harmony.Muted.TLabel",
            wraplength=300,
            justify="left",
            text=(
                "Back up the connected Harmony One and open a separate Remote Data copy "
                "for editing. Your complete original backup is preserved."
            ),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.home_edit_copy_button = ttk.Button(
            workspace_card,
            text="Back up connected Harmony One Remote and start editing a copy...",
            style="Harmony.Primary.TButton",
            command=self._read_from_remote,
        )
        self.home_edit_copy_button.grid(row=1, column=0, sticky="w", pady=2)
        self.home_backup_folder_button = ttk.Button(
            workspace_card,
            text="Open backup folder",
            command=self._open_backup_folder,
        )
        self.home_backup_folder_button.grid(row=2, column=0, sticky="w", pady=2)

        preview_card = ttk.LabelFrame(
            self.home_tab,
            text="Scanned remote preview",
            style="Harmony.Card.TLabelframe",
            padding=10,
        )
        preview_card.grid(row=1, column=1, sticky="nsew")
        preview_card.columnconfigure(0, weight=1)
        preview_card.rowconfigure(2, weight=1)
        selectors = ttk.Frame(preview_card)
        selectors.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(selectors, text="Menu:").pack(side="left")
        self.home_preview_role = tk.StringVar(value="Activities")
        self.home_preview_role_combo = ttk.Combobox(
            selectors,
            textvariable=self.home_preview_role,
            values=("Activities", "Devices"),
            state="readonly",
            width=13,
        )
        self.home_preview_role_combo.pack(side="left", padx=(5, 12))
        self.home_preview_role_combo.bind(
            "<<ComboboxSelected>>", self._on_home_preview_role_changed
        )
        ttk.Label(selectors, text="Page:").pack(side="left")
        self.home_preview_page = tk.StringVar()
        self.home_preview_page_combo = ttk.Combobox(
            selectors,
            textvariable=self.home_preview_page,
            state="readonly",
            width=16,
        )
        self.home_preview_page_combo.pack(side="left", padx=(5, 0))
        self.home_preview_page_combo.bind(
            "<<ComboboxSelected>>", self._on_home_preview_page_changed
        )
        self.home_preview_style = tk.StringVar(value="Style: Not scanned")
        ttk.Label(
            preview_card,
            textvariable=self.home_preview_style,
            style="Harmony.Muted.TLabel",
            wraplength=350,
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.home_preview_canvas = tk.Canvas(
            preview_card,
            width=370,
            height=460,
            background="#dedede",
            highlightthickness=0,
        )
        self.home_preview_canvas.grid(row=2, column=0, sticky="n")
        self.home_preview_placeholder = self.home_preview_canvas.create_text(
            185,
            230,
            text="Scan or load a remote backup to see its menu.",
            width=330,
            justify="center",
            fill="#333333",
        )

        self.home_summary = tk.StringVar()
        ttk.Label(
            self.home_tab,
            textvariable=self.home_summary,
            style="Harmony.Muted.TLabel",
            wraplength=880,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self._refresh_home_summary()

    def _on_home_preview_role_changed(self, _event: tk.Event | None = None) -> None:
        self._refresh_home_preview_choices()

    def _on_home_preview_page_changed(self, _event: tk.Event | None = None) -> None:
        self._render_home_preview_page()

    def _load_home_preview(self, blob: bytes) -> None:
        try:
            self.remote_preview = read_remote_preview(blob)
        except ValueError as error:
            self.remote_preview = None
            self.home_preview_style.set(f"Preview unavailable: {error}")
            self.home_preview_canvas.delete("all")
            self.home_preview_canvas.create_text(
                185, 230, text=str(error), width=330, justify="center", fill="#8b0000"
            )
            self.home_preview_role_combo.configure(values=())
            self.home_preview_page_combo.configure(values=())
            self.home_preview_page_labels.clear()
            return
        self.home_preview_style.set(
            f"Style: {self.remote_preview.style_name} "
            f"({self.remote_preview.style_fingerprint[:12]})"
        )
        self._refresh_home_preview_choices()

    def _refresh_home_preview_choices(self) -> None:
        if self.remote_preview is None:
            return
        role = "activities" if self.home_preview_role.get() == "Activities" else "devices"
        pages = [page for page in self.remote_preview.pages if page.role == role]
        available_roles = {
            "Activities" if page.role == "activities" else "Devices"
            for page in self.remote_preview.pages
        }
        roles = tuple(label for label in ("Activities", "Devices") if label in available_roles)
        self.home_preview_role_combo.configure(values=roles)
        if self.home_preview_role.get() not in roles and roles:
            self.home_preview_role.set(roles[0])
            role = "activities" if roles[0] == "Activities" else "devices"
            pages = [page for page in self.remote_preview.pages if page.role == role]
        self.home_preview_page_labels = {
            f"Page {page.page + 1}": page
            for page in pages
        }
        page_labels = tuple(self.home_preview_page_labels)
        self.home_preview_page_combo.configure(values=page_labels)
        if page_labels:
            self.home_preview_page.set(page_labels[0])
            self._render_home_preview_page()
        else:
            self.home_preview_canvas.delete("all")
            self.home_preview_canvas.create_text(
                185, 230, text="No menu pages for this selection.", fill="#333333"
            )

    def _render_home_preview_page(self) -> None:
        page = self.home_preview_page_labels.get(self.home_preview_page.get())
        if page is None:
            return
        try:
            image_data = render_remote_preview_page(page)
            photo = tk.PhotoImage(
                master=self.root, data=base64.b64encode(image_data).decode("ascii")
            )
        except ValueError as error:
            self.home_preview_canvas.delete("all")
            self.home_preview_canvas.create_text(
                185, 230, text=f"Could not render menu page:\n{error}", fill="#8b0000"
            )
            return
        self.home_preview_photo = photo
        self.home_preview_canvas.delete("all")
        self.home_preview_canvas.create_image(8, 8, image=photo, anchor="nw")
        for row in page.rows:
            x = 8 + 75
            y = 8 + row.y + 25
            self.home_preview_canvas.create_text(
                x + 1,
                y + 1,
                text=row.name,
                width=82,
                anchor="w",
                fill="#111111",
                font=("Segoe UI", 7, "bold"),
            )
            self.home_preview_canvas.create_text(
                x,
                y,
                text=row.name,
                width=82,
                anchor="w",
                fill="#ffffff",
                font=("Segoe UI", 7, "bold"),
            )

    def _build_icons_page(self) -> None:
        self.icons_frame.columnconfigure(0, weight=1)
        try:
            seed_default_icons()
        except IconLibraryError as error:
            seed_status = f"Could not initialize default icons: {error}"
        else:
            seed_status = "Bundled default icons are installed; added images appear in the icon picker."
        ttk.Label(
            self.icons_frame,
            text="Remote menu icon library",
            font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.icons_frame,
            text=(
                "Scan a connected Harmony One to export its distinct activity and device "
                "menu icons. Add PNG/GIF files to the user icons folder to use them in the "
                "Change icon picker."
            ),
            wraplength=880,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))
        ttk.Label(
            self.icons_frame,
            text=seed_status,
            wraplength=880,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Label(
            self.icons_frame,
            text=f"Icons folder: {icon_library_directory()}",
            wraplength=880,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(0, 8))
        controls = ttk.Frame(self.icons_frame)
        controls.grid(row=4, column=0, sticky="w")
        self.export_icons_button = ttk.Button(
            controls,
            text="Scan remote and save menu icons",
            command=self._scan_and_export_icons,
        )
        self.export_icons_button.pack(side="left")
        ttk.Button(
            controls,
            text="Open icons folder",
            command=self._open_icons_folder,
        ).pack(side="left", padx=(8, 0))

    def _open_backup_folder(self) -> None:
        directory = backup_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(directory))
        except OSError as error:
            messagebox.showerror(
                "Could not open backup folder",
                f"Could not open {directory}:\n{error}",
                parent=self.root,
            )

    def _home_browse_backup(self) -> None:
        self.tabs.select(self.backup_transfer_frame)
        self.backup_transfer_tabs.select(self.backup_frame)
        self.backup_browser._open_file()

    def _refresh_home_summary(self) -> None:
        if not hasattr(self, "home_summary"):
            return
        devices = len(self.project.get("devices", []))
        activities = len(self.project.get("activities", []))
        self.home_summary.set(
            f"Current Remote Data: {self.project_path.name} - {devices} device(s), "
            f"{activities} activity(ies). Backups are saved to {backup_directory()}."
        )

    def _build_device_tab(self) -> None:
        self.device_tab.columnconfigure(0, weight=1)
        self.device_tab.rowconfigure(1, weight=1)
        actions = ttk.Frame(self.device_tab)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(
            actions, text="Add Device", style="Harmony.Primary.TButton", command=self._add_device
        ).pack(side="left")
        ttk.Button(
            actions, text="Add from IR library...", command=self._add_device_from_library
        ).pack(side="left", padx=5)
        ttk.Button(actions, text="Import IR file...", command=self._import_ir).pack(
            side="left", padx=5
        )
        ttk.Button(
            actions,
            text="Import device object...",
            command=lambda: self._import_project_object("device"),
        ).pack(side="left", padx=5)
        ttk.Button(
            actions, text="Re-Order Devices", command=lambda: self._open_reorder_dialog("devices")
        ).pack(side="left", padx=5)
        self.device_page_label = ttk.Label(actions, style="Harmony.Muted.TLabel")
        self.device_page_label.pack(side="right", padx=6)
        ttk.Button(actions, text="Next", command=lambda: self._change_card_page("devices", 1)).pack(
            side="right"
        )
        ttk.Button(
            actions, text="Previous", command=lambda: self._change_card_page("devices", -1)
        ).pack(side="right", padx=4)

        content = ttk.Frame(self.device_tab)
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.device_canvas = tk.Canvas(
            content, background="#f4f8fc", highlightthickness=0, borderwidth=0
        )
        self.device_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.device_canvas.yview)
        scrollbar.grid(row=0, column=0, sticky="nse")
        self.device_canvas.configure(yscrollcommand=scrollbar.set)
        self.device_cards = ttk.Frame(self.device_canvas)
        self.device_canvas_window = self.device_canvas.create_window(
            (0, 0), window=self.device_cards, anchor="nw"
        )
        self.device_cards.bind(
            "<Configure>",
            lambda _event: self.device_canvas.configure(
                scrollregion=self.device_canvas.bbox("all")
            ),
        )
        self.device_canvas.bind(
            "<Configure>",
            lambda event: self.device_canvas.itemconfigure(
                self.device_canvas_window, width=event.width - 18
            ),
        )
        self.device_cards.columnconfigure(0, weight=1)
        self.device_cards.columnconfigure(1, weight=1)
        ttk.Label(
            self.device_tab,
            text=(
                "Commands edit local Remote Data. Change icon edits that device's "
                "left-side Harmony menu-row icon in a separate backup candidate; load "
                "the matching backup first. General Remote Data edits are not uploadable."
            ),
            style="Harmony.Muted.TLabel",
            wraplength=800,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.device_list = tk.Listbox(self.device_tab, exportselection=False)
        self.device_order_list = tk.Listbox(self.device_tab, exportselection=False)

        self.device_settings_window = tk.Toplevel(self.root)
        self.device_settings_window.withdraw()
        self.device_settings_window.title("Device Settings")
        self.device_settings_window.geometry("760x620")
        self.device_settings_window.transient(self.root)
        self.device_settings_window.protocol(
            "WM_DELETE_WINDOW", self.device_settings_window.withdraw
        )
        details = ttk.LabelFrame(
            self.device_settings_window, text="Device settings", padding=12
        )
        details.pack(fill="both", expand=True, padx=10, pady=10)
        details.columnconfigure(1, weight=1)
        self.device_name = tk.StringVar()
        self.device_brand = tk.StringVar()
        self.device_category = tk.StringVar()
        self._entry_row(details, "Name", self.device_name, 0)
        self._entry_row(details, "Brand", self.device_brand, 1)
        self._entry_row(details, "Category", self.device_category, 2)
        ttk.Button(details, text="Apply details", command=self._apply_device_details).grid(
            row=3, column=1, sticky="e", pady=(6, 10)
        )
        ttk.Label(details, text="IR commands").grid(row=4, column=0, columnspan=2, sticky="w")
        self.command_tree = ttk.Treeview(
            details,
            columns=("name", "protocol", "address", "command"),
            show="headings",
            height=15,
        )
        self.command_tree.heading("name", text="Button")
        self.command_tree.heading("protocol", text="Protocol")
        self.command_tree.heading("address", text="Address")
        self.command_tree.heading("command", text="Command")
        self.command_tree.column("name", width=150)
        self.command_tree.column("protocol", width=150, stretch=False)
        self.command_tree.column("address", width=150, stretch=False)
        self.command_tree.column("command", width=180)
        self.command_tree.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        details.rowconfigure(5, weight=1)
        self.command_tree.bind("<<TreeviewSelect>>", self._command_selected)
        self.command_name = tk.StringVar()
        ttk.Label(details, text="Selected command name").grid(
            row=6, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(details, textvariable=self.command_name).grid(
            row=6, column=1, sticky="ew", pady=(8, 0)
        )
        self.command_protocol = tk.StringVar()
        self.command_address = tk.StringVar()
        self.command_code = tk.StringVar()
        self._entry_row(details, "Protocol", self.command_protocol, 7)
        self._entry_row(details, "Address", self.command_address, 8)
        self._entry_row(details, "Command code", self.command_code, 9)
        command_buttons = ttk.Frame(details)
        command_buttons.grid(row=10, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(command_buttons, text="Add button", command=self._add_command).pack(
            side="left", padx=3
        )
        ttk.Button(command_buttons, text="Apply signal", command=self._apply_command).pack(
            side="left", padx=3
        )
        ttk.Button(command_buttons, text="Remove button", command=self._remove_command).pack(
            side="left", padx=3
        )
        ttk.Button(
            details,
            text="Export device object...",
            command=lambda: self._export_selected_project_object("device"),
        ).grid(row=11, column=0, sticky="w", pady=(8, 0))
        ttk.Button(
            details, text="Done", command=self.device_settings_window.withdraw
        ).grid(row=11, column=1, sticky="e", pady=(8, 0))

    def _entry_row(
        self, frame: ttk.LabelFrame, label: str, variable: tk.StringVar, row: int
    ) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(8, 0), pady=3
        )

    def _build_activity_tab(self) -> None:
        self.activity_tab.columnconfigure(0, weight=1)
        self.activity_tab.rowconfigure(1, weight=1)
        actions = ttk.Frame(self.activity_tab)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(
            actions,
            text="Add Activity",
            style="Harmony.Primary.TButton",
            command=self._add_activity,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Re-Order Activities",
            command=lambda: self._open_reorder_dialog("activities"),
        ).pack(side="left", padx=5)
        ttk.Button(
            actions, text="Add to remote (experimental)...", command=self._add_remote_activity
        ).pack(side="left", padx=5)
        ttk.Button(
            actions,
            text="Import activity object...",
            command=lambda: self._import_project_object("activity"),
        ).pack(side="left", padx=5)
        self.activity_page_label = ttk.Label(actions, style="Harmony.Muted.TLabel")
        self.activity_page_label.pack(side="right", padx=6)
        ttk.Button(
            actions,
            text="Next",
            command=lambda: self._change_card_page("activities", 1),
        ).pack(side="right")
        ttk.Button(
            actions,
            text="Previous",
            command=lambda: self._change_card_page("activities", -1),
        ).pack(side="right", padx=4)

        content = ttk.Frame(self.activity_tab)
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.activity_canvas = tk.Canvas(
            content, background="#f4f8fc", highlightthickness=0, borderwidth=0
        )
        self.activity_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            content, orient="vertical", command=self.activity_canvas.yview
        )
        scrollbar.grid(row=0, column=0, sticky="nse")
        self.activity_canvas.configure(yscrollcommand=scrollbar.set)
        self.activity_cards = ttk.Frame(self.activity_canvas)
        self.activity_canvas_window = self.activity_canvas.create_window(
            (0, 0), window=self.activity_cards, anchor="nw"
        )
        self.activity_cards.bind(
            "<Configure>",
            lambda _event: self.activity_canvas.configure(
                scrollregion=self.activity_canvas.bbox("all")
            ),
        )
        self.activity_canvas.bind(
            "<Configure>",
            lambda event: self.activity_canvas.itemconfigure(
                self.activity_canvas_window, width=event.width - 18
            ),
        )
        self.activity_cards.columnconfigure(0, weight=1)
        self.activity_cards.columnconfigure(1, weight=1)
        ttk.Label(
            self.activity_tab,
            text=(
                "Activity settings edit local Remote Data. Change icon edits that activity's "
                "left-side Harmony menu-row icon in a separate backup candidate; load the "
                "matching backup first. General project edits are not uploadable."
            ),
            style="Harmony.Muted.TLabel",
            wraplength=800,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.activity_list = tk.Listbox(self.activity_tab, exportselection=False)

        self.activity_editor_window = tk.Toplevel(self.root)
        self.activity_editor_window.withdraw()
        self.activity_editor_window.title("Activity Settings")
        self.activity_editor_window.geometry("900x750")
        self.activity_editor_window.minsize(800, 640)
        self.activity_editor_window.transient(self.root)
        self.activity_editor_window.protocol(
            "WM_DELETE_WINDOW", self._close_activity_editor
        )
        editor = ttk.Frame(self.activity_editor_window, padding=12)
        editor.pack(fill="both", expand=True, padx=10, pady=10)
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(6, weight=1)
        self.activity_name = tk.StringVar()
        ttk.Label(editor, text="Activity name").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.activity_name).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Button(editor, text="Apply name", command=self._apply_activity_name).grid(
            row=0, column=2, padx=(8, 0)
        )
        icon_controls = ttk.Frame(editor)
        icon_controls.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.activity_icon_preview = ttk.Label(icon_controls, text="No local icon")
        self.activity_icon_preview.pack(side="left", padx=(0, 8))
        self.activity_icon_image: tk.PhotoImage | None = None
        ttk.Button(icon_controls, text="Change local icon...", command=self._edit_activity_icon).pack(side="left")
        ttk.Label(
            editor,
            text="Changes edit local Remote Data. Save Remote Data; these settings are not uploaded to the remote.",
            style="Harmony.Muted.TLabel",
            wraplength=800,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        setup = ttk.LabelFrame(editor, text="1. Devices used in this activity", padding=8)
        setup.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        setup.columnconfigure(0, weight=1)
        setup.rowconfigure(1, weight=1)
        ttk.Label(
            setup,
            text="Click each device to select or deselect it. Adding an action also selects its device.",
        ).grid(row=0, column=0, sticky="w")
        self.activity_device_list = tk.Listbox(
            setup,
            selectmode=tk.MULTIPLE,
            exportselection=False,
            height=4,
            width=28,
        )
        self.activity_device_list.grid(row=1, column=0, sticky="nsew")
        self.activity_device_list.bind(
            "<<ListboxSelect>>", lambda _event: self._activity_participation_changed()
        )
        advanced_toggle = ttk.Button(editor, text="Show optional control settings")
        advanced_toggle.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        advanced = ttk.LabelFrame(editor, text="Optional control settings", padding=8)
        advanced.columnconfigure(1, weight=1)
        roles = ttk.Frame(advanced)
        roles.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        ttk.Label(roles, text="Changes channels").grid(row=0, column=0, sticky="w")
        self.channel_device = tk.StringVar()
        self.channel_device_box = ttk.Combobox(
            roles, textvariable=self.channel_device, state="readonly", width=22
        )
        self.channel_device_box.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        self.channel_device_box.bind("<<ComboboxSelected>>", self._save_activity_setup)
        ttk.Label(roles, text="Controls volume").grid(row=2, column=0, sticky="w")
        self.volume_device = tk.StringVar()
        self.volume_device_box = ttk.Combobox(
            roles, textvariable=self.volume_device, state="readonly", width=22
        )
        self.volume_device_box.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self.volume_device_box.bind("<<ComboboxSelected>>", self._save_activity_setup)
        inputs = ttk.LabelFrame(advanced, text="Input notes (do not send commands)", padding=6)
        inputs.grid(row=0, column=1, sticky="new")
        self.activity_inputs_frame = inputs
        self.activity_input_vars: dict[str, tk.StringVar] = {}
        self.activity_device_ids: list[str] = []
        def toggle_advanced() -> None:
            if advanced.winfo_manager():
                advanced.grid_remove()
                advanced_toggle.configure(text="Show optional control settings")
            else:
                advanced.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
                advanced_toggle.configure(text="Hide optional control settings")

        advanced_toggle.configure(command=toggle_advanced)
        sequence = ttk.LabelFrame(editor, text="2. Actions when this activity is selected", padding=8)
        sequence.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        sequence.columnconfigure(0, weight=1)
        sequence.rowconfigure(1, weight=1)
        ttk.Label(
            sequence,
            text="Intended order: top to bottom. Select a row to move or remove it.",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.step_tree = ttk.Treeview(
            sequence,
            columns=("number", "device", "command", "sequence"),
            show="headings",
            height=7,
        )
        self.step_tree.heading("number", text="#")
        self.step_tree.heading("device", text="Device")
        self.step_tree.heading("command", text="Action")
        self.step_tree.heading("sequence", text="Group")
        self.step_tree.column("number", width=45, stretch=False, anchor="center")
        self.step_tree.column("device", width=180)
        self.step_tree.column("command", width=220)
        self.step_tree.column("sequence", width=120)
        self.step_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        step_scrollbar = ttk.Scrollbar(sequence, orient="vertical", command=self.step_tree.yview)
        step_scrollbar.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.step_tree.configure(yscrollcommand=step_scrollbar.set)
        controls = ttk.Frame(editor)
        controls.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)
        self.step_sequence_name = tk.StringVar(value="Power on")
        ttk.Label(advanced, text="Group name for new actions (optional)").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(advanced, textvariable=self.step_sequence_name).grid(
            row=1, column=1, sticky="ew", pady=(8, 0)
        )
        self.step_device = tk.StringVar()
        self.step_command = tk.StringVar()
        ttk.Label(controls, text="Device").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.step_device_box = ttk.Combobox(
            controls, textvariable=self.step_device, state="readonly", width=25
        )
        self.step_device_box.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        self.step_device_box.bind("<<ComboboxSelected>>", self._step_device_changed)
        ttk.Label(controls, text="Command").grid(row=1, column=2, sticky="w", pady=(8, 0))
        self.step_command_box = ttk.Combobox(
            controls, textvariable=self.step_command, state="readonly", width=24
        )
        self.step_command_box.grid(row=1, column=3, sticky="ew", pady=(8, 0))
        row_actions = ttk.Frame(controls)
        row_actions.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(row_actions, text="Add action", command=self._add_step).pack(
            side="left"
        )
        ttk.Button(row_actions, text="Add delay", command=self._add_delay).pack(
            side="left", padx=4
        )
        ttk.Button(row_actions, text="Remove step", command=self._remove_step).pack(
            side="left", padx=4
        )
        ttk.Button(row_actions, text="Move up", command=lambda: self._move_step(-1)).pack(
            side="left", padx=4
        )
        ttk.Button(
            row_actions, text="Move down", command=lambda: self._move_step(1)
        ).pack(
            side="left", padx=4
        )
        editor_actions = ttk.Frame(editor)
        editor_actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(
            editor_actions,
            text="Export activity object...",
            command=lambda: self._export_selected_project_object("activity"),
        ).pack(side="left")
        ttk.Button(
            editor_actions,
            text="Done",
            command=self._close_activity_editor,
        ).pack(side="right")

    def _build_usb_tab(self) -> None:
        ttk.Label(
            self.usb_tab,
            text="Harmony One USB connection",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            self.usb_tab,
            text=(
                "A backup sends only READ_FLASH requests to a connected Harmony One "
                "(VID_046D&PID_C121). The complete GSPM container, PTYY end marker, and "
                "trailer checksum are validated before saving. Fixed-size image/text edits "
                "have been verified on the spare remote; general device/activity Remote Data "
                "uploads remain blocked until configuration generation is complete."
            ),
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))
        controls = ttk.Frame(self.usb_tab)
        controls.pack(anchor="w")
        self.backup_button = ttk.Button(
            controls, text="Scan connected remote and save full backup", command=self._start_backup
        )
        self.backup_button.pack(side="left")
        ttk.Button(
            controls, text="Export objects from backup", command=self._export_remote_objects
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            controls,
            text="Back up and export objects",
            command=lambda: self._start_backup(export_after=True),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            controls, text="Refresh USB devices", command=self._refresh_usb
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            controls, text="Repair USB connection...", command=self._repair_usb_device
        ).pack(side="left", padx=(8, 0))
        self.backup_progress = ttk.Progressbar(
            self.usb_tab, mode="determinate", maximum=100, length=500
        )
        self.backup_progress.pack(anchor="w", fill="x", pady=(12, 0))
        self.backup_progress_text = tk.StringVar(value="Backup has not started.")
        ttk.Label(self.usb_tab, textvariable=self.backup_progress_text).pack(
            anchor="w", pady=(4, 0)
        )
        self.usb_status = tk.StringVar(value="No device scan has run.")
        ttk.Label(self.usb_tab, textvariable=self.usb_status, wraplength=860).pack(
            anchor="w", pady=10
        )
        self.usb_tree = ttk.Treeview(
            self.usb_tab, columns=("name", "instance"), show="headings", height=12
        )
        self.usb_tree.heading("name", text="Logitech device")
        self.usb_tree.heading("instance", text="Windows device instance")
        self.usb_tree.column("name", width=260)
        self.usb_tree.column("instance", width=700)
        self.usb_tree.pack(fill="both", expand=True)

    def _read_from_remote(self) -> None:
        if self._backup_running:
            return
        if not messagebox.askyesno(
            "Check the connected remote",
            "Connect the Harmony One by USB and confirm it is charged and ready. "
            "The app will download and save a complete backup, then open a separate "
            "Remote Data copy for editing. Nothing will be written to the remote.",
            parent=self.root,
        ):
            return
        if not self._confirm_discard():
            return
        self._start_backup(import_after=True)

    def _scan_and_export_icons(self) -> None:
        if self._backup_running:
            return
        if not messagebox.askyesno(
            "Check the connected remote",
            "Connect the Harmony One by USB and confirm it is charged and ready. "
            "The app will read and save a complete backup, then export its distinct "
            "51 x 48 activity/device menu icons to your user icons folder. "
            "Nothing will be written to the remote.",
            parent=self.root,
        ):
            return
        self._start_backup(archive_after=True, export_icons_after=True)

    def _open_icons_folder(self) -> None:
        directory = icon_library_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(directory))
        except OSError as error:
            messagebox.showerror(
                "Could not open icons folder",
                f"Could not open {directory}:\n{error}",
                parent=self.root,
            )

    def _preview_write(self) -> None:
        if self.remote_project is None:
            self._open_restore_save()
            return
        plan = plan_changes(self.remote_project, self.project)
        rename_only = (
            len(plan.activities_renamed) == 1
            and not any(
                (
                    plan.devices_added,
                    plan.devices_removed,
                    plan.devices_renamed,
                    plan.devices_reordered,
                    plan.device_fields_changed,
                    plan.activities_added,
                    plan.activities_removed,
                    plan.activities_reordered,
                    plan.activities_with_steps_changed,
                    plan.activity_fields_changed,
                    plan.project_fields_changed,
                )
            )
        )
        if rename_only and self.remote_blob is not None:
            old, new = plan.activities_renamed[0]
            if not messagebox.askyesno(
                "Prepare offline candidate",
                f"Activity rename: {old} -> {new}\n\n"
                "Prepare a local configuration candidate for inspection? "
                "This does not write to the remote.",
                parent=self.root,
            ):
                return
            try:
                candidate = prepare_activity_rename(self.remote_blob, old, new)
            except (ValueError, IndexError) as error:
                messagebox.showwarning(
                    "Candidate could not be prepared",
                    str(error),
                    parent=self.root,
                )
                return
            chosen = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save offline candidate (not upload-ready)",
                initialdir=str(backup_directory()),
                initialfile=f"HarmonyOne-candidate-{datetime.now():%Y%m%d-%H%M%S}.EZHex",
                defaultextension=".EZHex",
                filetypes=[("Harmony backup", "*.EZHex")],
            )
            if not chosen:
                return
            try:
                with Path(chosen).open("xb") as output:
                    output.write(candidate.image)
            except OSError as error:
                messagebox.showerror("Could not save candidate", str(error), parent=self.root)
                return
            messagebox.showinfo(
                "Offline candidate saved",
                f"Saved {chosen}\n\nChanged {candidate.places_changed} text locations "
                f"and {candidate.changed_bytes} bytes including the checksum. "
                "This candidate is not upload-ready; no data was written to the remote.",
                parent=self.root,
            )
            return

        self._open_restore_save()
        self.save_restore.status.set(
            "The selected local project changes cannot be included in a complete-save transfer. "
            "The Transfer Remote workflow copies a raw .EZHex snapshot from one remote to "
            "another; the current local JSON edits will not be included."
        )

    def _open_restore_save(self) -> None:
        self.tabs.select(self.backup_transfer_frame)
        self.backup_transfer_tabs.select(self.restore_save_frame)
        self.save_restore.status.set(
            "Download and save a connected remote, choose a saved .EZHex backup, or select an FQ preset. "
            "Local Remote Data editor changes are not included."
        )

    def _add_remote_activity(self) -> None:
        if self._backup_running or self.slideshow.writing:
            messagebox.showwarning(
                "Remote busy", "Wait for the current USB operation to finish.", parent=self.root
            )
            return
        if self.remote_blob is None:
            messagebox.showinfo(
                "Read the remote first",
                "Use Read from connected remote before adding an activity.", parent=self.root,
            )
            return
        try:
            self.activity_upload_dialog = ActivityUploadDialog(
                self.root, self.remote_blob, backup_directory()
            )
        except (ConfigError, ValueError) as error:
            messagebox.showerror("Could not open activity upload", str(error), parent=self.root)

    def _start_backup(
        self,
        import_after: bool = False,
        export_after: bool = False,
        browse_after: bool = False,
        archive_after: bool = False,
        export_icons_after: bool = False,
    ) -> None:
        if self._backup_running:
            return
        self._import_after_backup = import_after
        self._export_after_backup = export_after
        self._browse_after_backup = browse_after
        self._export_icons_after_backup = export_icons_after
        if archive_after:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            destination = backup_directory() / f"HarmonyOne-{stamp}.EZHex"
        elif import_after or export_after or browse_after:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            destination = backup_directory() / f"HarmonyOne-{stamp}.EZHex"
        else:
            destination = backup_directory() / "HarmonyOne-current.EZHex"
        overwrite = False
        if destination.exists():
            overwrite = messagebox.askyesno(
                "Replace existing backup?",
                f"A backup already exists at:\n{destination}\n\nReplace it only after "
                "the new read passes all integrity checks?",
                parent=self.root,
            )
            if not overwrite:
                return

        self._backup_running = True
        self.backup_button.configure(state="disabled")
        self.export_icons_button.configure(state="disabled")
        self.backup_progress.configure(value=0)
        self.backup_progress_text.set(
            f"Reading remote; validated backup will be saved to {destination}"
        )
        self.usb_status.set("Reading the connected Harmony One over USB.")
        self._show_usb_backup_activity()
        self._backup_events = queue.Queue()

        def worker() -> None:
            try:
                blob = backup_connected_harmony_one(
                    destination,
                    on_progress=lambda done, total: self._backup_events.put(
                        ("progress", (done, total))
                    ),
                    overwrite=overwrite,
                )
                self._backup_events.put(("complete", (destination, blob)))
            except Exception as error:
                self._backup_events.put(("error", str(error)))

        threading.Thread(target=worker, name="HarmonyOneBackup", daemon=True).start()
        self.root.after(100, self._poll_backup_events)

    def _poll_backup_events(self) -> None:
        while True:
            try:
                event, payload = self._backup_events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                done, total = payload
                percent = done * 100 / total if total else 0
                self.backup_progress.configure(value=percent)
                self.backup_progress_text.set(
                    f"Read {done:,} of {total:,} bytes ({percent:.1f}%)"
                )
                self._show_usb_backup_activity(percent if total else None)
            elif event == "complete":
                destination, blob = payload
                self.remote_blob = blob
                self.remote_backup_path = destination
                assistant = getattr(self, "remote_assistant", None)
                if assistant is not None:
                    assistant.load(blob, destination)
                self._load_home_preview(blob)
                if not self._import_after_backup and not self._browse_after_backup:
                    self.slideshow.load(blob)
                self.save_restore._refresh_saved_backups(destination)
                size = len(blob)
                self.backup_progress.configure(value=100)
                self.backup_progress_text.set(
                    f"Validated {size:,} bytes and saved backup: {destination}"
                )
                self.usb_status.set("Read-only Harmony One configuration backup completed.")
                self._backup_running = False
                self.backup_button.configure(state="normal")
                self.export_icons_button.configure(state="normal")
                self._clear_usb_backup_activity()
                if self._import_after_backup:
                    self._load_remote_blob(blob, destination)
                elif self._export_after_backup:
                    self._export_objects_from_blob(blob, destination.parent)
                elif self._export_icons_after_backup:
                    try:
                        icons = export_remote_menu_icons(blob)
                    except IconLibraryError as error:
                        self.usb_status.set(
                            f"Backup saved, but icon export failed: {error}"
                        )
                        messagebox.showerror(
                            "Remote icon export failed",
                            f"The validated backup was saved to:\n{destination}\n\n"
                            f"{error}",
                            parent=self.root,
                        )
                    else:
                        self.usb_status.set(
                            f"Exported {len(icons)} distinct menu icons to "
                            f"{icon_library_directory()}."
                        )
                        messagebox.showinfo(
                            "Remote icons exported",
                            f"Exported {len(icons)} distinct menu icons to:\n"
                            f"{icon_library_directory()}\n\n"
                            f"Full remote backup:\n{destination}\n\n"
                            "Add PNG/GIF files to the icons folder to make them "
                            "available in the Change icon picker.",
                            parent=self.root,
                        )
                elif self._browse_after_backup:
                    self.backup_browser.load(blob, destination)
                    self.tabs.select(self.backup_transfer_frame)
                    self.backup_transfer_tabs.select(self.backup_frame)
                else:
                    messagebox.showinfo(
                        "Backup complete",
                        f"Validated {size:,} bytes and saved:\n{destination}",
                        parent=self.root,
                    )
            elif event == "error":
                self._backup_running = False
                self.backup_button.configure(state="normal")
                self.export_icons_button.configure(state="normal")
                self.backup_progress_text.set("Backup operation ended with an error; see details.")
                self.usb_status.set(f"Backup failed: {payload}")
                self._clear_usb_backup_activity()
                if "No connected Logitech Harmony One" in str(payload):
                    self._handle_missing_remote(str(payload))
                else:
                    messagebox.showerror("Harmony One backup failed", payload, parent=self.root)

        if self._backup_running:
            self.root.after(100, self._poll_backup_events)

    def _handle_missing_remote(self, original_error: str) -> None:
        """Explain a missing-remote failure and offer a driver restart when applicable."""
        failed = find_failed_harmony_usb_device()
        if failed is None:
            messagebox.showerror(
                "Harmony One backup failed",
                f"{original_error}\n\nCheck that the USB cable is connected to the remote "
                "and the PC, and that the remote shows its USB-connected screen.",
                parent=self.root,
            )
            return
        instance_id, status = failed
        repair = messagebox.askyesno(
            "Harmony One needs a driver restart",
            "Windows can see the Harmony One, but its USB device failed to start "
            f"(status: {status}).\n\n{instance_id}\n\nRestart the device driver now? "
            "Windows will show an administrator (UAC) prompt. Alternatively, unplug "
            "the remote and plug it into a different USB port.",
            parent=self.root,
        )
        if not repair:
            return
        if restart_usb_device_elevated(instance_id):
            messagebox.showinfo(
                "USB device restarted",
                "The Harmony One USB device was restarted. Try the backup again.",
                parent=self.root,
            )
            self.usb_status.set("Harmony One USB device restarted; ready to retry the backup.")
        else:
            messagebox.showerror(
                "USB repair failed",
                "The driver restart did not complete (the administrator prompt may have "
                "been declined). Unplug the remote and plug it into a different USB port, "
                "then try again.",
                parent=self.root,
            )

    def _repair_usb_device(self) -> None:
        failed = find_failed_harmony_usb_device()
        if failed is None:
            messagebox.showinfo(
                "No failed Harmony One found",
                "No present-but-failed Harmony One USB device was found. If the remote "
                "is not listed below, check the cable and the remote's USB screen.",
                parent=self.root,
            )
            return
        self._handle_missing_remote("The Harmony One USB device is in a failed state.")

    def _update_title(self) -> None:
        marker = " *" if self.dirty else ""
        self.root.title(f"{APP_NAME} - {self.project_path.name}{marker}")
        if hasattr(self, "header_status"):
            state = "saved" if self.project_saved else "not yet saved"
            if self.dirty:
                state += "; unsaved changes"
            self.header_status.configure(
                text=f"Version {__version__}\n"
                f"Remote Data JSON ({state}): {self.project_path.name}\n"
                "Local editable data, not the raw remote backup"
            )

    def _mark_dirty(self) -> None:
        self.dirty = True
        self._update_title()

    def _replace_project(self, project: dict[str, Any], path: Path, *, saved: bool = False) -> None:
        self.project = validate_project(project)
        self.project_path = path
        self.project_saved = saved
        self.selected_device_id = None
        self.selected_activity_id = None
        self.dirty = False
        self._refresh_devices()
        self._refresh_activities()
        self._update_title()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        return messagebox.askyesno(
            "Unsaved changes",
            "Discard unsaved Remote Data changes?",
            parent=self.root,
        )

    def _new_project(self) -> None:
        if self._confirm_discard():
            self._replace_project(
                validate_project(new_project()), portable_directory() / DEFAULT_PROJECT_NAME
            )

    def _open_project(self) -> None:
        if not self._confirm_discard():
            return
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Open Remote Data",
            filetypes=(("Remote Data JSON", "*.json"), ("All files", "*.*")),
        )
        if not filename:
            return
        path = Path(filename)
        try:
            project = load_project(path)
        except ProjectError as error:
            messagebox.showerror("Open failed", str(error), parent=self.root)
            return
        self._replace_project(project, path, saved=True)
        self.status.set(f"Opened {path}")

    def _save_project(self) -> None:
        try:
            save_project(self.project_path, self.project)
        except ProjectError as error:
            messagebox.showerror("Save failed", str(error), parent=self.root)
            return
        self.project_saved = True
        self.dirty = False
        self._update_title()
        self.status.set(f"Saved {self.project_path}")

    def _save_project_as(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Remote Data",
            defaultextension=".json",
            initialfile=self.project_path.name,
            filetypes=(("Remote Data JSON", "*.json"), ("All files", "*.*")),
        )
        if not filename:
            return
        self.project_path = Path(filename)
        self.project_saved = False
        self._update_title()
        self._save_project()

    def _close(self) -> None:
        if hasattr(self, "save_restore") and self.save_restore.busy:
            messagebox.showwarning(
                "Remote operation in progress",
                "Wait for the remote transfer to finish and keep USB connected.",
                parent=self.root,
            )
            return
        if self.activity_upload_dialog is not None and self.activity_upload_dialog.busy:
            messagebox.showwarning(
                "Remote operation in progress",
                "Wait for the activity operation to finish and keep USB connected.",
                parent=self.root,
            )
            return
        if (
            hasattr(self, "backup_browser")
            and self.backup_browser.edited_icon_candidate
            and not messagebox.askyesno(
                "Unsaved menu-icon edits",
                "Discard the unsaved menu-icon backup candidate?",
                parent=self.root,
            )
        ):
            return
        if self._confirm_discard():
            self.root.destroy()

    def _refresh_devices(self) -> None:
        self._refresh_home_summary()
        self._refresh_device_order()
        self.device_list.delete(0, tk.END)
        self.device_tree_iids.clear()
        for child in self.device_cards.winfo_children():
            child.destroy()
        devices = sorted(
            self.project["devices"],
            key=lambda device: (
                device["category"].casefold(),
                device["brand"].casefold(),
                device["name"].casefold(),
                device["id"],
            ),
        )
        page_count = max(1, (len(devices) + CARD_PAGE_SIZE - 1) // CARD_PAGE_SIZE)
        self.device_card_page = min(self.device_card_page, page_count - 1)
        self.device_page_label.configure(
            text=f"Page {self.device_card_page + 1} of {page_count}"
        )
        if not devices:
            ttk.Label(
                self.device_cards,
                text="No devices yet. Add a device or import supported IR command data.",
                padding=20,
                style="Harmony.Muted.TLabel",
            ).grid(row=0, column=0, columnspan=2, sticky="w")
        used_by = {
            device["id"]: sorted(
                {
                    activity["name"]
                    for activity in self.project["activities"]
                    if any(step.get("device_id") == device["id"] for step in activity["steps"])
                },
                key=str.casefold,
            )
            for device in devices
        }
        start = self.device_card_page * CARD_PAGE_SIZE
        for index, device in enumerate(devices[start : start + CARD_PAGE_SIZE]):
            card_index = index
            self.device_list.insert(tk.END, device["name"])
            self.device_tree_iids[device["id"]] = device["id"]
            card = ttk.LabelFrame(
                self.device_cards,
                text=device["name"],
                padding=10,
                style="Harmony.Card.TLabelframe",
            )
            card.grid(
                row=card_index // 2,
                column=card_index % 2,
                sticky="nsew",
                padx=(0, 8),
                pady=(0, 8),
            )
            card.columnconfigure(0, weight=1)
            category = device["category"] or "Uncategorized"
            brand = device["brand"] or "Unbranded"
            ttk.Label(
                card,
                text=f"{category}  |  {brand}",
                style="Harmony.Muted.TLabel",
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(
                card,
                text=f"IR buttons: {len(device['commands'])}",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=1, column=0, sticky="w", pady=(8, 2))
            names = used_by[device["id"]]
            ttk.Label(
                card,
                text="Activities used in:",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=2, column=0, sticky="w", pady=(4, 0))
            ttk.Label(
                card,
                text="  ".join(names[:4]) if names else "Not used in an activity yet",
                wraplength=300,
                style="Harmony.Muted.TLabel",
            ).grid(row=3, column=0, sticky="w", pady=(2, 8))
            buttons = ttk.Frame(card)
            buttons.grid(row=4, column=0, sticky="ew")
            ttk.Button(
                buttons,
                text="Settings",
                style="Harmony.Primary.TButton",
                command=lambda item_id=device["id"]: self._open_device_settings(item_id),
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Troubleshoot",
                command=lambda item=device: self._device_troubleshoot(item),
            ).pack(side="left", padx=3, fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Change icon",
                command=lambda name=device["name"]: self._change_remote_menu_icon(
                    "device", name
                ),
            ).pack(side="left", padx=3, fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Learn IR",
                command=self._learn_ir_unavailable,
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Remove",
                command=lambda item_id=device["id"]: self._remove_device_by_id(item_id),
            ).pack(side="left", padx=(3, 0))

        if self.selected_device_id is not None:
            selected = self._selected_device()
            if selected:
                self._show_device(selected)
            else:
                self.selected_device_id = None
                self._show_device(None)
        else:
            self._show_device(None)
        self._refresh_step_choices()

    def _open_device_settings(self, device_id: str) -> None:
        self.selected_device_id = device_id
        device = self._selected_device()
        if not device:
            return
        index = next(
            (i for i, item in enumerate(self.project["devices"]) if item["id"] == device_id),
            0,
        )
        self.device_list.selection_clear(0, tk.END)
        self.device_list.selection_set(index)
        self._show_device(device)
        self.device_settings_window.title(f"Device Settings - {device['name']}")
        self.device_settings_window.deiconify()
        self.device_settings_window.lift()

    def _device_troubleshoot(self, device: dict[str, Any]) -> None:
        messagebox.showinfo(
            "Device troubleshooting",
            f"{device['name']} has {len(device['commands'])} command(s) in this local Remote Data.\n\n"
            "Confirm that the device brand/model and imported IR codes are correct. "
            "This version does not run a live remote troubleshooting test.",
            parent=self.root,
        )

    def _change_remote_menu_icon(self, role: str, item_name: str) -> None:
        self.backup_browser.edit_menu_icon_for_item(
            role,
            item_name,
            apply_to_all_occurrences=role in ("device", "activity"),
            fallback_to_role_occurrences=role == "activity",
        )

    def _learn_ir_unavailable(self) -> None:
        messagebox.showinfo(
            "Learn IR",
            "Live IR learning is not implemented yet. Use 'Import IR file...' to add "
            "supported parsed signals from a Flipper IR file.",
            parent=self.root,
        )

    def _remove_device_by_id(self, device_id: str) -> None:
        self.selected_device_id = device_id
        self._remove_device()

    def _device_selected(self, _event: tk.Event[Any] | None = None) -> None:
        selection = self.device_list.selection()
        if not selection:
            return
        device_id = selection[0]
        if device_id not in self.device_tree_iids:
            self.selected_device_id = None
            self._show_device(None)
            return
        device = next(
            (item for item in self.project["devices"] if item["id"] == device_id),
            None,
        )
        if device:
            self.selected_device_id = device["id"]
            self._show_device(device)

    def _show_device(self, device: dict[str, Any] | None) -> None:
        self.device_name.set(device["name"] if device else "")
        self.device_brand.set(device["brand"] if device else "")
        self.device_category.set(device["category"] if device else "")
        self.command_name.set("")
        self.command_protocol.set("")
        self.command_address.set("")
        self.command_code.set("")
        for item in self.command_tree.get_children():
            self.command_tree.delete(item)
        if device:
            for index, command in enumerate(device["commands"]):
                self.command_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        command["name"],
                        command["protocol"],
                        command["address"],
                        command["command"],
                    ),
                )

    def _apply_device_details(self) -> None:
        device = self._selected_device()
        if not device:
            return
        name = self.device_name.get().strip()
        brand = self.device_brand.get().strip()
        category = self.device_category.get().strip()
        if not name:
            messagebox.showerror("Invalid device", "Device name cannot be empty.")
            return
        device.update(name=name, brand=brand, category=category)
        self._mark_dirty()
        self._refresh_devices()
        self._refresh_activities()
        self.device_settings_window.title(f"Device Settings - {device['name']}")

    def _command_selected(self, _event: tk.Event[Any] | None = None) -> None:
        selected = self.command_tree.selection()
        if not selected:
            self.command_name.set("")
            self.command_protocol.set("")
            self.command_address.set("")
            self.command_code.set("")
            return
        values = self.command_tree.item(selected[0], "values")
        self.command_name.set(values[0])
        self.command_protocol.set(values[1])
        self.command_address.set(values[2])
        self.command_code.set(values[3])

    def _apply_command(self) -> None:
        device = self._selected_device()
        selected = self.command_tree.selection()
        if not device or not selected:
            return
        index = int(selected[0])
        old_name = device["commands"][index]["name"]
        new_name = self.command_name.get().strip()
        protocol = self.command_protocol.get().strip()
        address = self.command_address.get().strip()
        command_code = self.command_code.get().strip()
        if not all((new_name, protocol, address, command_code)):
            messagebox.showerror(
                "Invalid signal",
                "Button name, protocol, address, and command code are required.",
                parent=self.root,
            )
            return
        if any(
            command["name"].casefold() == new_name.casefold()
            for offset, command in enumerate(device["commands"])
            if offset != index
        ):
            messagebox.showerror("Invalid command", "Command names must be unique per device.")
            return
        device["commands"][index].update(
            name=new_name,
            protocol=protocol,
            address=address,
            command=command_code,
        )
        for activity in self.project["activities"]:
            for step in activity["steps"]:
                if step.get("device_id") == device["id"] and step.get("command_name") == old_name:
                    step["command_name"] = new_name
        self._mark_dirty()
        self._refresh_devices()
        self._refresh_activities()
        self.command_tree.selection_set(str(index))
        self._command_selected()

    def _add_command(self) -> None:
        device = self._selected_device()
        if not device:
            return
        name = simpledialog.askstring(
            "Add button",
            "Button name (for example: Power on):",
            parent=self.root,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("Invalid button", "Button name cannot be empty.")
            return
        fields: list[tuple[str, str]] = []
        for label in ("Protocol", "Address", "Command code"):
            value = simpledialog.askstring(f"Add button - {label}", f"{label}:", parent=self.root)
            if value is None:
                return
            value = value.strip()
            if not value:
                messagebox.showerror("Invalid signal", f"{label} cannot be empty.")
                return
            fields.append((label, value))
        if any(command["name"].casefold() == name.casefold() for command in device["commands"]):
            messagebox.showerror("Invalid button", "Button names must be unique per device.")
            return
        device["commands"].append(
            {
                "name": name,
                "protocol": fields[0][1],
                "address": fields[1][1],
                "command": fields[2][1],
            }
        )
        self._mark_dirty()
        self._refresh_devices()
        self.command_tree.selection_set(str(len(device["commands"]) - 1))
        self._command_selected()
        self._refresh_step_choices()

    def _remove_command(self) -> None:
        device = self._selected_device()
        selected = self.command_tree.selection()
        if not device or not selected:
            return
        index = int(selected[0])
        command = device["commands"][index]
        if any(
            step.get("device_id") == device["id"] and step.get("command_name") == command["name"]
            for activity in self.project["activities"]
            for step in activity["steps"]
        ):
            messagebox.showerror(
                "Button is in use",
                "Remove this button from activity sequences before deleting it.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Remove button", f"Remove {command['name']}?", parent=self.root
        ):
            return
        device["commands"].pop(index)
        self._mark_dirty()
        self._refresh_devices()
        self._refresh_step_choices()

    def _selected_device(self) -> dict[str, Any] | None:
        for device in self.project["devices"]:
            if device["id"] == self.selected_device_id:
                return device
        return None

    def _add_device(self) -> None:
        name = simpledialog.askstring("Add device", "Device name:", parent=self.root)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("Invalid device", "Device name cannot be empty.")
            return
        device = {
            "id": uuid.uuid4().hex,
            "name": name,
            "brand": "",
            "category": "",
            "commands": [],
        }
        self.project["devices"].append(device)
        self.selected_device_id = device["id"]
        self._mark_dirty()
        self._refresh_devices()
        self._refresh_step_choices()

    def _add_device_from_library(self) -> None:
        try:
            library = bundled_project()["devices"]
        except (OSError, ValueError, ProjectError) as error:
            messagebox.showerror("IR library unavailable", str(error), parent=self.root)
            return
        if not library:
            messagebox.showinfo(
                "IR library empty",
                "No bundled IR library profiles are available in this build.",
                parent=self.root,
            )
            return
        library = sorted(
            library,
            key=lambda entry: (
                entry["category"].casefold(), entry["brand"].casefold(), entry["name"].casefold()
            ),
        )
        dialog = tk.Toplevel(self.root)
        dialog.title("Add device from IR library")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("520x420")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(
            dialog,
            padding=(10, 10),
            wraplength=490,
            justify="left",
            text=(
                f"Bundled IR library ({len(library)} profiles, Flipper-IRDB, CC0). "
                "Select profiles to copy into the current Remote Data."
            ),
        ).grid(row=0, column=0, sticky="w")
        listing = tk.Listbox(dialog, selectmode="extended", exportselection=False)
        listing.grid(row=1, column=0, sticky="nsew", padx=10)
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=listing.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        listing.configure(yscrollcommand=scroll.set)
        for entry in library:
            label = " / ".join(
                part for part in (entry["category"], entry["brand"], entry["name"]) if part
            )
            listing.insert(tk.END, f"{label}  ({len(entry['commands'])} commands)")

        def add_selected() -> None:
            selection = listing.curselection()
            if not selection:
                messagebox.showinfo(
                    "Nothing selected", "Select at least one profile to add.", parent=dialog
                )
                return
            for index in selection:
                device = copy.deepcopy(library[index])
                device["id"] = uuid.uuid4().hex
                self.project["devices"].append(device)
                self.selected_device_id = device["id"]
            self._mark_dirty()
            self._refresh_devices()
            self._refresh_step_choices()
            self.status.set(f"Added {len(selection)} device(s) from the bundled IR library")
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=10)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(
            buttons,
            text="Add selected",
            style="Harmony.Primary.TButton",
            command=add_selected,
        ).pack(side="right", padx=5)

    def _import_ir(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Import Flipper IR file",
            filetypes=(("Flipper IR files", "*.ir"), ("All files", "*.*")),
        )
        if not filename:
            return
        source = Path(filename)
        try:
            signals = parse_ir(source.read_text(encoding="utf-8"))
            catalog = {
                "device": {
                    "name": source.stem.replace("_", " "),
                    "brand": source.parent.name,
                    "category": source.parent.parent.name,
                },
                "signals": signals,
            }
            device = device_from_catalog(catalog)
        except (OSError, UnicodeError, ValueError, ProjectError) as error:
            messagebox.showerror("IR import failed", str(error), parent=self.root)
            return
        self.project["devices"].append(device)
        self.selected_device_id = device["id"]
        self._mark_dirty()
        self._refresh_devices()
        self._refresh_step_choices()
        self.status.set(f"Imported {len(device['commands'])} IR commands from {source.name}")

    def _remove_device(self) -> None:
        device = self._selected_device()
        if not device:
            return
        references = [
            activity["name"]
            for activity in self.project["activities"]
            if (
                device["id"] in activity.get("participating_device_ids", [])
                or activity.get("channel_device_id") == device["id"]
                or activity.get("volume_device_id") == device["id"]
                or device["id"] in activity.get("inputs", {})
                or any(step.get("device_id") == device["id"] for step in activity["steps"])
            )
        ]
        if references:
            messagebox.showerror(
                "Device is in use",
                "Remove this device from these activities first by editing their device setup or sequences:\n"
                + "\n".join(sorted(set(references))),
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Remove device", f"Remove {device['name']} from this Remote Data?", parent=self.root
        ):
            return
        self.project["devices"].remove(device)
        self.selected_device_id = None
        self._mark_dirty()
        self._refresh_devices()
        self._refresh_step_choices()

    def _refresh_activities(self) -> None:
        self._refresh_home_summary()
        self.activity_list.delete(0, tk.END)
        for child in self.activity_cards.winfo_children():
            child.destroy()
        device_by_id = {device["id"]: device for device in self.project["devices"]}
        page_count = max(
            1, (len(self.project["activities"]) + CARD_PAGE_SIZE - 1) // CARD_PAGE_SIZE
        )
        self.activity_card_page = min(self.activity_card_page, page_count - 1)
        self.activity_page_label.configure(
            text=f"Page {self.activity_card_page + 1} of {page_count}"
        )
        if not self.project["activities"]:
            ttk.Label(
                self.activity_cards,
                text="No activities yet. Add an activity to create a command sequence.",
                padding=20,
                style="Harmony.Muted.TLabel",
            ).grid(row=0, column=0, columnspan=2, sticky="w")
        for index, activity in enumerate(self.project["activities"]):
            self.activity_list.insert(tk.END, self._paged_label(index, activity["name"]))
        start = self.activity_card_page * CARD_PAGE_SIZE
        for index, activity in enumerate(
            self.project["activities"][start : start + CARD_PAGE_SIZE]
        ):
            card = ttk.LabelFrame(
                self.activity_cards,
                text=activity["name"],
                padding=10,
                style="Harmony.Card.TLabelframe",
            )
            card.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(0, 8),
                pady=(0, 8),
            )
            card.columnconfigure(0, weight=1)
            ttk.Label(
                card,
                text=f"Activity {index + 1}  |  {len(activity['steps'])} sequence step(s)",
                style="Harmony.Muted.TLabel",
            ).grid(row=0, column=0, sticky="w")
            used = []
            used_ids = list(activity.get("participating_device_ids", []))
            if not used_ids:
                used_ids = [
                    step["device_id"]
                    for step in activity["steps"]
                    if step.get("device_id") in device_by_id
                ]
            for device_id in used_ids:
                device = device_by_id.get(device_id)
                if device and device["name"] not in used:
                    used.append(device["name"])
            ttk.Label(
                card,
                text="Devices Used:",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=1, column=0, sticky="w", pady=(8, 0))
            ttk.Label(
                card,
                text="  |  ".join(used) if used else "Choose devices in Settings",
                wraplength=320,
                style="Harmony.Muted.TLabel",
            ).grid(row=2, column=0, sticky="w", pady=(2, 8))
            channel_device = device_by_id.get(activity.get("channel_device_id"))
            volume_device = device_by_id.get(activity.get("volume_device_id"))
            role_summary = []
            if channel_device:
                role_summary.append(f"Channels: {channel_device['name']}")
            if volume_device:
                role_summary.append(f"Volume: {volume_device['name']}")
            ttk.Label(
                card,
                text="  |  ".join(role_summary) if role_summary else "Control roles not assigned",
                wraplength=320,
                style="Harmony.Muted.TLabel",
            ).grid(row=3, column=0, sticky="w", pady=(0, 8))
            buttons = ttk.Frame(card)
            buttons.grid(row=4, column=0, sticky="ew")
            ttk.Button(
                buttons,
                text="Settings",
                style="Harmony.Primary.TButton",
                command=lambda item_id=activity["id"]: self._open_activity_editor(item_id),
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Troubleshoot",
                command=lambda item=activity: self._activity_troubleshoot(item),
            ).pack(side="left", padx=3, fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Change icon",
                command=lambda name=activity["name"]: self._change_remote_menu_icon(
                    "activity", name
                ),
            ).pack(side="left", padx=3, fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Customize Buttons",
                command=lambda item=activity: self._activity_buttons_info(item),
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(
                buttons,
                text="Remove",
                command=lambda item_id=activity["id"]: self._remove_activity_by_id(item_id),
            ).pack(side="left", padx=(3, 0))
        self._refresh_step_choices()
        activity = self._selected_activity()
        if activity:
            self._show_activity(activity)
        else:
            self._show_activity(None)

    def _change_card_page(self, kind: str, offset: int) -> None:
        if kind == "devices":
            count = len(self.project["devices"])
            page = self.device_card_page
        else:
            count = len(self.project["activities"])
            page = self.activity_card_page
        page_count = max(1, (count + CARD_PAGE_SIZE - 1) // CARD_PAGE_SIZE)
        target = max(0, min(page + offset, page_count - 1))
        if target == page:
            return
        if kind == "devices":
            self.device_card_page = target
            self._refresh_devices()
        else:
            self.activity_card_page = target
            self._refresh_activities()

    def _open_activity_editor(self, activity_id: str) -> None:
        activity = next(
            (item for item in self.project["activities"] if item["id"] == activity_id),
            None,
        )
        if not activity:
            return
        self.selected_activity_id = activity_id
        index = self.project["activities"].index(activity)
        self.activity_list.selection_clear(0, tk.END)
        self.activity_list.selection_set(index)
        self._refresh_step_choices()
        self._refresh_activity_setup(activity)
        self._show_activity(activity)
        self.activity_editor_window.title(f"Activity Settings - {activity['name']}")
        self.activity_editor_window.deiconify()
        self.activity_editor_window.lift()

    def _close_activity_editor(self) -> None:
        self._save_activity_setup()
        self._refresh_activities()
        self.activity_editor_window.withdraw()

    def _edit_activity_icon(self) -> None:
        activity = self._selected_activity()
        if not activity:
            return
        self._save_activity_setup()

        def apply(icon: dict[str, Any] | None) -> None:
            if icon == activity.get("icon"):
                return
            if icon is None:
                activity.pop("icon", None)
            else:
                activity["icon"] = icon
            self._mark_dirty()
            self._show_activity(activity)

        ActivityIconDialog(self.activity_editor_window, activity.get("icon"), apply)

    def _activity_troubleshoot(self, activity: dict[str, Any]) -> None:
        messagebox.showinfo(
            "Activity troubleshooting",
            f"{activity['name']} has {len(activity['steps'])} configured step(s).\n\n"
            "Check that each sequence uses the intended device and command. "
            "This version does not run a live remote test.",
            parent=self.root,
        )

    def _activity_buttons_info(self, activity: dict[str, Any]) -> None:
        messagebox.showinfo(
            "Customize Buttons",
            "Per-button activity mappings are not yet editable in this Remote Data editor. "
            "The current Settings editor builds ordered command sequences.\n\n"
            f"Selected activity: {activity['name']}",
            parent=self.root,
        )

    def _remove_activity_by_id(self, activity_id: str) -> None:
        self.selected_activity_id = activity_id
        self._remove_activity()

    def _open_reorder_dialog(self, kind: str) -> None:
        is_device = kind == "devices"
        items = self.project["devices"] if is_device else self.project["activities"]
        title = "Re-Order Devices" if is_device else "Re-Order Activities"
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry("440x420")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(
            dialog,
            text=f"Order on remote ({ITEMS_PER_PAGE} items per page)",
            padding=(10, 10),
        ).grid(row=0, column=0, sticky="w")
        listing = tk.Listbox(dialog, exportselection=False)
        listing.grid(row=1, column=0, sticky="nsew", padx=10)

        def refresh(selected: int = 0) -> None:
            listing.delete(0, tk.END)
            for index, item in enumerate(items):
                listing.insert(tk.END, self._paged_label(index, item["name"]))
            if items:
                selected = max(0, min(selected, len(items) - 1))
                listing.selection_set(selected)
                listing.see(selected)

        def move(offset: int) -> None:
            selection = listing.curselection()
            if not selection:
                return
            current = selection[0]
            target = move_item(items, current, offset)
            if target == current:
                return
            self._mark_dirty()
            refresh(target)
            self._refresh_devices() if is_device else self._refresh_activities()

        buttons = ttk.Frame(dialog, padding=10)
        buttons.grid(row=2, column=0, sticky="ew")
        ttk.Button(buttons, text="Move up", command=lambda: move(-1)).pack(side="left")
        ttk.Button(buttons, text="Move down", command=lambda: move(1)).pack(
            side="left", padx=5
        )
        ttk.Button(buttons, text="Done", command=dialog.destroy).pack(side="right")
        refresh()

    def _activity_selected(self, _event: tk.Event[Any] | None = None) -> None:
        selection = self.activity_list.curselection()
        if not selection:
            return
        activity = self.project["activities"][selection[0]]
        self.selected_activity_id = activity["id"]
        self._show_activity(activity)

    def _show_activity(self, activity: dict[str, Any] | None) -> None:
        self.activity_name.set(activity["name"] if activity else "")
        icon = activity.get("icon") if activity else None
        self.activity_icon_image = make_preview(self.activity_editor_window, icon) if icon else None
        self.activity_icon_preview.configure(
            image=self.activity_icon_image or "", text="" if icon else "No local icon"
        )
        self.step_sequence_name.set("Power on")
        self._refresh_activity_setup(activity)
        for item in self.step_tree.get_children():
            self.step_tree.delete(item)
        if not activity:
            self.selected_activity_id = None
            return
        device_by_id = {device["id"]: device for device in self.project["devices"]}
        for index, step in enumerate(activity["steps"]):
            if step.get("type") == "delay":
                values = (index + 1, "", f"Wait {step['ms']} ms", "")
            elif step.get("type") == "state":
                values = (
                    index + 1,
                    device_by_id[step["device_id"]]["name"],
                    step["label"],
                    "",
                )
            else:
                device = device_by_id[step["device_id"]]
                values = (
                    index + 1,
                    device["name"],
                    step["command_name"],
                    step.get("sequence_name", ""),
                )
            self.step_tree.insert("", "end", iid=str(index), values=values)
        if activity["steps"]:
            last_sequence = next(
                (
                    step.get("sequence_name")
                    for step in reversed(activity["steps"])
                    if step.get("type", "command") == "command"
                    and step.get("sequence_name")
                ),
                None,
            )
            if last_sequence:
                self.step_sequence_name.set(last_sequence)

    def _refresh_activity_setup(self, activity: dict[str, Any] | None) -> None:
        self.activity_device_list.delete(0, tk.END)
        for child in self.activity_inputs_frame.winfo_children():
            child.destroy()
        self.activity_input_vars.clear()
        devices = self.project["devices"]
        self.activity_device_ids = [device["id"] for device in devices]
        if not activity:
            self.channel_device_box["values"] = [""]
            self.volume_device_box["values"] = [""]
            self.channel_device.set("")
            self.volume_device.set("")
            return

        device_by_id = {device["id"]: device for device in devices}
        participating = activity.get("participating_device_ids")
        if participating is None:
            participating = self._legacy_activity_device_ids(activity)
        for index, device in enumerate(devices):
            self.activity_device_list.insert(tk.END, self._activity_device_label(device))
            if device["id"] in participating:
                self.activity_device_list.selection_set(index)

        labels = [""] + [self._activity_device_label(device) for device in devices]
        self.channel_device_box["values"] = labels
        self.volume_device_box["values"] = labels
        channel_id = activity.get("channel_device_id")
        volume_id = activity.get("volume_device_id")
        self.channel_device.set(
            self._activity_device_label(device_by_id[channel_id])
            if channel_id in device_by_id
            else ""
        )
        self.volume_device.set(
            self._activity_device_label(device_by_id[volume_id])
            if volume_id in device_by_id
            else ""
        )
        self._update_activity_role_choices()
        self._render_activity_inputs(activity.get("inputs", {}))

    def _legacy_activity_device_ids(self, activity: dict[str, Any]) -> list[str]:
        used = {step.get("device_id") for step in activity["steps"]}
        used.update((activity.get("channel_device_id"), activity.get("volume_device_id")))
        used.update(activity.get("inputs", {}))
        return [device["id"] for device in self.project["devices"] if device["id"] in used]

    def _render_activity_inputs(self, saved_inputs: dict[str, str]) -> None:
        for child in self.activity_inputs_frame.winfo_children():
            child.destroy()
        self.activity_input_vars.clear()
        ttk.Label(
            self.activity_inputs_frame,
            text="Notes only; add input commands to the action list.",
            style="Harmony.Muted.TLabel",
        ).grid(row=0, column=0, sticky="w", columnspan=2)
        row = 1
        for device in self.project["devices"]:
            if device["id"] not in self._selected_participating_device_ids():
                continue
            input_var = tk.StringVar(value=saved_inputs.get(device["id"], ""))
            self.activity_input_vars[device["id"]] = input_var
            input_commands = [
                command["name"]
                for command in device["commands"]
                if command["name"].casefold().startswith("input")
            ]
            ttk.Label(
                self.activity_inputs_frame, text=device["name"], width=15, anchor="w"
            ).grid(row=row, column=0, sticky="w", pady=2)
            input_box = ttk.Combobox(
                self.activity_inputs_frame,
                textvariable=input_var,
                values=input_commands,
                width=18,
            )
            input_box.grid(row=row, column=1, sticky="ew", pady=2)
            input_box.bind("<FocusOut>", self._save_activity_setup)
            input_box.bind("<<ComboboxSelected>>", self._save_activity_setup)
            row += 1
        self.activity_inputs_frame.columnconfigure(1, weight=1)

    def _activity_participation_changed(self) -> None:
        saved_inputs = {
            device_id: variable.get().strip()
            for device_id, variable in self.activity_input_vars.items()
            if variable.get().strip()
        }
        activity = self._selected_activity()
        if activity:
            required_ids = {
                step.get("device_id") for step in activity["steps"] if step.get("device_id")
            }
            missing = required_ids - set(self._selected_participating_device_ids())
            if missing:
                for index, device_id in enumerate(self.activity_device_ids):
                    if device_id in missing:
                        self.activity_device_list.selection_set(index)
                messagebox.showinfo(
                    "Device used by an action",
                    "Devices referenced by an action remain selected. Remove their actions "
                    "before deselecting them.",
                    parent=self.activity_editor_window,
                )
        self._update_activity_role_choices()
        self._render_activity_inputs(saved_inputs)
        self._save_activity_setup()

    def _selected_participating_device_ids(self) -> list[str]:
        return [
            self.activity_device_ids[index]
            for index in self.activity_device_list.curselection()
            if index < len(self.activity_device_ids)
        ]

    @staticmethod
    def _activity_device_label(device: dict[str, Any]) -> str:
        return f"{device['name']} [{device['id'][:6]}]"

    def _update_activity_role_choices(self) -> None:
        selected = [
            device
            for device in self.project["devices"]
            if device["id"] in self._selected_participating_device_ids()
        ]
        values = [""] + [self._activity_device_label(device) for device in selected]
        old_channel, old_volume = self.channel_device.get(), self.volume_device.get()
        self.channel_device_box["values"] = values
        self.volume_device_box["values"] = values
        self.channel_device.set(old_channel if old_channel in values else "")
        self.volume_device.set(old_volume if old_volume in values else "")

    def _selected_activity_device_id(self, label: str) -> str | None:
        return next(
            (
                device["id"]
                for device in self.project["devices"]
                if self._activity_device_label(device) == label
            ),
            None,
        )

    def _save_activity_setup(self, _event: tk.Event[Any] | None = None) -> None:
        activity = self._selected_activity()
        if not activity:
            return
        participating = self._selected_participating_device_ids()
        channel_id = self._selected_activity_device_id(self.channel_device.get())
        volume_id = self._selected_activity_device_id(self.volume_device.get())
        if channel_id is not None and channel_id not in participating:
            messagebox.showerror(
                "Invalid activity setup",
                "The channel-control device must be selected as an activity device.",
                parent=self.activity_editor_window,
            )
            return
        if volume_id is not None and volume_id not in participating:
            messagebox.showerror(
                "Invalid activity setup",
                "The volume-control device must be selected as an activity device.",
                parent=self.activity_editor_window,
            )
            return
        inputs = {
            device_id: variable.get().strip()
            for device_id, variable in self.activity_input_vars.items()
            if device_id in participating and variable.get().strip()
        }
        changes = {
            "participating_device_ids": participating,
            "channel_device_id": channel_id,
            "volume_device_id": volume_id,
            "inputs": inputs,
        }
        previous = {
            "participating_device_ids": activity.get(
                "participating_device_ids", self._legacy_activity_device_ids(activity)
            ),
            "channel_device_id": activity.get("channel_device_id"),
            "volume_device_id": activity.get("volume_device_id"),
            "inputs": activity.get("inputs", {}),
        }
        if changes != previous:
            activity.update(changes)
            self._mark_dirty()

    def _selected_step_index(self) -> int | None:
        selected = self.step_tree.selection()
        if not selected or not selected[0].isdigit():
            return None
        return int(selected[0])

    def _selected_activity(self) -> dict[str, Any] | None:
        for activity in self.project["activities"]:
            if activity["id"] == self.selected_activity_id:
                return activity
        return None

    def _apply_activity_name(self) -> None:
        activity = self._selected_activity()
        if not activity:
            return
        name = self.activity_name.get().strip()
        if not name:
            messagebox.showerror("Invalid activity", "Activity name cannot be empty.")
            return
        self._save_activity_setup()
        activity["name"] = name
        self._mark_dirty()
        self._refresh_activities()
        self.activity_editor_window.title(f"Activity Settings - {activity['name']}")
        for index, candidate in enumerate(self.project["activities"]):
            if candidate["id"] == activity["id"]:
                self.activity_list.selection_set(index)
                break

    def _add_activity(self) -> None:
        name = simpledialog.askstring("Add activity", "Activity name:", parent=self.root)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("Invalid activity", "Activity name cannot be empty.")
            return
        activity = {
            "id": uuid.uuid4().hex,
            "name": name,
            "steps": [],
            "participating_device_ids": [],
            "channel_device_id": None,
            "volume_device_id": None,
            "inputs": {},
        }
        self.project["activities"].append(activity)
        self.selected_activity_id = activity["id"]
        self._mark_dirty()
        self._refresh_activities()
        self.activity_list.selection_set(tk.END)
        self._open_activity_editor(activity["id"])

    def _remove_activity(self) -> None:
        activity = self._selected_activity()
        if not activity:
            return
        if not messagebox.askyesno(
            "Remove activity", f"Remove {activity['name']}?", parent=self.root
        ):
            return
        self.project["activities"].remove(activity)
        self.selected_activity_id = None
        self._mark_dirty()
        self._refresh_activities()

    def _refresh_step_choices(self) -> None:
        devices = sorted(
            self.project["devices"],
            key=lambda device: (
                device["category"].casefold(),
                device["brand"].casefold(),
                device["name"].casefold(),
                device["id"],
            ),
        )
        labels = [self._device_label(device) for device in devices]
        self.step_device_box["values"] = labels
        current = self.step_device.get()
        if current not in labels:
            self.step_device.set(labels[0] if labels else "")
        self._step_device_changed()

    @staticmethod
    def _device_label(device: dict[str, Any]) -> str:
        category = device["category"] or "Uncategorized"
        brand = (device["brand"] or "Unbranded").replace("_", " ")
        return f"{category} > {brand} > {device['name']} [{device['id'][:6]}]"

    def _step_device_changed(self, _event: tk.Event[Any] | None = None) -> None:
        selected_label = self.step_device.get()
        device = next(
            (
                item
                for item in self.project["devices"]
                if self._device_label(item) == selected_label
            ),
            None,
        )
        command_names = [command["name"] for command in device["commands"]] if device else []
        self.step_command_box["values"] = command_names
        if self.step_command.get() not in command_names:
            self.step_command.set(command_names[0] if command_names else "")

    def _add_step(self) -> None:
        activity = self._selected_activity()
        device = next(
            (
                item
                for item in self.project["devices"]
                if self._device_label(item) == self.step_device.get()
            ),
            None,
        )
        command_name = self.step_command.get()
        if not activity:
            messagebox.showerror("Select activity", "Create or select an activity first.")
            return
        if not device or not command_name:
            messagebox.showerror("Select command", "Choose a device with an IR command.")
            return
        self._save_activity_setup()
        participating = activity.setdefault(
            "participating_device_ids", self._legacy_activity_device_ids(activity)
        )
        if device["id"] not in participating:
            participating.append(device["id"])
        activity["steps"].append(
            {
                "type": "command",
                "sequence_name": self.step_sequence_name.get().strip() or "Sequence",
                "device_id": device["id"],
                "command_name": command_name,
            }
        )
        self._mark_dirty()
        self._show_activity(activity)
        self._refresh_activities()
        self.step_tree.selection_set(str(len(activity["steps"]) - 1))

    def _add_sequence_label(self) -> None:
        activity = self._selected_activity()
        if not activity:
            messagebox.showerror("Select activity", "Create or select an activity first.")
            return
        sequence_count = 1 + max(
            (
                int(step["sequence_name"].removeprefix("Sequence "))
                for step in activity["steps"]
                if step.get("type", "command") == "command"
                and step.get("sequence_name", "").startswith("Sequence ")
                and step["sequence_name"].removeprefix("Sequence ").isdigit()
            ),
            default=0,
        )
        self.step_sequence_name.set(f"Sequence {sequence_count}")

    def _remove_step(self) -> None:
        activity = self._selected_activity()
        index = self._selected_step_index()
        if not activity or index is None:
            return
        self._save_activity_setup()
        activity["steps"].pop(index)
        self._mark_dirty()
        self._refresh_activities()

    def _add_delay(self) -> None:
        activity = self._selected_activity()
        if not activity:
            messagebox.showerror("Select activity", "Create or select an activity first.")
            return
        ms = simpledialog.askinteger(
            "Add delay",
            f"Delay in milliseconds ({MIN_DELAY_MS}-{MAX_DELAY_MS}):",
            parent=self.root,
            initialvalue=500,
            minvalue=MIN_DELAY_MS,
            maxvalue=MAX_DELAY_MS,
        )
        if ms is None:
            return
        self._save_activity_setup()
        index = self._selected_step_index()
        position = len(activity["steps"]) if index is None else index + 1
        activity["steps"].insert(position, {"type": "delay", "ms": ms})
        self._mark_dirty()
        self._refresh_activities()
        self.step_tree.selection_set(str(position))

    def _move_step(self, offset: int) -> None:
        activity = self._selected_activity()
        index = self._selected_step_index()
        if not activity or index is None:
            return
        self._save_activity_setup()
        target = move_item(activity["steps"], index, offset)
        if target == index:
            return
        self._mark_dirty()
        self._refresh_activities()
        self.step_tree.selection_set(str(target))

    def _move_activity(self, offset: int) -> None:
        selection = self.activity_list.curselection()
        if not selection:
            return
        target = move_item(self.project["activities"], selection[0], offset)
        if target == selection[0]:
            return
        self._mark_dirty()
        self._refresh_activities()
        self.activity_list.selection_set(target)

    @staticmethod
    def _paged_label(index: int, name: str) -> str:
        return f"p{index // ITEMS_PER_PAGE + 1}.{index % ITEMS_PER_PAGE + 1}  {name}"

    def _refresh_device_order(self) -> None:
        self.device_order_list.delete(0, tk.END)
        for index, device in enumerate(self.project["devices"]):
            self.device_order_list.insert(tk.END, self._paged_label(index, device["name"]))

    def _move_device(self, offset: int) -> None:
        selection = self.device_order_list.curselection()
        if not selection:
            return
        target = move_item(self.project["devices"], selection[0], offset)
        if target == selection[0]:
            return
        self._mark_dirty()
        self._refresh_device_order()
        self.device_order_list.selection_set(target)
    def _load_remote_blob(self, blob: bytes, backup_path: Path) -> bool:
        try:
            config = parse_config(blob)
        except ConfigError as error:
            messagebox.showerror("Could not read remote", str(error), parent=self.root)
            return False
        project = project_from_remote(config, blob)
        self.remote_blob = blob
        self.remote_backup_path = backup_path
        self.clock.load(blob, backup_path)
        self.screen_styles.load(blob, backup_path)
        self.backup_browser.load(blob, backup_path)
        self.remote_project = copy.deepcopy(project)
        self._replace_project(project, backup_path.with_suffix(".json"))
        self._mark_dirty()
        messagebox.showinfo(
            "Backup copy ready to edit",
            f"Imported {len(project['devices'])} decoded devices and "
            f"{len(project['activities'])} decoded activities into a separate Remote Data "
            f"copy:\n{self.project_path}\n\n"
            f"The complete original backup remains at:\n{backup_path}\n\n"
            "Save Remote Data to keep your project edits. This project is not an .EZHex "
            "backup, and general device/activity edits cannot yet be compiled for upload. "
            "The backup is unchanged; nothing was written to the remote.",
            parent=self.root,
        )
        return True

    def _start_editing_backup_copy(self, blob: bytes, backup_path: Path) -> None:
        if not self._confirm_discard():
            return
        if self._load_remote_blob(blob, backup_path):
            self.tabs.select(self.device_tab)

    def _import_remote_backup(self) -> None:
        if not self._confirm_discard():
            return
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Open a Harmony One backup",
            initialdir=str(backup_directory()),
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            blob = Path(chosen).read_bytes()
        except OSError as error:
            messagebox.showerror("Could not read backup", str(error), parent=self.root)
            return
        self._load_remote_blob(blob, Path(chosen))

    def _export_remote_objects(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a Harmony One backup to export",
            initialdir=str(backup_directory()),
            filetypes=[("Harmony backup", "*.EZHex"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            blob = Path(chosen).read_bytes()
        except OSError as error:
            messagebox.showerror("Object export failed", str(error), parent=self.root)
            return
        self._export_objects_from_blob(blob, Path(chosen).parent)

    def _export_objects_from_blob(self, blob: bytes, initial_dir: Path) -> None:
        try:
            bundle = build_remote_objects(blob)
        except (ValueError, IndexError) as error:
            messagebox.showerror("Object export failed", str(error), parent=self.root)
            return
        selected = self._choose_objects_to_export(bundle)
        if selected is None:
            return
        self.export_objects_with_ids(
            blob, selected, initial_dir=initial_dir, total=len(bundle["objects"])
        )

    def backup_initialdir(self) -> Path:
        """Default directory for choosing backup files."""
        return backup_directory()

    def export_objects_with_ids(
        self,
        blob: bytes,
        selected_ids: list[str],
        initial_dir: Path | None = None,
        total: int | None = None,
    ) -> None:
        """Ask for a destination and export the given decoded objects."""
        parent = filedialog.askdirectory(
            parent=self.root,
            title="Choose the parent folder for the exported objects",
            initialdir=str(initial_dir or backup_directory()),
        )
        if not parent:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = Path(parent) / f"HarmonyObjects-{stamp}"
        try:
            written = export_remote_objects(blob, destination, selected_ids=selected_ids)
        except (OSError, ValueError, IndexError) as error:
            messagebox.showerror(
                "Object export failed",
                f"{error}\n\nIf created, the incomplete export folder is:\n{destination}",
                parent=self.root,
            )
            return
        saved = len(written) - 1
        counts = f"{saved} of {total}" if total is not None else f"{saved}"
        messagebox.showinfo(
            "Objects exported",
            f"Saved {counts} device/activity objects "
            f"and an index to:\n{destination}\n\nKeep the original .EZHex backup too. These "
            "objects are incomplete source data and cannot yet be uploaded to another remote.",
            parent=self.root,
        )

    def _choose_objects_to_export(self, bundle: dict[str, Any]) -> list[str] | None:
        """Show every decoded object and return chosen ids, or None if cancelled."""
        devices = [item for item in bundle["objects"] if item["object_type"] == "device"]
        activities = [item for item in bundle["objects"] if item["object_type"] == "activity"]
        dialog = tk.Toplevel(self.root)
        dialog.title("Choose objects to back up")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("640x460")
        dialog.columnconfigure(0, weight=1)
        dialog.columnconfigure(1, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(
            dialog,
            text=(
                "Each selected device or activity is saved as its own JSON file with an "
                "index. These files are archival source data, not yet transfer-ready."
            ),
            wraplength=600,
            justify="left",
            padding=(10, 10),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        def build_list(column: int, title: str, items: list[dict[str, Any]]) -> tk.Listbox:
            frame = ttk.LabelFrame(dialog, text=f"{title} ({len(items)})", padding=6)
            frame.grid(row=1, column=column, sticky="nsew", padx=10, pady=(0, 6))
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            listing = tk.Listbox(frame, selectmode="extended", exportselection=False)
            listing.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(frame, orient="vertical", command=listing.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            listing.configure(yscrollcommand=scroll.set)
            for item in items:
                listing.insert(tk.END, f"{item['name']}  [{item['object_id']}]")
            listing.selection_set(0, tk.END)
            return listing

        activity_list = build_list(0, "Activities", activities)
        device_list = build_list(1, "Devices", devices)
        result: list[list[str] | None] = [None]

        def set_all(selected: bool) -> None:
            for listing in (device_list, activity_list):
                if selected:
                    listing.selection_set(0, tk.END)
                else:
                    listing.selection_clear(0, tk.END)

        def export_now() -> None:
            chosen = [devices[i]["object_id"] for i in device_list.curselection()]
            chosen += [activities[i]["object_id"] for i in activity_list.curselection()]
            if not chosen:
                messagebox.showinfo(
                    "Nothing selected",
                    "Select at least one device or activity to back up.",
                    parent=dialog,
                )
                return
            result[0] = chosen
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=10)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="Select all", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(buttons, text="Select none", command=lambda: set_all(False)).pack(
            side="left", padx=5
        )
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Export selected...", command=export_now).pack(
            side="right", padx=5
        )
        dialog.wait_window()
        return result[0]

    def _export_selected_project_object(self, object_type: str) -> None:
        item = self._selected_device() if object_type == "device" else self._selected_activity()
        if item is None:
            messagebox.showinfo(
                "Select an object",
                f"Open the settings for a {object_type} before exporting it.",
                parent=self.root,
            )
            return
        try:
            package = export_project_object(self.project, object_type, item["id"])
        except ProjectError as error:
            messagebox.showerror("Object export failed", str(error), parent=self.root)
            return
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title=f"Export {object_type} object",
            initialdir=str(self.project_path.parent),
            initialfile=f"Harmony-{object_type}-{item['id'][:8]}.json",
            defaultextension=".json",
            filetypes=[("Remote Data object", "*.json"), ("All files", "*.*")],
        )
        if not destination:
            return
        try:
            with Path(destination).open("x", encoding="utf-8", newline="\n") as output:
                json.dump(package, output, indent=2, ensure_ascii=False)
                output.write("\n")
        except OSError as error:
            messagebox.showerror("Object export failed", str(error), parent=self.root)
            return
        dependency_count = len(package["dependency_devices"])
        messagebox.showinfo(
            "Object exported",
            f"Saved {item['name']} to:\n{destination}\n\n"
            f"Included device dependencies: {dependency_count}. This local Remote Data package "
            "is not upload-ready; no remote data was changed.",
            parent=self.root,
        )

    def _import_project_object(self, object_type: str) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title=f"Import {object_type} object",
            initialdir=str(self.project_path.parent),
            filetypes=[("Remote Data object", "*.json"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            package = json.loads(Path(chosen).read_text(encoding="utf-8"))
            if not isinstance(package, dict) or package.get("object_type") != object_type:
                raise ProjectError(f"Select a {object_type} object package")
            updated, imported = import_project_object(self.project, package)
        except (OSError, UnicodeError, json.JSONDecodeError, ProjectError) as error:
            messagebox.showerror("Object import failed", str(error), parent=self.root)
            return
        self.project = updated
        self._mark_dirty()
        self._refresh_devices()
        self._refresh_activities()
        if object_type == "device":
            self._open_device_settings(imported["id"])
        else:
            self._open_activity_editor(imported["id"])
        self.status.set(f"Imported {imported['name']} into local Remote Data")
        messagebox.showinfo(
            "Object imported",
            f"Imported {imported['name']} into this local Remote Data."
            " The object IDs were remapped; activity dependencies were copied as local devices.\n\n"
            "This does not create a remote configuration or upload anything.",
            parent=self.root,
        )

    def _refresh_usb(self) -> None:
        for item in self.usb_tree.get_children():
            self.usb_tree.delete(item)
        try:
            devices = list_logitech_usb_devices()
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            self.usb_status.set(f"Read-only device discovery failed: {error}")
            return
        if not devices:
            self.usb_status.set(
                "No currently connected Logitech USB/HID devices were found. "
                "Discovery is informational only; general Remote Data uploads remain blocked."
            )
            return
        for name, instance_id in devices:
            self.usb_tree.insert("", "end", values=(name, instance_id))
        self.usb_status.set(
            f"Found {len(devices)} Logitech USB/HID device(s). "
            "Only VID_046D&PID_C121 is accepted by the Harmony One reader."
        )


def run_self_test() -> int:
    from zoneinfo import ZoneInfo, available_timezones
    import struct

    project = bundled_project()
    features = load_feature_catalog(resource_path("data/features.json"))
    styles = load_builtin_styles()
    default_icons = sorted(
        (Path(__file__).resolve().parent / "data" / "default_icons").glob("*.png")
    )
    if not default_icons or any(
        not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in default_icons
    ):
        raise RuntimeError("Bundled default menu icons are missing or invalid")
    default_slides = sorted(
        (Path(__file__).resolve().parent / "data" / "default_slideshow").glob("*.png")
    )
    if len(default_slides) != 4 or any(
        not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        or struct.unpack_from(">II", path.read_bytes(), 16) != (176, 220)
        for path in default_slides
    ):
        raise RuntimeError("Bundled default slideshow images are missing or invalid")
    presets = []
    for label, filename in FQ_PRESET_FILES.items():
        path = resource_path(f"data/presets/{filename}")
        config = validate_source_save(path.read_bytes(), label)
        presets.append(f"{label} (architecture {config.architecture}, skin {config.skin})")
    zones = available_timezones()
    required_zones = ("America/Los_Angeles", "Asia/Tokyo", "Europe/London")
    missing_zones = [zone for zone in required_zones if zone not in zones]
    if missing_zones:
        raise RuntimeError(f"Bundled IANA timezone data is missing {missing_zones}")
    for zone in required_zones:
        ZoneInfo(zone)
    device_count = len(project["devices"])
    signal_count = sum(len(device["commands"]) for device in project["devices"])
    print(f"Remote Data schema: v{project['formatVersion']}")
    print(f"Bundled library devices: {device_count}")
    print(f"Bundled parsed IR commands: {signal_count}")
    print(f"Tracked application features: {len(features)}")
    print(f"Bundled display styles: {', '.join(style.name for style in styles.values())}")
    print(f"Bundled default menu icons: {len(default_icons)}")
    print(f"Bundled default slideshow images: {len(default_slides)}")
    print(f"Bundled full-save presets: {', '.join(presets)}")
    print(f"Bundled IANA timezone database: {len(zones)} zones; sample zones resolved")
    print(
        "USB writes: guarded display-style writes and clock updates are not hardware-validated; "
        "general Remote Data uploads remain blocked"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="validate bundled offline data")
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    root = tk.Tk()
    HarmonyOfflineApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
