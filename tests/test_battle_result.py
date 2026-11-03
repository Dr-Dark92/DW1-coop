import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench.player import battle_result_image
from dw1_workbench.progression import BattleResult, total_exp


@pytest.mark.parametrize("width,height", [(320, 240), (640, 480)])
def test_result_replaces_only_stats_panel(width, height):
    app = QApplication.instance() or QApplication([])
    # Qt's Windows offscreen plugin does not discover installed fonts.
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf"
    if not QFontDatabase.families() and font.exists():
        QFontDatabase.addApplicationFont(str(font))
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("magenta"))
    assert battle_result_image(image, None) is image
    result = battle_result_image(image, BattleResult(30, total_exp(20) + 30))
    assert result.pixelColor(width // 2, height // 4) != QColor("magenta")
    assert result.pixelColor(width // 2, height * 3 // 4) == QColor("magenta")
    assert result.pixelColor(0, 0) == QColor("magenta")
    assert image.pixelColor(width // 2, height // 4) == QColor("magenta")
