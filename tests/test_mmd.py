import struct

import pytest

from dw1_workbench.errors import DiscFormatError
from dw1_workbench.mmd import (SkeletonNode, parse_mmd_static,
                               parse_usa_skeleton)


def make_executable():
    data = bytearray(0xA1000)
    data[:8] = b"PS-X EXE"
    resource_id = 3
    struct.pack_into("<I", data, 0x9CEB4 + resource_id * 52 + 20, 2)
    struct.pack_into("<I", data, 0x8CE60 + resource_id * 4, 0x80090000 + 0x1000)
    struct.pack_into("bbbb", data, 0x1000, -1, -1, 0, 0)
    return bytes(data)


def make_mmd(*, bad_vertex=False, mode=0x34):
    # one object, 3 vertices/normals, one textured Gouraud triangle
    object_base = 20
    primitive_top = 28
    vertex_top = primitive_top + 28
    normal_top = vertex_top + 24
    animation_offset = object_base + normal_top + 24
    data = bytearray(animation_offset + 64)
    struct.pack_into("<5I", data, 0, 8, animation_offset, 0x41, 0, 1)
    struct.pack_into("<6Ii", data, object_base, vertex_top, 3, normal_top, 3, primitive_top, 1, 0)
    primitive = object_base + primitive_top
    struct.pack_into("4B", data, primitive, 9, 6, 0, mode)
    struct.pack_into("<2BH2BH2BH", data, primitive + 4,
                     0, 0, (488 << 6), 8, 0, 5, 0, 8, 0)
    indices = [(0, 0), (1, 1), (2, 3 if bad_vertex else 2)]
    for index, pair in enumerate(indices):
        struct.pack_into("<HH", data, primitive + 16 + index * 4, *pair)
    for index, xyz in enumerate(((0, 0, 0), (10, 0, 0), (0, 10, 0))):
        struct.pack_into("<4h", data, object_base + vertex_top + index * 8, *xyz, 0)
        struct.pack_into("<4h", data, object_base + normal_top + index * 8, 0, 0, 4096, 0)
    # one animation pointer and a two-node initial pose
    struct.pack_into("<I", data, animation_offset, 4)
    struct.pack_into("<H6h", data, animation_offset + 4, 1, 0, 0, 0, 10, 0, 0)
    return bytes(data)


def test_usa_skeleton_and_static_triangle_parse():
    skeleton = parse_usa_skeleton(make_executable(), 3)
    model = parse_mmd_static(make_mmd(), skeleton)
    assert skeleton == (SkeletonNode(None, None), SkeletonNode(0, 0))
    assert (len(model.objects), model.vertex_count, model.primitive_count, model.triangle_count) == (1, 3, 1, 1)
    assert model.objects[0].triangles[0].vertex_indices == (2, 1, 0)
    assert model.pose[1].translation.x == 10


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"bad_vertex": True}, "out-of-range index"),
    ({"mode": 0x20}, "Unsupported primitive"),
])
def test_static_parser_rejects_invalid_or_unsupported_packets(kwargs, message):
    with pytest.raises(DiscFormatError, match=message):
        parse_mmd_static(make_mmd(**kwargs), parse_usa_skeleton(make_executable(), 3))


def test_skeleton_rejects_forward_parent_reference():
    data = bytearray(make_executable())
    data[0x1003] = 2
    with pytest.raises(DiscFormatError, match="parent index"):
        parse_usa_skeleton(bytes(data), 3)


def test_translucency_is_explicit_thumbnail_opt_in():
    skeleton = parse_usa_skeleton(make_executable(), 3)
    with pytest.raises(DiscFormatError, match="Unsupported primitive"):
        parse_mmd_static(make_mmd(mode=0x36), skeleton)
    model = parse_mmd_static(make_mmd(mode=0x36), skeleton, translucent_preview=True)
    assert model.triangle_count == 1
    assert model.objects[0].triangles[0].material.translucent
    assert any("blend modes are not emulated" in warning for warning in model.warnings)
    with pytest.raises(DiscFormatError, match="out-of-range index"):
        parse_mmd_static(make_mmd(mode=0x36, bad_vertex=True), skeleton,
                         translucent_preview=True)


def test_translucent_quad_validates_packet_and_indices():
    skeleton = parse_usa_skeleton(make_executable(), 3)
    data = bytearray(212)
    struct.pack_into("<5I", data, 0, 8, 148, 0x41, 0, 1)
    struct.pack_into("<6Ii", data, 20, 64, 4, 96, 4, 28, 1, 0)
    struct.pack_into("4B", data, 48, 12, 8, 0, 0x3e)
    struct.pack_into("<2BH2BH2BH2BH", data, 52,
                     0, 0, 488 << 6, 8, 0, 5, 0, 8, 0, 8, 8, 0)
    for index in range(4):
        struct.pack_into("<HH", data, 68 + index * 4, index, index)
        struct.pack_into("<4h", data, 84 + index * 8, index % 2 * 10, index // 2 * 10, 0, 0)
        struct.pack_into("<4h", data, 116 + index * 8, 0, 0, 4096, 0)
    struct.pack_into("<I", data, 148, 4)
    struct.pack_into("<H6h", data, 152, 1, 0, 0, 0, 10, 0, 0)
    model = parse_mmd_static(bytes(data), skeleton, translucent_preview=True)
    assert model.triangle_count == 2
    assert all(face.material.translucent for face in model.objects[0].triangles)
    data[49] = 7
    with pytest.raises(DiscFormatError):
        parse_mmd_static(bytes(data), skeleton, translucent_preview=True)
