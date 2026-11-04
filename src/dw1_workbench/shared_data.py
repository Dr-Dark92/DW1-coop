"""Where the apps keep settings, saves and icons: one folder in the user's home.

Not the registry or AppData: on Windows a program started from inside a packaged app gets its own private copy of those, so
the same app started two ways would have two sets of settings and saves. Whatever an older version kept there is brought in
once per copy, and never over anything already here. The old copies are left in place.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

HOME_VARIABLE = "DW1_COOP_HOME"  # another folder instead, for tests
OLD_ORGANISATION, OLD_APPLICATION = "DW1-coop", "workbench"
BROUGHT_IN = ".brought-in-to-shared-folder"


def data_root() -> Path:
    chosen = os.environ.get(HOME_VARIABLE)
    return Path(chosen) if chosen else Path.home() / ".dw1-coop"


def settings_path() -> Path:
    return data_root() / "settings.ini"


def app_settings() -> QSettings:
    return QSettings(str(settings_path()), QSettings.Format.IniFormat)


def saves_root() -> Path:
    return data_root() / "play"


def icons_folder() -> Path:
    return data_root() / "icons"


def old_saves_root() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / OLD_ORGANISATION / "play"


def bring_in_settings(old: QSettings | None = None) -> bool:
    """Copy the old settings, the first time. True when it did."""
    if settings_path().exists():
        return False
    old = old if old is not None else QSettings(OLD_ORGANISATION, OLD_APPLICATION)
    data_root().mkdir(parents=True, exist_ok=True)
    new = app_settings()
    for key in old.allKeys():
        new.setValue(key, old.value(key))
    new.sync()
    return True


def bring_in_saves(old_root: Path | None = None) -> list[str]:
    """Copy each old disc's saves that aren't here yet, once per old folder. The names of what was copied."""
    old_root = old_root if old_root is not None else old_saves_root()
    copied: list[str] = []
    if not old_root.is_dir():
        return copied
    for folder in sorted(path for path in old_root.iterdir() if path.is_dir()):
        marker = folder / BROUGHT_IN
        if marker.exists():
            continue
        for source in sorted(path for path in folder.rglob("*") if path.is_file()):
            target = saves_root() / folder.name / source.relative_to(folder)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(str(source.relative_to(old_root)))
        marker.write_text("Copied to the shared folder; this copy is no longer used.\n", encoding="utf-8")
    return copied


def bring_in_old_data():
    """At an app's start. Trouble here leaves the app with what is in the shared folder; it must still open."""
    try:
        bring_in_settings()
        bring_in_saves()
    except OSError:
        pass
