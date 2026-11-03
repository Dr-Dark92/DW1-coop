"""Play window: runs the user's game through a libretro core on a worker thread.

The core runs on its own QThread at the core's frame rate. Frames, audio and messages reach the GUI
through queued signals only; the GUI talks back through thread-safe attributes and a command queue.
"""
from __future__ import annotations

import hashlib
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QBuffer, QCoreApplication, QByteArray, QIODevice, QObject, QRectF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QLabel, QLineEdit, QMainWindow, QMessageBox, QToolBar,
                               QWidget)

from .libretro import (BUTTON, PIXEL_0RGB1555, PIXEL_RGB565, PIXEL_XRGB8888, Frame, LibretroCore,
                       LibretroError)
from .quicksave import RESUME_SLOT, SLOT_COUNT, QuickSaveError, QuickSaveStore

FAST_FORWARD = 4  # speed while fast-forward is held
MEMORY_CARD_FLUSH_FRAMES = 600  # write a changed memory card about every 10 s
IMAGE_FORMATS = {PIXEL_XRGB8888: QImage.Format.Format_RGB32, PIXEL_RGB565: QImage.Format.Format_RGB16,
                 PIXEL_0RGB1555: QImage.Format.Format_RGB555}

# Keyboard defaults. On the PlayStation core B is Cross, A Circle, Y Square and X Triangle.
DEFAULT_KEYS = {
    Qt.Key.Key_Up: "Up", Qt.Key.Key_Down: "Down", Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Z: "B", Qt.Key.Key_X: "A", Qt.Key.Key_A: "Y", Qt.Key.Key_S: "X",
    Qt.Key.Key_Q: "L", Qt.Key.Key_W: "R", Qt.Key.Key_E: "L2", Qt.Key.Key_R: "R2",
    Qt.Key.Key_Return: "Start", Qt.Key.Key_Enter: "Start", Qt.Key.Key_Backspace: "Select",
}
KEY_HELP = ("Arrows: D-pad  •  Z Cross  X Circle  A Square  S Triangle  •  Q/W L1/R1  E/R L2/R2  •  "
            "Enter Start  Backspace Select  •  F5 quick save  F8 quick load  F6/F7 slot  •  "
            "hold Tab fast-forward  •  P pause  •  F11 full screen")


@dataclass(frozen=True)
class PlayConfig:
    core_path: Path
    bios_dir: Path
    disc_path: Path
    store_dir: Path
    launch_slot: int | None = None
    resume_on_close: bool = True
    options: dict[str, str] = field(default_factory=dict)


def frame_image(frame: Frame) -> QImage:
    return QImage(frame.data, frame.width, frame.height, frame.pitch, IMAGE_FORMATS[frame.pixel_format]).copy()


def png_bytes(image: QImage | None) -> bytes | None:
    if image is None or image.isNull():
        return None
    data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


class EmulatorWorker(QObject):
    frame_ready = Signal()
    audio_ready = Signal(bytes)
    message = Signal(str)
    started_ok = Signal(object)  # dict with core identity and timing
    failed = Signal(str)
    slot_saved = Signal(object)  # SlotInfo
    slot_loaded = Signal(int)
    command_failed = Signal(str)
    finished = Signal()

    def __init__(self, config: PlayConfig):
        super().__init__()
        self.config = config
        self.store = QuickSaveStore(config.store_dir)
        self.buttons = 0
        self.paused = False
        self.fast_forward = False
        self._stop = threading.Event()
        self._commands: queue.SimpleQueue = queue.SimpleQueue()
        self._image_lock = threading.Lock()
        self._image: QImage | None = None  # newest frame
        self._image_pending = False
        self._last_image: QImage | None = None
        self._card_hash = None
        self.core: LibretroCore | None = None

    # -- called from the GUI thread ------------------------------------------------------------------------

    def request_stop(self):
        self._stop.set()

    def quick_save(self, slot: int, note: str = ""):
        self._commands.put(("save", slot, note))

    def quick_load(self, slot: int):
        self._commands.put(("load", slot))

    def reset(self):
        self._commands.put(("reset",))

    def take_frame(self) -> QImage | None:
        with self._image_lock:
            self._image_pending = False
            return self._image

    # -- worker thread -------------------------------------------------------------------------------------

    @Slot()
    def run(self):
        config = self.config
        try:
            config.store_dir.mkdir(parents=True, exist_ok=True)
            self.core = LibretroCore(config.core_path, config.bios_dir, config.store_dir, config.options,
                                     log=self.message.emit)
            self.core.start(config.disc_path)
        except (LibretroError, OSError) as exc:
            self.failed.emit(str(exc)); self.finished.emit()
            return
        core = self.core
        try:
            card = self.store.read_memory_card()
            if card is not None:
                core.load_save_ram(card); self._card_hash = hashlib.sha256(card).digest()
                self.message.emit(f"Memory card loaded from {self.store.memory_card_path}")
            if config.launch_slot is not None:
                self._load(config.launch_slot)
            self.started_ok.emit({"core": core.info.name, "version": core.info.version, "fps": core.fps,
                                  "sample_rate": core.sample_rate, "aspect": core.aspect_ratio})
            self._loop(core)
        except Exception as exc:
            self.failed.emit(f"The game stopped unexpectedly: {exc}")
        finally:
            self._shutdown(core)
            self.finished.emit()

    def _loop(self, core: LibretroCore):
        frames = 0
        deadline = time.perf_counter()
        while not self._stop.is_set() and not core.shutdown_requested:
            self._run_commands(core)
            if self.paused:
                self._stop.wait(0.02); deadline = time.perf_counter()
                continue
            core.buttons = self.buttons
            frame, audio = core.run_frame()
            frames += 1
            if frame is not None:
                self._publish(frame_image(frame))
            fast = self.fast_forward
            if audio and not fast:
                self.audio_ready.emit(audio)
            if frames % MEMORY_CARD_FLUSH_FRAMES == 0:
                self._flush_memory_card(core)
            # core.fps is read every frame: cores may change timing (Beetle PSX settles on 59.826 fps after boot).
            deadline += 1.0 / (core.fps * (FAST_FORWARD if fast else 1))
            delay = deadline - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            elif delay < -0.25:  # far behind (a slow frame or a breakpoint): do not try to catch up
                deadline = time.perf_counter()

    def _publish(self, image: QImage):
        self._last_image = image
        with self._image_lock:
            self._image, notify = image, not self._image_pending
            self._image_pending = True
        if notify:  # at most one queued notification; the GUI always takes the newest frame
            self.frame_ready.emit()

    def _run_commands(self, core: LibretroCore):
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if command[0] == "save":
                    self.slot_saved.emit(self._save(command[1], command[2]))
                elif command[0] == "load":
                    self._load(command[1]); self.slot_loaded.emit(command[1])
                elif command[0] == "reset":
                    core.reset(); self.message.emit("Console reset")
            except (LibretroError, QuickSaveError, OSError) as exc:
                self.command_failed.emit(str(exc))

    def _save(self, slot: int, note: str = ""):
        core = self.core
        info = self.store.save(slot, core.serialize(), png_bytes(self._last_image), core.info.name,
                               core.info.version, note)
        self.message.emit(f"{info.label} saved ({info.size:,} bytes){f': {info.note}' if info.note else ''}")
        return info

    def _load(self, slot: int):
        core = self.core
        info = self.store.info(slot)
        core.unserialize(self.store.load(slot, core=core.info.name))
        if info and info.core_version != core.info.version:
            self.message.emit(f"{info.label} was made by core version {info.core_version}; "
                              f"running {core.info.version}")
        self.message.emit(f"{info.label if info else f'Slot {slot}'} loaded")

    def _flush_memory_card(self, core: LibretroCore):
        card = core.save_ram()
        if card is None:
            return
        digest = hashlib.sha256(card).digest()
        if digest != self._card_hash:
            self.store.write_memory_card(card); self._card_hash = digest
            self.message.emit("Memory card written")

    def _shutdown(self, core: LibretroCore):
        try:
            if self.config.resume_on_close and core.av is not None:
                self.slot_saved.emit(self._save(RESUME_SLOT, "Saved when the game was closed"))
        except (LibretroError, QuickSaveError, OSError) as exc:
            self.message.emit(f"Resume save failed: {exc}")
        try:
            if core.av is not None:
                self._flush_memory_card(core)
        except OSError as exc:
            self.message.emit(f"Memory card could not be written: {exc}")
        core.stop()
        self.message.emit("Game closed")


class AudioOutput:
    """Pushes the core's 16-bit stereo audio to the default output; drops audio rather than lagging."""

    def __init__(self, sample_rate: int, log):
        audio_format = QAudioFormat()
        audio_format.setSampleRate(sample_rate); audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        self.sink = self.device = None
        if device.isNull():
            log("No audio output device; playing without sound")
            return
        if not device.isFormatSupported(audio_format):
            log(f"The audio device does not accept {sample_rate} Hz stereo 16-bit; playing without sound")
            return
        self.sink = QAudioSink(device, audio_format)
        self.sink.setBufferSize(sample_rate * 4 // 6)  # about 170 ms
        self.device = self.sink.start()

    def write(self, data: bytes):
        if self.device is not None and self.sink.bytesFree() >= len(data):
            self.device.write(data)

    def stop(self):
        if self.sink is not None:
            self.sink.stop()


class FrameView(QWidget):
    """Draws the newest frame letterboxed at the core's aspect ratio; forwards keys to the window."""

    def __init__(self, key_handler, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.image: QImage | None = None
        self.aspect = 4 / 3
        self.smooth = False
        self.text = "Starting…"
        self._keys = key_handler

    def target(self) -> QRectF:
        width, height = self.width(), self.height()
        if width / height > self.aspect:
            shown = height * self.aspect
            return QRectF((width - shown) / 2, 0, shown, height)
        shown = width / self.aspect
        return QRectF(0, (height - shown) / 2, width, shown)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if self.image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
            painter.drawImage(self.target(), self.image)
        if self.text:
            painter.setPen(QColor("#e0e0e0"))
            painter.drawText(self.rect().adjusted(12, 12, -12, -12),
                             Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, self.text)

    def keyPressEvent(self, event):
        if not self._keys(event, True):
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if not self._keys(event, False):
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._keys(None, False)  # release every held button so none sticks
        super().focusOutEvent(event)


class PlayWindow(QMainWindow):
    """One game session. Closing the window stops the core, writes the resume slot and the memory card."""

    slots_changed = Signal()
    log = Signal(str)
    closed = Signal()

    def __init__(self, config: PlayConfig, title: str = "Digimon World", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} — DW1-coop player")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(960, 760)
        self.config = config
        self.keys = dict(DEFAULT_KEYS)
        self._held = 0
        self.audio: AudioOutput | None = None
        self.view = FrameView(self._handle_key)
        self.setCentralWidget(self.view)
        self._build_toolbar()
        self.status = QLabel("Starting the core…"); self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(QLabel("F1: controls"))
        self.thread = QThread(self)
        self.worker = EmulatorWorker(config)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.frame_ready.connect(self._show_frame)
        self.worker.audio_ready.connect(self._play_audio)
        self.worker.message.connect(self._message)
        self.worker.started_ok.connect(self._started)
        self.worker.failed.connect(self._failed)
        self.worker.slot_saved.connect(self._slot_saved)
        self.worker.slot_loaded.connect(self._slot_loaded)
        self.worker.command_failed.connect(self._command_failed)
        # Direct: stop() blocks the GUI thread in wait(), so a queued quit would never be delivered.
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self._finished = False
        self.thread.finished.connect(self._thread_finished)

    def start(self):
        self.show(); self.view.setFocus()
        self.thread.start()

    def _build_toolbar(self):
        bar = QToolBar("Quick saves"); bar.setMovable(False); self.addToolBar(bar)
        self.slot = QComboBox(); self.slot.addItems([f"Slot {index}" for index in range(1, SLOT_COUNT + 1)])
        self.note = QLineEdit(); self.note.setPlaceholderText("Note for the next quick save, e.g. the zone")
        self.note.setMaximumWidth(320); self.note.returnPressed.connect(self.view.setFocus)

        def action(text, shortcut, handler, checkable=False):
            item = QAction(text, self); item.setShortcut(QKeySequence(shortcut))
            item.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            item.setCheckable(checkable); item.triggered.connect(handler); bar.addAction(item)
            return item

        bar.addWidget(self.slot)
        action("Save", "F5", lambda: self.worker.quick_save(self.current_slot, self.note.text()))
        action("Load", "F8", lambda: self.worker.quick_load(self.current_slot))
        previous = QAction(self); previous.setShortcut(QKeySequence("F6")); previous.triggered.connect(lambda: self._step_slot(-1))
        following = QAction(self); following.setShortcut(QKeySequence("F7")); following.triggered.connect(lambda: self._step_slot(1))
        self.addAction(previous); self.addAction(following)
        bar.addWidget(self.note); bar.addSeparator()
        self.pause = action("Pause", "P", self._toggle_pause, checkable=True)
        action("Reset", "Ctrl+R", self._reset)
        self.full = action("Full screen", "F11", self._toggle_full_screen, checkable=True)
        smooth = QCheckBox("Smooth"); smooth.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        smooth.toggled.connect(self._set_smooth); bar.addWidget(smooth)
        help_action = QAction(self); help_action.setShortcut(QKeySequence("F1")); help_action.triggered.connect(self._show_help)
        self.addAction(help_action)
        for widget in (self.slot,):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    @property
    def current_slot(self) -> int:
        return self.slot.currentIndex() + 1

    def _step_slot(self, step: int):
        self.slot.setCurrentIndex((self.slot.currentIndex() + step) % SLOT_COUNT)
        info = self.worker.store.info(self.current_slot)
        self.status.setText(f"Slot {self.current_slot}: " + (f"{info.created}{f' — {info.note}' if info.note else ''}"
                                                             if info else "empty"))

    def _handle_key(self, event, pressed: bool) -> bool:
        if event is None:
            self._held = 0; self.worker.buttons = 0; self.worker.fast_forward = False
            return True
        if event.isAutoRepeat():
            return event.key() in self.keys or event.key() == Qt.Key.Key_Tab
        if event.key() == Qt.Key.Key_Tab:
            self.worker.fast_forward = pressed
            return True
        button = self.keys.get(event.key())
        if button is None:
            return False
        bit = 1 << BUTTON[button]
        self._held = self._held | bit if pressed else self._held & ~bit
        self.worker.buttons = self._held
        return True

    def focusNextPrevChild(self, _next):
        return False  # Tab is the fast-forward key, not focus navigation

    def _toggle_pause(self, paused: bool):
        self.worker.paused = paused
        self.view.text = "Paused" if paused else ""; self.view.update()

    def _reset(self):
        if QMessageBox.question(self, "Reset console", "Reset the console? Unsaved progress is lost.") \
                == QMessageBox.StandardButton.Yes:
            self.worker.reset()
        self.view.setFocus()

    def _toggle_full_screen(self, full: bool):
        self.showFullScreen() if full else self.showNormal()

    def _set_smooth(self, smooth: bool):
        self.view.smooth = smooth; self.view.update(); self.view.setFocus()

    def _show_help(self):
        QMessageBox.information(self, "Controls", KEY_HELP.replace("  •  ", "\n"))

    @Slot()
    def _show_frame(self):
        image = self.worker.take_frame()
        if image is not None:
            self.view.image = image; self.view.update()

    @Slot(bytes)
    def _play_audio(self, data: bytes):
        if self.audio is not None:
            self.audio.write(data)

    @Slot(str)
    def _message(self, text: str):
        self.status.setText(text); self.log.emit(text)

    @Slot(object)
    def _started(self, info):
        self.view.aspect, self.view.text = info["aspect"], ""
        self.audio = AudioOutput(info["sample_rate"], self._message)
        self._message(f"Running {info['core']} {info['version']} at {info['fps']:.3f} fps — F1 shows the controls")

    @Slot(str)
    def _failed(self, message: str):
        self.log.emit(f"Failed: {message}")
        self.view.text = message; self.view.update()
        QMessageBox.critical(self, "The game could not run", message)

    @Slot(object)
    def _slot_saved(self, info):
        self.slots_changed.emit()
        if info.slot != RESUME_SLOT:
            self.note.clear()

    @Slot(int)
    def _slot_loaded(self, _slot):
        self.view.setFocus()

    @Slot(str)
    def _command_failed(self, message: str):
        self._message(message)
        QMessageBox.warning(self, "Quick save", message)
        self.view.setFocus()

    @Slot()
    def _thread_finished(self):
        self._finished = True
        if self.audio is not None:
            self.audio.stop()
        self.slots_changed.emit()

    def stop(self, wait_ms: int = 10000) -> bool:
        """Stop the core and wait for its shutdown saves; True once the worker thread has ended."""
        if not self.thread.isRunning():
            return True
        self.worker.request_stop()
        return self.thread.wait(wait_ms)

    def closeEvent(self, event):
        if not self.stop():
            self._message("The core is still shutting down…")
            event.ignore()
            return
        # Deliver the worker's last queued messages (resume save, memory card) before the window is deleted.
        QCoreApplication.sendPostedEvents(self)
        self.closed.emit()
        event.accept()
