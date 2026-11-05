import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench.player import PlayConfig, PlayWindow


def test_view_size_is_exact_and_does_not_start_or_patch_the_core(tmp_path):
    application = QApplication.instance() or QApplication([])
    window = PlayWindow(PlayConfig(tmp_path / "unused.so", tmp_path, tmp_path / "unused.cue", tmp_path / "saves"))
    try:
        window.show()
        application.processEvents()
        window.view.aspect = 1.2190476655960083
        window._set_screen("16:9 fill")
        assert window.set_game_view_size()
        application.processEvents()
        assert (window.view.width(), window.view.height()) == (640, 480)
        assert (window.view.target().width(), window.view.target().height()) == (640, 480)
        assert window.view.screen == "4:3"
        assert window.worker.core is None and window.worker._camera is None
        window.full.setChecked(True)
        window._toggle_full_screen(True)
        application.processEvents()
        assert window.isFullScreen()
        assert window.set_game_view_size()
        deadline = time.monotonic() + 3
        while (window.view.width(), window.view.height()) != (640, 480) and time.monotonic() < deadline:
            application.processEvents(); time.sleep(0.01)
        assert not window.isFullScreen() and not window.full.isChecked()
        assert (window.view.width(), window.view.height()) == (640, 480)
        window.view.second = QImage(640, 480, QImage.Format.Format_RGB32)
        assert not window.set_game_view_size()  # must not reshape an active split-screen layout
    finally:
        window.close()
        application.processEvents()
