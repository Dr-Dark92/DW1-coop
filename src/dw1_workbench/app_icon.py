"""The apps' identity on the desktop: the Agumon icon and Windows shortcuts.

The icon is rendered once from the user's own disc and kept on this computer; no game art is bundled.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QIcon

from .avatars import render_character
from .errors import DiscFormatError, ValidationError

AGUMON = 3
ICON_SIZE = 256
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
SHORTCUTS = {"DW1-settings": "dw1_workbench.app", "DW1-play": "dw1_workbench.play_app"}


def icon_folder() -> Path:
    data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    return Path(data) / "DW1-coop" / "icons"


def ensure_icon(disc: Path, folder: Path | None = None) -> Path | None:
    """The PNG of Agumon's icon, with an .ico beside it; None when the disc can't supply it."""
    folder = folder or icon_folder()
    png = folder / "agumon.png"
    if png.is_file() and png.with_suffix(".ico").is_file():
        return png
    try:
        image = render_character(Path(disc), AGUMON, ICON_SIZE)
        folder.mkdir(parents=True, exist_ok=True)
        if image is None or not image.save(str(png), "PNG"):
            return None
        with Image.open(png) as opened:
            opened.save(png.with_suffix(".ico"), sizes=ICO_SIZES)
    except (OSError, DiscFormatError, ValidationError):
        return None
    return png


def apply_icon(application, disc: Path) -> Path | None:
    png = ensure_icon(disc)
    if png is not None:
        application.setWindowIcon(QIcon(str(png)))
    return png


def set_app_id(name: str):
    """Give this process its own taskbar button and icon on Windows, instead of Python's."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"DW1-coop.{name}")


def shortcut_script(icon: Path, python: Path) -> str:
    """PowerShell creating both apps' desktop shortcuts, replacing earlier ones."""
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    lines = ["$shell = New-Object -ComObject WScript.Shell", "$desktop = [Environment]::GetFolderPath('Desktop')"]
    for name, module in SHORTCUTS.items():
        lines += [f"$link = $shell.CreateShortcut((Join-Path $desktop {quote(name + '.lnk')}))",
                  f"$link.TargetPath = {quote(python)}", f"$link.Arguments = {quote('-m ' + module)}",
                  f"$link.WorkingDirectory = {quote(python.parent)}", f"$link.IconLocation = {quote(icon)}",
                  f"$link.Description = {quote(name)}", "$link.Save()"]
    return "; ".join(lines)


def create_shortcuts(disc: Path):
    """Desktop shortcuts for both apps, with the Agumon icon. Windows only; raises OSError when they can't be made."""
    png = ensure_icon(disc)
    if png is None:
        raise OSError("The Agumon icon could not be made from the disc.")
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    shortcut_script(png.with_suffix(".ico"), windowless if windowless.is_file() else python)],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
