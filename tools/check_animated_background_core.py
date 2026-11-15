"""Compare a user WebP on a copied File City save, with private overrides/cards and bounded telemetry."""
import argparse
import hashlib
import json
from pathlib import Path
import threading
import time

import numpy as np
from PIL import Image

from dw1_workbench.background_animation import AnimatedFrame, webp_animation
from dw1_workbench.hd_background import BackgroundLayer, Compositor, HdPictures
from dw1_workbench.libretro import LibretroCore
from dw1_workbench.live import CURRENT_SCREEN, ram_offset, set_ram_size
from dw1_workbench.quicksave import QuickSaveStore
from dw1_workbench.telemetry import Telemetry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("core", "bios", "disc", "saves", "image", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--slot", type=int, default=1)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]): parser.error("External output required")
    args.output.mkdir(parents=True, exist_ok=False)
    from PySide6.QtGui import QImage
    from dw1_workbench.player import frame_image
    data = args.image.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    animation = webp_animation(data)
    if animation is None: parser.error("Input must be animated WebP")
    private = HdPictures(args.output / "hd")
    private.store(204, "TWNA01", data)
    baseline_dir = args.output / "baseline"; baseline_dir.mkdir()
    with Image.open(args.image) as image: image.convert("RGB").save(baseline_dir / "204-TWNA01.png")
    state = QuickSaveStore(args.saves).load(args.slot)
    core = LibretroCore(args.core, args.bios, args.output / "private-cards")
    report = dict(source_sha256=digest, frames=len(animation.durations), duration_ms=sum(animation.durations), loops=animation.loops)
    try:
        core.start(args.disc); core.run_frame(); set_ram_size(core.ram_size)
        for mode, source, count in (("static", HdPictures(baseline_dir), 180), ("animated", private, 600)):
            core.unserialize(state)
            if core.read_memory(ram_offset(CURRENT_SCREEN), 1)[0] != 204:
                raise ValueError("Selected save is not File City map204")
            clock = [0.0]
            telemetry = Telemetry(True)
            errors, samples, output = [], [], []
            lock = threading.Lock()
            class Layer(BackgroundLayer):
                def compose(self, job):
                    result = super().compose(job)
                    picture = job.payload[4][0]
                    with lock:
                        if len(samples) < 2000:
                            samples.append((picture.index if isinstance(picture, AnimatedFrame) else -1, result.coverage))
                    return result
            layer = Layer(core.read_memory, core.serialize, source.path, telemetry=telemetry,
                          on_error=errors.append, clock=lambda: clock[0])
            def deliver(composed):
                with lock: output[:] = [composed.pixels]
            compositor = Compositor(layer, deliver, errors.append)
            durations = []
            deadline = time.perf_counter()
            start = deadline
            try:
                for number in range(count):
                    t = time.perf_counter(); frame, _ = core.run_frame(); durations.append((time.perf_counter() - t) * 1000)
                    clock[0] += 1 / core.fps
                    if frame is not None:
                        image = frame_image(frame).convertToFormat(QImage.Format.Format_RGB32)
                        pixels = np.frombuffer(image.constBits(), np.uint32).reshape(image.height(), image.bytesPerLine() // 4)[:, :image.width()].copy() & 0xFFFFFF
                        job = layer.prepare(pixels)
                        if job is not None: compositor.submit(job)
                    if number in (count // 3, count * 2 // 3, count - 1):
                        with lock: pixels = output[0].copy() if output else None
                        if pixels is not None:
                            Image.fromarray(np.stack(((pixels >> 16) & 255, (pixels >> 8) & 255, pixels & 255), axis=-1).astype(np.uint8)).save(args.output / f"{mode}-{number}.png")
                    deadline += 1 / core.fps
                    delay = deadline - time.perf_counter()
                    if delay > 0: time.sleep(delay)
                elapsed = time.perf_counter() - start
            finally:
                compositor.stop(); layer.close()
            with lock: captured = list(samples)
            indices = [s[0] for s in captured if s[0] >= 0]
            report[mode] = dict(elapsed_seconds=round(elapsed, 3), core_frames=count,
                                core_frame_ms_p50=round(float(np.percentile(durations, 50)), 3),
                                core_frame_ms_p95=round(float(np.percentile(durations, 95)), 3),
                                composed=len(captured), distinct_animation_frames=len(set(indices)),
                                loop_wraps=sum(a > b for a, b in zip(indices, indices[1:])),
                                frame_samples=indices[::max(1, len(indices) // 40)], errors=errors,
                                telemetry=telemetry.snapshot())
            print(mode, {key: value for key, value in report[mode].items() if key != "telemetry"}, flush=True)
            (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        report["source_unchanged"] = hashlib.sha256(args.image.read_bytes()).hexdigest() == digest
        (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    finally: core.stop()


if __name__ == "__main__":
    main()
