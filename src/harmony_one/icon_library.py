"""User-managed library of Harmony One menu icons."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import tkinter as tk

from harmony_one.app_paths import icon_library_directory
from harmony_one.activity_icons import IconError
from harmony_one.pictures import decode_picture, encode_png, picture_at
from harmony_one.remote_menu_icons import import_remote_menu_icon
from harmony_one.screen_styles import ScreenStyleError, list_menu_icon_choices


class IconLibraryError(ValueError):
    """A remote icon library export could not be completed safely."""


def export_remote_menu_icons(
    blob: bytes, directory: Path | None = None
) -> tuple[Path, ...]:
    """Save distinct 51 x 48 menu icons from a validated backup as PNG files."""
    try:
        choices = list_menu_icon_choices(blob)
    except ScreenStyleError as error:
        raise IconLibraryError(str(error)) from error
    if not choices:
        raise IconLibraryError("No 51 x 48 menu icons were found in this remote backup.")

    destination = directory or icon_library_directory()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise IconLibraryError(f"Could not create the icons folder {destination}: {error}") from error

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    paths: list[Path] = []
    try:
        for index, image_data in enumerate(choices, start=1):
            picture = picture_at(image_data, 0, len(image_data))
            if (
                picture is None
                or picture.end != len(image_data)
                or (picture.width, picture.height) != (51, 48)
            ):
                raise IconLibraryError(
                    f"Menu icon {index} did not validate as a complete 51 x 48 image."
                )
            path = destination / f"harmony-one-icon-{stamp}-{index:03}.png"
            with path.open("xb") as output:
                output.write(encode_png(decode_picture(image_data, picture)))
            paths.append(path)
    except (OSError, ValueError) as error:
        written = f" Already exported: {', '.join(map(str, paths))}." if paths else ""
        raise IconLibraryError(f"Could not export remote menu icons: {error}.{written}") from error
    return tuple(paths)


def seed_default_icons(
    directory: Path | None = None,
    defaults_directory: Path | None = None,
) -> tuple[Path, ...]:
    """Copy bundled menu icons and archived button artwork without overwriting files."""
    destination = directory or icon_library_directory()
    defaults = defaults_directory or (
        Path(__file__).resolve().parent / "data" / "default_icons"
    )
    try:
        defaults_found = sorted(defaults.rglob("*.png"), key=lambda path: str(path).casefold())
        if not defaults_found:
            raise IconLibraryError(f"No bundled default icons were found in {defaults}.")
        destination.mkdir(parents=True, exist_ok=True)
        seeded = []
        for source in defaults_found:
            target = destination / source.relative_to(defaults)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(source, target)
            seeded.append(target)
    except OSError as error:
        raise IconLibraryError(
            f"Could not initialize the user icons folder {destination}: {error}"
        ) from error
    return tuple(seeded)


def load_user_menu_icons(
    master: tk.Misc, directory: Path | None = None
) -> tuple[tuple[tuple[str, bytes], ...], tuple[str, ...]]:
    """Load PNG/GIF files users added to the icon folder for the remote icon picker."""
    folder = directory or icon_library_directory()
    if not folder.exists():
        return (), ()
    try:
        paths = sorted(
            (
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.casefold() in (".png", ".gif")
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError as error:
        return (), (f"Could not read the icons folder {folder}: {error}",)

    loaded: list[tuple[str, bytes]] = []
    errors: list[str] = []
    for path in paths:
        try:
            loaded.append((f"User icon: {path.name}", import_remote_menu_icon(path, master)))
        except (IconError, OSError, ValueError) as error:
            errors.append(f"{path.name}: {error}")
    return tuple(loaded), tuple(errors)
