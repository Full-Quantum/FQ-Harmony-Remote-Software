"""Default and user-managed full-screen slideshow image library."""

from __future__ import annotations

from pathlib import Path
import shutil

from harmony_one.app_paths import slideshow_library_directory


class SlideshowLibraryError(ValueError):
    """A slideshow image library operation could not be completed."""


def bundled_default_slideshow_images() -> tuple[Path, ...]:
    """Return original bundled slides, not possibly customized user-library copies."""
    directory = Path(__file__).resolve().parent / "data" / "default_slideshow"
    images = tuple(directory / name for name in (
        "01-charging-battery.png", "02-blurred-lights-1.png",
        "03-blurred-lights-2.png", "04-blurred-lights-3.png",
    ))
    if not all(path.is_file() for path in images):
        raise SlideshowLibraryError(f"The four bundled default images are missing from {directory}.")
    return images


def seed_default_slideshow_images(
    directory: Path | None = None,
    defaults_directory: Path | None = None,
) -> tuple[Path, ...]:
    """Copy bundled default slides into the user library without overwriting files."""
    destination = directory or slideshow_library_directory()
    defaults = defaults_directory or (
        Path(__file__).resolve().parent / "data" / "default_slideshow"
    )
    try:
        defaults_found = sorted(defaults.glob("*.png"), key=lambda path: path.name.casefold())
        if not defaults_found:
            raise SlideshowLibraryError(
                f"No bundled default slideshow images were found in {defaults}."
            )
        destination.mkdir(parents=True, exist_ok=True)
        seeded = []
        for source in defaults_found:
            target = destination / source.name
            if not target.exists():
                shutil.copyfile(source, target)
            seeded.append(target)
    except OSError as error:
        raise SlideshowLibraryError(
            f"Could not initialize the slideshow folder {destination}: {error}"
        ) from error
    return tuple(seeded)
