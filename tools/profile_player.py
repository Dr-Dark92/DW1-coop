"""Real Qt/worker smoke test using ONLY disposable copies of requested save slots or an inspection.

No audio output; offscreen paint timings are not display/vsync latency. Never auto-migrate settings or saves.
"""
import argparse
import os
from pathlib import Path
import shutil
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "bios", "disc", "store", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--slot", type=int)
    source.add_argument("--inspection")
    parser.add_argument("--hd", type=Path)
    parser.add_argument("--resolution", choices=("1x", "2x", "4x"), default="2x")
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--avatars", action="store_true")
    parser.add_argument("--roundtrip", action="store_true", help="save/load disposable slot 9 halfway through")
    parser.add_argument("--toggle-hd", action="store_true")
    parser.add_argument("--view-640", action="store_true", help="explicit 4:3, 640x480 game presentation area")
    parser.add_argument("--capture", action="store_true", help="save a proprietary view screenshot outside Git")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("diagnostic output must be outside the source checkout")
    if not 3 <= args.seconds <= 120:
        parser.error("seconds must be 3–120")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="player-", dir=args.output) as scratch:
        scratch = Path(scratch)
        os.environ["DW1_COOP_HOME"] = str(scratch / "home")
        os.environ["DW1_COOP_TELEMETRY"] = str(args.output)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from dw1_workbench import player
        from dw1_workbench.shared_data import app_settings

        class SilentAudio:
            def __init__(self, *args): pass
            def write(self, data): pass
            def stop(self): pass

        player.AudioOutput = SilentAudio
        app = QApplication.instance() or QApplication([])
        store = scratch / "store"
        store.mkdir()
        if args.slot is not None:
            for suffix in ("state", "json", "png", "progress.json"):
                source = args.store / f"slot{args.slot}.{suffix}"
                if source.exists():
                    shutil.copy2(source, store / source.name)
        else:
            shutil.copytree(args.store / "inspections" / args.inspection, store / "inspections" / args.inspection)
        app_settings().setValue("play/hd_backgrounds", bool(args.hd))
        config = player.PlayConfig(args.core, args.bios, args.disc, store, launch_slot=args.slot,
                                   launch_inspection=args.inspection, resume_on_close=False, live_ram=True,
                                   hd_pictures=args.hd, options={"beetle_psx_internal_resolution": args.resolution,
                                                               "beetle_psx_dither_mode": "disabled"})
        window = player.PlayWindow(config, "P0 disposable diagnostic")
        worker = window.worker
        # An unattended diagnostic must report a failure, not block forever in a modal error dialog.
        worker.failed.disconnect(window._failed)
        worker.command_failed.disconnect(window._command_failed)
        if not args.avatars:
            worker._start_avatars = lambda: None
        worker.failed.connect(lambda text: print("FAILED:", text))
        worker.command_failed.connect(lambda text: print("COMMAND FAILED:", text))
        saved = []
        worker.slot_saved.connect(lambda info: saved.append(info.slot))
        window.start()
        if args.view_640:
            app.processEvents()
            window.set_game_view_size()
        start = time.monotonic()
        requested_save = requested_load = toggled = False
        try:
            while time.monotonic() - start < args.seconds and window.thread.isRunning():
                app.processEvents()
                elapsed = time.monotonic() - start
                if args.roundtrip and not requested_save and elapsed > args.seconds / 3:
                    worker.quick_save(9, "P0 diagnostic: disposable copy only")
                    requested_save = True
                if requested_save and 9 in saved and not requested_load:
                    worker.quick_load(9)
                    requested_load = True
                if args.toggle_hd and not toggled and elapsed > args.seconds * 0.65:
                    worker.hd_enabled = False
                    toggled = True
                if toggled and elapsed > args.seconds * 0.8:
                    worker.hd_enabled = True
                time.sleep(0.002)
            worker.telemetry.event("qt.smoke", save_requested=requested_save, load_requested=requested_load,
                                   saved_slots=saved, hd_toggled=toggled, screen=window.view.screen,
                                   core_aspect=window.view.aspect,
                                   view_size=[window.view.width(), window.view.height()],
                                   game_size=[window.view.target().width(), window.view.target().height()])
            if args.capture:
                window.view.grab().save(str(args.output / f"view-{time.monotonic_ns()}.png"))
        finally:
            window.close()
            app.processEvents()
        print("Closed disposable player; reports:", args.output)


if __name__ == "__main__":
    main()
