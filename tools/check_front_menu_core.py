"""Isolated title-to-Jijimon check. Private memory card/output; original disc is read-only."""
import argparse
import json
import os
from pathlib import Path
import zlib

from dw1_workbench.front_menu import FrontMenu, SettingsMenu, CURRENT_MENU, TARGET_MENU, SCRIPT_STATE, TIMES
from dw1_workbench.gameplay_options import RoamingOptions
from dw1_workbench.evolution import bit
from dw1_workbench.libretro import LibretroCore
from dw1_workbench.live import set_ram_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "bios", "disc", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("Output must be outside checkout")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from dw1_workbench.player import frame_image
    from dw1_workbench.game_menu_view import evolution_menu_image
    app = QApplication([])
    core = LibretroCore(args.core, args.bios, args.output / "private-cards")
    settings = [RoamingOptions(), True, False]
    menu = SettingsMenu(lambda: tuple(settings), lambda *values: settings.__setitem__(slice(None), values))
    front = FrontMenu(core, menu)
    stage = 0
    trace = []
    previous = None
    try:
        core.start(args.disc); core.run_frame(); set_ram_size(core.ram_size)
        for index in range(18000):
            # Game dialogue polls at ~20Hz: a one-core-frame pulse can be missed entirely.
            buttons = bit("Start") if stage == 0 and index % 60 < 6 else bit("B") if stage >= 4 and index % 60 < 6 else 0
            if stage == 1: front.selection = 4; buttons = bit("B")
            if stage == 2: buttons = 0
            if stage == 3: buttons = bit("A")
            if stage == 4: front.selection = 0
            core.buttons = front.before_frame(buttons)
            frame, _ = core.run_frame()
            observed = (front.number(CURRENT_MENU), front.number(TARGET_MENU), front.number(SCRIPT_STATE, "<h"))
            if observed != previous and len(trace) < 256:
                trace.append((index, *observed)); previous = observed
            if stage == 0 and front.menu is not None and front.menu.title == "Digimon World":
                if frame is not None: evolution_menu_image(frame_image(frame), front.menu).save(str(args.output / "title.png"))
                stage = 1
            elif stage == 1 and front.menu is not None and front.menu.title == "Settings":
                if frame is not None: evolution_menu_image(frame_image(frame), front.menu).save(str(args.output / "settings.png"))
                stage = 2
            elif stage == 2: stage = 3
            elif stage == 3: stage = 4
            if front.question:
                if frame is not None: evolution_menu_image(frame_image(frame), front.menu).save(str(args.output / "jijimon.png"))
                (args.output / "question-state.zz").write_bytes(zlib.compress(core.serialize()))
                stage = 5
                break
        report = {"stage": stage, "frames": index + 1, "title_and_settings": stage >= 4,
                  "jijimon_question": stage == 5, "trace": trace}
        (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output / "last-state.zz").write_bytes(zlib.compress(core.serialize()))
        print(json.dumps({k: v for k, v in report.items() if k != "trace"}), flush=True)
    finally:
        front.restore(); core.stop()
    assert app is not None


if __name__ == "__main__":
    main()
