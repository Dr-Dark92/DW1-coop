"""Interactive, disposable fixed-90 trial from the recorded user baseline. Not the normal player launcher."""
import os
from pathlib import Path
import shutil
import sys
import tempfile


def main():
    root = Path("/home/drdark/dev/DW1-coop-data/fixed-framing-live")
    root.mkdir(parents=True, exist_ok=True)
    trial = Path(tempfile.mkdtemp(prefix="trial-", dir=root))
    os.environ["DW1_COOP_HOME"] = str(trial / "home")
    os.environ["DW1_COOP_TELEMETRY"] = str(trial / "telemetry")
    from PySide6.QtWidgets import QApplication
    from dw1_workbench.fixed_framing import FixedFramingProof
    from dw1_workbench.libretro import BUTTON
    from dw1_workbench.player import PlayConfig, PlayWindow
    from dw1_workbench.shared_data import app_settings

    app = QApplication([])
    inspection = "20261105-053603-355650"
    source = Path("/home/drdark/.dw1-coop/play/06084490ea0db340/inspections") / inspection
    store = trial / "store"
    shutil.copytree(source, store / "inspections" / inspection)
    app_settings().setValue("play/screen", "4:3")
    app_settings().setValue("play/hd_backgrounds", False)
    config = PlayConfig(
        Path("/home/drdark/dev/DW1-coop-data/core-8mb/mednafen_psx_8mb_libretro.so"),
        Path("/home/drdark/Downloads"),
        Path(sys.argv[1]),  # the patched disc in launch.log: its name changes with your imports
        store, launch_inspection=inspection, resume_on_close=False, live_ram=True,
        options={"beetle_psx_internal_resolution": "2x", "beetle_psx_dither_mode": "disabled"})
    window = PlayWindow(config, "EXPERIMENT: fixed 90% — walking only, no persistent saves or HD")
    worker = window.worker
    worker._start_avatars = lambda: None
    original_loop = worker._loop

    def loop(core):
        proof = FixedFramingProof(core)
        proof.install()
        native_run = core.run_frame

        def run():
            try:
                proof.tick()
                if core.buttons & ((1 << BUTTON["Start"]) | (1 << BUTTON["Y"])):
                    raise ValueError("menus are not supported by this walking-only trial")
                result = native_run()
                proof.tick()
                return result
            except ValueError as exc:
                worker.message.emit(f"EXPERIMENT STOPPED: {exc}. Close this trial window.")
                core.shutdown_requested = True
                return None, b""

        core.run_frame = run
        original_loop(core)

    worker._loop = loop
    blocked = lambda *args: worker.message.emit("Save/load/reset/inspection disabled in this disposable trial.")
    worker.quick_save = worker.quick_load = worker.reset = worker.inspect = blocked
    window.closed.connect(app.quit)
    window.start()
    app.processEvents()
    window.set_game_view_size()
    print(f"Disposable trial data: {trial}", flush=True)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
