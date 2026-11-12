"""Paths for user-managed files stored beside the portable app."""

from __future__ import annotations

import sys
from pathlib import Path


def portable_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def application_data_directory(install_directory: Path | None = None) -> Path:
    directory = install_directory or portable_directory()
    if getattr(sys, "frozen", False) and directory.name.casefold() == "portable":
        return directory.parent
    return directory


def backup_directory(install_directory: Path | None = None) -> Path:
    return application_data_directory(install_directory) / "backups"


def icon_library_directory(install_directory: Path | None = None) -> Path:
    return application_data_directory(install_directory) / "icons"


def slideshow_library_directory(install_directory: Path | None = None) -> Path:
    return application_data_directory(install_directory) / "slideshow"


def custom_style_library_path(install_directory: Path | None = None) -> Path:
    return application_data_directory(install_directory) / "custom_screen_styles.json"
