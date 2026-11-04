"""DW1-play: runs the game directly, with the game and settings chosen in DW1-settings."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .app_icon import apply_icon, set_app_id
from .catalog import open_catalogue
from .gui import LAST_PROJECT, Workspace, workbench_settings

NAME = "DW1-play"


def main() -> int:
    set_app_id(NAME)
    application = QApplication(sys.argv)
    application.setApplicationName(NAME)
    application.setQuitOnLastWindowClosed(False)  # the app ends with the game, reported by the Play panel
    path = workbench_settings().value(LAST_PROJECT, "")
    try:
        catalogue = open_catalogue(Path(path)) if path else None
    except Exception as exc:
        QMessageBox.warning(None, NAME, f"Could not open your game at {path}:\n{exc}\n\nOpen it again in DW1-settings.")
        return 1
    if catalogue is None:
        QMessageBox.information(None, NAME, "Open your game in DW1-settings first; DW1-play then runs it directly.")
        return 1
    apply_icon(application, Path(catalogue.meta().get("source_path", "")))
    # The same panel as DW1-settings' Play tab, not shown: it holds the settings and how a game starts.
    workspace = Workspace(catalogue)
    panel = workspace.play_panel
    panel.player_closed.connect(application.quit)

    def start():
        if not panel.launch_directly():
            application.quit()

    QTimer.singleShot(0, start)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
