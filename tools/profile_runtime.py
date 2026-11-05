"""Read-only real-core diagnostic; explicit inputs, disposable save storage, no audio output.

This measures the core and HD pipeline without pacing/Qt presentation. It is not a gameplay acceptance test.
Use --baseline to compare the f51a7c5 HD implementation with the same core/instrumentation harness.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench import hd_background as current
from dw1_workbench.avatars import load_avatars, load_avatars_isolated
from dw1_workbench.inspection import InspectionStore
from dw1_workbench.libretro import LibretroCore, BUTTON
from dw1_workbench.live import CURRENT_SCREEN, ram_offset, set_ram_size
from dw1_workbench.player import frame_image
from dw1_workbench.quicksave import QuickSaveStore
from dw1_workbench.telemetry import Telemetry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "bios", "disc", "store", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--slot", type=int)
    source.add_argument("--inspection")
    parser.add_argument("--hd", type=Path)
    parser.add_argument("--resolution", choices=("1x", "2x", "4x"), default="1x")
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--avatars", choices=("off", "thread", "process"), default="off")
    parser.add_argument("--press", choices=("B", "Up", "Down", "Left", "Right"))
    parser.add_argument("--captures", action="store_true", help="write proprietary final-frame PNGs under output only")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("diagnostic output must be outside the source checkout")
    if not 1 <= args.frames <= 36000:
        parser.error("frames must be 1–36000")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    module = current
    if args.baseline:
        code = subprocess.check_output(["git", "show", "f51a7c56fa0d53dd78a64c0893bb9ed2e487709d:src/dw1_workbench/hd_background.py"])
        name = "dw1_workbench._baseline_background"
        spec = importlib.util.spec_from_loader(name, loader=None)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(compile(code, name, "exec"), module.__dict__)
    # Read only these requested save files. The emulator never sees the user's save directory.
    if args.slot is not None:
        state = QuickSaveStore(args.store).load(args.slot, core="Beetle PSX")
    else:
        state = InspectionStore(args.store / "inspections").state(args.inspection)
    telemetry = Telemetry(True, capacity=4096)
    args.output.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    avatar_thread = None
    errors, maps, coverage, frame_hashes = [], set(), [], []
    avatar_count = [0]
    with tempfile.TemporaryDirectory(prefix="runtime-", dir=args.output) as disposable:
        core = LibretroCore(args.core, args.bios, Path(disposable),
                            {"beetle_psx_internal_resolution": args.resolution,
                             "beetle_psx_dither_mode": "disabled"}, telemetry=telemetry)
        layer = None
        try:
            core.start(args.disc)
            set_ram_size(core.ram_size)
            for _ in range(8):
                core.run_frame()
            core.unserialize(state)
            if args.hd:
                layer = module.BackgroundLayer(core.read_memory, core.serialize, module.HdPictures(args.hd).path)
                if not args.baseline:
                    layer.telemetry = telemetry
                    layer.picture_cache.telemetry = telemetry
            if args.avatars != "off":
                def avatars():
                    interval = sys.getswitchinterval()
                    try:
                        if args.avatars == "thread":
                            sys.setswitchinterval(0.0002)  # previous player's exact mitigation
                        method = load_avatars if args.avatars == "thread" else load_avatars_isolated
                        with telemetry.measure("avatars.batch"):
                            method(args.disc, Path(disposable) / "avatars", [3, 5, 6, 9, 12, 14],
                                   lambda *unused: avatar_count.__setitem__(0, avatar_count[0] + 1), stop.is_set)
                    except Exception as exc:
                        errors.append(str(exc))
                    finally:
                        if args.avatars == "thread":
                            sys.setswitchinterval(interval)
                avatar_thread = threading.Thread(target=avatars)
                avatar_thread.start()
            previous, repeats, composed = None, 0, None
            started = time.monotonic()
            for number in range(args.frames):
                begin = time.monotonic_ns()
                core.buttons = (1 << BUTTON[args.press]) if args.press and (args.press != "B" or number % 40 < 3) else 0
                frame, _audio = core.run_frame()
                map_index = core.read_memory(ram_offset(CURRENT_SCREEN), 1)[0]
                game_state = core.read_memory(ram_offset(current.GAME_STATE), 1)[0]
                transition = int.from_bytes(core.read_memory(ram_offset(current.SCRIPT_MAP_CHANGE_STATE), 2), "little")
                telemetry.observe_scene(map_index, game_state, transition)
                maps.add(map_index)
                if frame is not None:
                    repeat = frame.data == previous and repeats < 15
                    previous, repeats = frame.data, repeats + 1 if repeat else 0
                    if not repeat:
                        with telemetry.measure("qt.frame_conversion"):
                            image = frame_image(frame).convertToFormat(QImage.Format.Format_RGB32)
                            pixels = np.frombuffer(image.constBits(), np.uint32).reshape(image.height(), image.bytesPerLine() // 4)
                            pixels = pixels[:, :image.width()] & 0xFFFFFF
                        if layer is not None:
                            with telemetry.measure("harness.prepare"):
                                job = layer.prepare(pixels)
                            if job is not None:
                                with telemetry.measure("harness.compose"):
                                    composed = layer.compose(job)
                                coverage.append(composed.coverage)
                        if number % 120 == 0:
                            frame_hashes.append(dict(frame=number, sha256=hashlib.sha256(frame.data).hexdigest()))
                telemetry.duration("frame.work", time.monotonic_ns() - begin)
                if time.monotonic_ns() - begin > 1e9 / core.fps:
                    telemetry.count("frame.over_budget")
                if number % 120 == 0:
                    telemetry.memory()
                application.processEvents()
            elapsed = time.monotonic() - started
            if args.captures:
                capture = args.output / f"capture-{time.monotonic_ns()}"
                image.save(str(capture) + "-native.png")
                if composed is not None:
                    from PIL import Image
                    pixels = composed.pixels
                    Image.fromarray(np.stack(((pixels >> 16) & 255, (pixels >> 8) & 255, pixels & 255), -1)
                                    .astype(np.uint8)).save(str(capture) + "-hd.png")
            telemetry.event("result", frames=args.frames, unpaced_fps=args.frames / elapsed, maps=sorted(maps),
                            coverage_samples=len(coverage), median_coverage=float(np.median(coverage)) if coverage else None,
                            avatars=avatar_count[0], errors=errors, native_frame_hashes=frame_hashes,
                            core=core.info.name, version=core.info.version, resolution=args.resolution,
                            state_sha256=hashlib.sha256(state).hexdigest(), baseline=args.baseline,
                            final_ram_sha256=hashlib.sha256(core.read_memory(0, core.ram_size)).hexdigest())
        finally:
            stop.set()
            if avatar_thread is not None:
                avatar_thread.join(5)
            if layer is not None and hasattr(layer, "close"):
                layer.close()
            core.stop()
    report = telemetry.write(args.output)
    print(report)
    print(json.dumps(telemetry.snapshot()["metrics"], indent=2))


if __name__ == "__main__":
    main()
