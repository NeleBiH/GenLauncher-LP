"""Asset path resolution for UI images."""

from __future__ import annotations

from pathlib import Path

_IMAGES_DIR = Path(__file__).parent / "images"


def image_path(name: str) -> str:
    """Return absolute path string for an image asset, suitable for QSS/QPixmap."""
    return str(_IMAGES_DIR / name)


# Convenience constants
BACKGROUND_ZH = image_path("Background.png")
BACKGROUND_GENERALS = image_path("BackgroundGenerals.png")
ADDMOD_BG = image_path("addmodbg.png")

# Application icon (from GenLauncherRewritten-reference GenLauncherGo.ico).
# Kept as separate PNG frames rather than the .ico itself: Qt's ICO reader is
# not guaranteed on every Linux build, and a task manager wants a real PNG.
ICON_16 = image_path("app_icon_16.png")
ICON_32 = image_path("app_icon_32.png")
ICON_48 = image_path("app_icon_48.png")
ICON_64 = image_path("app_icon_64.png")
ICON_128 = image_path("app_icon_128.png")
ICON_256 = image_path("app_icon_256.png")

APP_ICON = ICON_256
ICON_PATHS = (ICON_16, ICON_32, ICON_48, ICON_64, ICON_128, ICON_256)


def app_icon():
    """Build the application QIcon from every available frame.

    Deliberately imports Qt inside the function: this module stays importable
    without PySide6 so the headless CLI can load it.
    """
    from PySide6.QtGui import QIcon

    icon = QIcon()
    for path in ICON_PATHS:
        icon.addFile(path)
    return icon


def apply_app_icon(target) -> None:
    """Set the application icon on a QApplication or a window."""
    icon = app_icon()
    if not icon.isNull():
        target.setWindowIcon(icon)
