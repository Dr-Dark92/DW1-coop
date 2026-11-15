"""Replay a private captured Jijimon question through all native starter initializations."""
import argparse
import json
import os
from pathlib import Path
import struct
import zlib

from dw1_workbench.front_menu import FrontMenu, SettingsMenu, TIMES
from dw1_workbench.gameplay_options import RoamingOptions
from dw1_workbench.evolution import bit
from dw1_workbench.libretro import LibretroCore
from dw1_workbench.live import set_ram_size, ram_offset
from dw1_workbench.progression import PARTNER_ENTITY, PARTNER_STATS, LIFETIME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("core", "bios", "disc", "question-state", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]): parser.error("External output required")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from dw1_workbench.player import frame_image
    from dw1_workbench.game_menu_view import evolution_menu_image
    app = QApplication([])
    state = zlib.decompress(args.question_state.read_bytes())
    core = LibretroCore(args.core, args.bios, args.output / "private-cards")
    report = []
    front = None
    try:
        core.start(args.disc); core.run_frame(); set_ram_size(core.ram_size)
        for index, (time, kind) in enumerate(TIMES):
            if index < args.start: continue
            core.unserialize(state)
            front = FrontMenu(core, SettingsMenu(lambda: (RoamingOptions(), True, False), lambda *args: None))
            front.question = True; front.wait_stage = 6; front.time_index = index
            core.buttons = front.before_frame(0)
            frame, _ = core.run_frame()
            if frame is not None:
                evolution_menu_image(frame_image(frame), front.menu).save(str(args.output / f"{index}-question.png"))
            core.buttons = front.before_frame(bit("B")); core.run_frame()
            initialized = False
            observed = []
            last_stage = None
            for count in range(12000):
                # Cross advances dialogue/names; Start selects OK in the native naming UI and skips the movie.
                buttons = bit("B") if count % 60 < 6 else bit("Start") if 30 <= count % 60 < 36 else 0
                core.buttons = front.before_frame(buttons)
                frame, _ = core.run_frame()
                native_stage = front.number(0x80134FFA, "<h")
                if native_stage != last_stage and len(observed) < 100:
                    observed.append([count, native_stage]); last_stage = native_stage
                partner = struct.unpack("<i", core.read_memory(ram_offset(PARTNER_ENTITY), 4))[0]
                current_stats = struct.unpack("<4h", core.read_memory(ram_offset(PARTNER_STATS + 16), 8))
                current_life = struct.unpack("<h", core.read_memory(ram_offset(LIFETIME), 2))[0]
                if front.starting is None and partner == kind and current_stats[0] > 0 and current_life > 0:
                    initialized = True
                    if frame is not None: frame_image(frame).save(str(args.output / f"{index}-initialized.png"))
                    break
            stats = struct.unpack("<4h", core.read_memory(ram_offset(PARTNER_STATS + 16), 8))
            lifetime = struct.unpack("<h", core.read_memory(ram_offset(LIFETIME), 2))[0]
            result = dict(time=time, expected=kind, actual=partner, initialized=initialized,
                          frames=count + 1, stats=stats, lifetime=lifetime, stages=observed,
                          equipped=list(core.read_memory(ram_offset(PARTNER_STATS + 12), 4)),
                          learned=int.from_bytes(core.read_memory(ram_offset(PARTNER_ENTITY + 88), 8), "little"))
            report.append(result)
            (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({k: v for k, v in result.items() if k != "stages"}), flush=True)
            front.restore()
    finally:
        if front is not None: front.restore()
        core.stop()
    assert app is not None


if __name__ == "__main__":
    main()
