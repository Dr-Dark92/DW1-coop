from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .app_icon import set_app_id
from .gui import APP_NAME, MainWindow, workbench_settings
from .shared_data import bring_in_old_data


def main() -> int:
    set_app_id(APP_NAME)
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    bring_in_old_data()
    window = MainWindow(workbench_settings()); window.show()
    window.open_last_project()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
