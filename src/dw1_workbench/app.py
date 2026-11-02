from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .gui import MainWindow


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("DW1-coop Asset Workbench")
    window = MainWindow(); window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
