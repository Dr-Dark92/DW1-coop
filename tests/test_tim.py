import json
import struct

import pytest

from dw1_workbench.errors import DiscFormatError
from dw1_workbench.tim import decode_tim, export_png


def make_4bit_tim():
    palette = [0x0000, 0x001F, 0x83E0] + [0] * 13
    clut = struct.pack("<IHHHH", 12 + 32, 0, 0, 16, 1) + struct.pack("<16H", *palette)
    pixels = bytes((0x21, 0x10))  # red, green(STP), transparent, red
    image = struct.pack("<IHHHH", 14, 0, 0, 1, 1) + pixels
    return struct.pack("<II", 0x10, 8) + clut + image


def test_known_tim_pixels_and_export(tmp_path):
    tim = decode_tim(make_4bit_tim())
    assert tim.image.size == (4, 1)
    assert list(tim.image.get_flattened_data()) == [(255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 0, 0), (255, 0, 0, 255)]
    png, metadata = export_png(tim, tmp_path / "texture.png", "disc:abc", "deadbeef",
                               provenance={"original_path": "CHDAT/ALLTIM.TIM#003", "offset": 0xD800})
    assert png.is_file()
    assert json.loads(metadata.read_text())["source_resource_id"] == "disc:abc"
    assert json.loads(metadata.read_text())["provenance"]["offset"] == 0xD800


def test_malformed_tim_rejected():
    with pytest.raises(DiscFormatError): decode_tim(make_4bit_tim()[:-1])
