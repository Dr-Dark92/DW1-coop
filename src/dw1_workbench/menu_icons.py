"""Original, code-authored 20px sprites for added actions; no copied/bundled game artwork.

Integer pixels, dark stepped silhouettes and small shaded palettes match the presentation scale of native icons.
"""
from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

NAMES = ("Evolution", "Digi-Dex", "Digi-Bank", "Split Screen", "LAN Co-op")
PALETTE = {
    "ink": "#101820", "shadow": "#303840", "metal": "#687878", "light": "#b8c8c0", "white": "#e8e8d0",
    "red": "#b84028", "red_light": "#f08048", "red_dark": "#682820",
    "gold": "#b88838", "gold_light": "#e8c878", "brown": "#784830", "wood": "#b07840",
    "green": "#488848", "green_light": "#98c878", "blue": "#386878", "blue_light": "#78b0b8",
}


@lru_cache(maxsize=5)
def menu_icon(name):
    if name not in NAMES:
        raise ValueError(f"No sprite for {name}")
    image = QImage(20, 20, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)

    def r(x, y, w, h, color):
        painter.fillRect(x, y, w, h, QColor(PALETTE[color]))

    def monitor(x, y, color):
        r(x, y, 9, 10, "ink"); r(x + 1, y + 1, 7, 7, "metal")
        r(x + 1, y + 1, 6, 1, "light"); r(x + 1, y + 2, 1, 5, "light")
        r(x + 2, y + 2, 5, 4, "shadow"); r(x + 2, y + 2, 4, 3, color)
        r(x + 2, y + 2, 2, 1, "white"); r(x + 3, y + 8, 3, 2, "shadow")
        r(x + 1, y + 10, 7, 1, "ink"); r(x + 2, y + 9, 5, 1, "metal")

    if name == "Digi-Dex":
        # Bound red reference book: stepped page edges, dark spine, cream paper and a clasp.
        r(4, 1, 12, 1, "ink"); r(2, 2, 15, 15, "ink"); r(4, 17, 12, 2, "ink")
        r(3, 3, 3, 13, "red_dark"); r(4, 3, 1, 12, "red_light")
        r(6, 2, 9, 14, "red"); r(6, 2, 8, 1, "red_light"); r(14, 3, 2, 13, "red_dark")
        r(6, 16, 9, 2, "gold"); r(7, 16, 8, 1, "white"); r(7, 18, 8, 1, "shadow")
        r(8, 5, 5, 1, "gold_light"); r(9, 7, 3, 4, "gold"); r(10, 8, 1, 2, "white")
        r(14, 11, 3, 3, "ink"); r(14, 11, 2, 2, "gold_light")
    elif name == "Digi-Bank":
        r(2, 5, 16, 12, "ink"); r(3, 3, 14, 3, "ink"); r(4, 2, 12, 1, "ink")
        r(3, 6, 14, 10, "brown"); r(4, 4, 12, 4, "wood"); r(5, 3, 10, 1, "gold_light")
        r(4, 9, 12, 1, "ink"); r(5, 11, 10, 1, "wood"); r(5, 14, 10, 1, "wood")
        for x in (4, 14):
            r(x, 5, 2, 11, "gold"); r(x, 5, 1, 10, "gold_light")
        r(8, 8, 4, 5, "ink"); r(9, 8, 2, 4, "gold_light"); r(9, 10, 1, 2, "shadow")
        r(3, 17, 3, 1, "shadow"); r(14, 17, 3, 1, "shadow")
    elif name == "Evolution":
        # Shaded hatching egg and a rising green arrow, not a modern flowchart glyph.
        r(3, 7, 9, 10, "ink"); r(5, 4, 5, 3, "ink"); r(4, 6, 7, 11, "gold")
        r(5, 5, 4, 10, "gold_light"); r(5, 7, 2, 4, "white"); r(9, 10, 2, 5, "brown")
        r(4, 12, 2, 2, "ink"); r(6, 13, 2, 2, "ink"); r(8, 12, 3, 2, "ink")
        r(3, 17, 9, 1, "shadow"); r(14, 2, 3, 13, "ink")
        r(12, 5, 7, 3, "ink"); r(13, 4, 5, 2, "ink"); r(15, 3, 1, 11, "green_light")
        r(13, 6, 5, 1, "green"); r(16, 7, 1, 7, "green")
    elif name == "Split Screen":
        monitor(0, 4, "green"); monitor(10, 4, "blue_light")
        r(8, 15, 4, 2, "ink"); r(9, 14, 2, 2, "gold")
    else:
        r(4, 10, 2, 7, "ink"); r(5, 15, 11, 2, "ink"); r(5, 16, 8, 1, "gold")
        monitor(1, 1, "green"); monitor(10, 7, "blue_light")
        r(6, 17, 3, 2, "metal"); r(7, 17, 1, 1, "white")
    painter.end()
    return image
