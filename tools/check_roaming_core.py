"""Isolated controlled hour-boundary check on a user-supplied inspection; never loads user memory cards."""
import argparse
import json
import os
from pathlib import Path
import struct

from dw1_workbench.inspection import InspectionStore
from dw1_workbench.libretro import LibretroCore
from dw1_workbench.live import ram_offset, set_ram_size
from dw1_workbench.lifespan import LifespanPause
from dw1_workbench.progression import HOUR, LIFETIME, PARTNER_LIVES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "bios", "disc", "inspections", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--inspection", required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("Output must be outside the checkout")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from dw1_workbench.player import frame_image
    from dw1_workbench.hud_view import HudPainter, read_hud
    from dw1_workbench.gameplay_options import RoamingOptions
    app = QApplication([])
    state = InspectionStore(args.inspections).state(args.inspection)
    core = LibretroCore(args.core, args.bios, args.output / "private-core-saves")
    results = {}
    try:
        core.start(args.disc)
        core.run_frame()
        set_ram_size(core.ram_size)
        def put(address, value):
            core.write_memory(ram_offset(address), struct.pack("<h", value))
        def short(address):
            return struct.unpack("<h", core.read_memory(ram_offset(address), 2))[0]
        for name in ("native", "paused"):
            core.unserialize(state)
            put(LIFETIME, 5); put(HOUR, 12); put(HOUR + 2, 59)
            put(0x80134F08, 18)  # CURRENT_FRAME; next minute near a verified hourly boundary
            lives = core.read_memory(ram_offset(PARTNER_LIVES), 1)[0]
            guard = LifespanPause(core.read_memory, core.write_memory)
            frame = None
            for _ in range(120):
                snapshot = guard.before_frame() if name == "paused" else None
                current, _ = core.run_frame()
                if name == "paused": guard.after_frame(snapshot)
                if current is not None: frame = current
            hud = read_hud(core.read_memory, None)
            results[name] = dict(hour=short(HOUR), minute=short(HOUR + 2), lifetime=short(LIFETIME),
                                 lives_before=lives, lives_after=core.read_memory(ram_offset(PARTNER_LIVES), 1)[0],
                                 care=hud.care if hud is not None else None)
            if frame is not None:
                image = frame_image(frame).copy()
                if hud is not None:
                    painter = HudPainter(core.serialize); painter.prepare()
                    painter.draw(image, hud, RoamingOptions(True, True, True, True, True))
                image.save(str(args.output / f"{name}.png"))
        results["controlled_check_passed"] = (results["native"]["hour"] == 13
            and results["native"]["lifetime"] == 4 and results["paused"]["hour"] == 13
            and results["paused"]["lifetime"] == 5
            and all(results[n]["lives_before"] == results[n]["lives_after"] for n in ("native", "paused")))
        (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results), flush=True)
    finally:
        core.stop()
    assert app is not None


if __name__ == "__main__":
    main()
