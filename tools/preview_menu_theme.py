"""Render real, read-only user inspection/Dex data for UI review; outputs must remain outside Git."""
import argparse
import os
from pathlib import Path
import struct
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dex", "inspections", "avatars", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--inspection", required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("preview output must be outside the checkout")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    from PySide6.QtCore import Qt
    from dw1_workbench.digidex import DigiDex, read_moves
    from dw1_workbench.digidex_view import DigiDexDialog
    from dw1_workbench.evolution import EvolutionMenu
    from dw1_workbench.game_menu_view import evolution_menu_image, native_icons
    from dw1_workbench.inspection import InspectionStore
    from dw1_workbench.menu_icons import NAMES
    app = QApplication([])
    state = InspectionStore(args.inspections).state(args.inspection)
    rows = ("Items", "Digimon", "Player", "Praise", "Scold", "Sleep", *NAMES)
    frame = QImage(640, 480, QImage.Format.Format_RGB32); frame.fill(Qt.GlobalColor.black)
    menu = EvolutionMenu("Menu", rows, 0, "Cross: select   Circle/Triangle: close", layout="grid")
    evolution_menu_image(frame, menu, native_icons(SimpleNamespace(serialize=lambda: state))).save(str(args.output / "menu.png"))
    label = b"MainRAM.data8"
    ram = None
    for size in (0x800000, 0x200000):
        marker = bytes([len(label)]) + label + struct.pack("<I", size)
        if state.count(marker) == 1:
            start = state.index(marker) + len(marker)
            if start + size <= len(state): ram = memoryview(state)[start:start + size]
    if ram is None:
        raise ValueError("No verified complete MainRAM field in the supplied inspection")
    dex = DigiDex(args.dex)
    dex.moves = read_moves(lambda offset, size: bytes(ram[offset:offset + size]))
    avatars = {}
    for key in dex.entries:
        paths = sorted(args.avatars.glob(f"{int(key):03d}-*.png"))
        if paths: avatars[int(key)] = QImage(str(paths[0]))
    dialog = DigiDexDialog(dex.snapshot(), avatars=avatars)
    dialog.discovered_only.setChecked(True)
    dialog.show(); app.processEvents()
    for index, name in enumerate(dialog.sections):
        dialog.tabs.setCurrentIndex(index)
        app.processEvents()
        dialog.grab().save(str(args.output / (name.lower().replace(" ", "-") + ".png")))
    dialog.close()
    print(args.output)


if __name__ == "__main__":
    main()
