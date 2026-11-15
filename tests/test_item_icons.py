import runpy
import struct
from pathlib import Path

import pytest

from dw1_workbench.errors import DiscFormatError
from dw1_workbench.items import ITEM_PARA_OFFSET

module = runpy.run_path(str(Path(__file__).parents[1] / "tools/export_item_icons.py"))


def fixture():
    executable = bytearray(module["PALETTE_OFFSET"] + 128)
    executable[:8] = b"PS-X EXE"
    for index in range(128):
        name = f"Item{index}".encode()
        start = ITEM_PARA_OFFSET + index * 32
        executable[start:start + len(name)] = name
    executable[module["PALETTE_OFFSET"] + 1] = 1
    colors = [0, 31] + [0] * 14 + [0, 31 << 10] + [0] * 14 + [0] * (22 * 16)
    atlas = struct.pack("<II", 16, 8)
    atlas += struct.pack("<IHHHH", 12 + len(colors) * 2, 224, 488, 16, 24)
    atlas += struct.pack("<384H", *colors)
    atlas += struct.pack("<IHHHH", 12 + 16384, 320, 0, 64, 128)
    atlas += bytes([0x11]) * 16384
    return executable, atlas


def test_item_icons_use_item_specific_palettes_and_all_cells():
    executable, atlas = fixture()
    icons = list(module["decode_icons"](bytes(executable), atlas))
    assert len(icons) == 128
    assert icons[0][3].getpixel((0, 0)) == (255, 0, 0, 255)
    assert icons[1][3].getpixel((0, 0)) == (0, 0, 255, 255)
    assert icons[127][:3] == (127, "Item127", 0)
    assert all(icon.size == (16, 16) for _, _, _, icon in icons)


def test_item_icons_reject_invalid_palette_and_truncation():
    executable, atlas = fixture()
    executable[module["PALETTE_OFFSET"]] = 24
    with pytest.raises(DiscFormatError, match="palette index"):
        list(module["decode_icons"](bytes(executable), atlas))
    executable[module["PALETTE_OFFSET"]] = 0
    with pytest.raises(DiscFormatError):
        list(module["decode_icons"](bytes(executable), atlas[:-1]))
