from __future__ import annotations

import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench.mmd import parse_mmd_static, parse_usa_skeleton
from dw1_workbench.model_view import StaticModelView
from test_mmd import make_executable, make_mmd

RED, BLUE = QColor(255, 0, 0), QColor(0, 0, 255)


@pytest.fixture(scope="module")
def application():
    yield QApplication.instance() or QApplication([])


def model_with_uvs(uvs):
    data = bytearray(make_mmd())
    primitive = 20 + 28
    for index, (u, v) in enumerate(uvs):
        struct.pack_into("2B", data, primitive + 4 + index * 4, u, v)
    return parse_mmd_static(bytes(data), parse_usa_skeleton(make_executable(), 3))


def render(model):
    palette = QImage(16, 16, QImage.Format.Format_ARGB32)
    palette.fill(BLUE)
    for v in range(16):
        palette.setPixelColor(3, v, RED)
    view = StaticModelView()
    view.resize(240, 240)
    view.set_model(model, (palette,), 488)
    image = view.grab().toImage()
    colours = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())]
    return sum(c == RED for c in colours), sum(c == BLUE for c in colours)


@pytest.mark.parametrize("uvs", [((3, 3), (3, 3), (3, 3)), ((3, 1), (3, 9), (3, 5))],
                         ids=["single-texel", "texel-column"])
def test_zero_area_uv_face_is_filled_from_its_texels(application, uvs):
    red, blue = render(model_with_uvs(uvs))
    assert red > 500 and blue == 0
