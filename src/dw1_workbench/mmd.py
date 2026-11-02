from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Callable

from .errors import Cancelled, DiscFormatError
from .tim import TimImage

DECODER_VERSION = "mmd-static-v1"
MAX_FILE_SIZE = 16 * 1024 * 1024
MAX_OBJECTS = 64
MAX_VERTICES = 100_000
MAX_PRIMITIVES = 100_000
USA_SKELETON_POINTER_TABLE = 0x8CE60
USA_LOAD_ADDRESS = 0x80090000
USA_PARAMETER_TABLE = 0x9CEB4
USA_PARAMETER_SIZE = 52


@dataclass(frozen=True)
class Vector3:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class TexCoord:
    u: int
    v: int


@dataclass(frozen=True)
class Material:
    texture_page: int
    color_mode: int
    blend_mode: int
    clut_x: int
    clut_y: int
    translucent: bool
    double_sided: bool


@dataclass(frozen=True)
class Triangle:
    vertex_indices: tuple[int, int, int]
    normal_indices: tuple[int, int, int]
    uvs: tuple[TexCoord, TexCoord, TexCoord]
    material: Material


@dataclass(frozen=True)
class ModelObject:
    vertices: tuple[Vector3, ...]
    normals: tuple[Vector3, ...]
    triangles: tuple[Triangle, ...]
    primitive_count: int
    scale_exponent: int


@dataclass(frozen=True)
class SkeletonNode:
    object_index: int | None
    parent_index: int | None


@dataclass(frozen=True)
class StaticPose:
    scale: Vector3
    rotation: Vector3
    translation: Vector3


@dataclass(frozen=True)
class StaticModel:
    objects: tuple[ModelObject, ...]
    skeleton: tuple[SkeletonNode, ...]
    pose: tuple[StaticPose, ...]
    animation_offset: int
    animation_index: int
    warnings: tuple[str, ...]

    @property
    def vertex_count(self) -> int:
        return sum(len(obj.vertices) for obj in self.objects)

    @property
    def primitive_count(self) -> int:
        return sum(obj.primitive_count for obj in self.objects)

    @property
    def triangle_count(self) -> int:
        return sum(len(obj.triangles) for obj in self.objects)


def _check_range(offset: int, size: int, limit: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > limit or size > limit - offset:
        raise DiscFormatError(f"{label} is outside its bounded section.")


def parse_usa_skeleton(executable: bytes, resource_id: int) -> tuple[SkeletonNode, ...]:
    if not executable.startswith(b"PS-X EXE") or not 0 <= resource_id < 180:
        raise DiscFormatError("A USA PS-X EXE and resource ID 0..179 are required.")
    parameter = USA_PARAMETER_TABLE + resource_id * USA_PARAMETER_SIZE
    pointer_at = USA_SKELETON_POINTER_TABLE + resource_id * 4
    _check_range(parameter + 20, 4, len(executable), "Bone count")
    _check_range(pointer_at, 4, len(executable), "Skeleton pointer")
    bone_count = struct.unpack_from("<I", executable, parameter + 20)[0]
    if not 1 <= bone_count <= MAX_OBJECTS:
        raise DiscFormatError(f"Skeleton bone count {bone_count} exceeds the safety limit.")
    address = struct.unpack_from("<I", executable, pointer_at)[0]
    offset = address - USA_LOAD_ADDRESS
    _check_range(offset, bone_count * 2, len(executable), "Skeleton table")
    nodes = []
    for index in range(bone_count):
        obj, parent = struct.unpack_from("bb", executable, offset + index * 2)
        if obj < -1 or obj >= MAX_OBJECTS:
            raise DiscFormatError(f"Skeleton node {index} has invalid object index {obj}.")
        if parent < -1 or parent >= index:
            raise DiscFormatError(f"Skeleton node {index} has invalid parent index {parent}.")
        nodes.append(SkeletonNode(None if obj == -1 else obj, None if parent == -1 else parent))
    return tuple(nodes)


def _parse_initial_pose(data: bytes, animation_offset: int, bone_count: int) -> tuple[tuple[StaticPose, ...], int]:
    _check_range(animation_offset, 4, len(data), "Animation pointer table")
    first_offset = struct.unpack_from("<I", data, animation_offset)[0]
    if first_offset < 4 or first_offset % 4:
        raise DiscFormatError("Animation pointer table has an invalid first offset.")
    animation_count = first_offset // 4
    if not 1 <= animation_count <= 1024:
        raise DiscFormatError("Animation pointer count exceeds the safety limit.")
    _check_range(animation_offset, animation_count * 4, len(data), "Animation pointer table")
    offsets = struct.unpack_from(f"<{animation_count}I", data, animation_offset)
    selected = next(((index, value) for index, value in enumerate(offsets) if value), None)
    if selected is None:
        raise DiscFormatError("MMD contains no static initial pose record.")
    animation_index, relative = selected
    start = animation_offset + relative
    _check_range(start, 2, len(data), "Initial pose")
    frame_word = struct.unpack_from("<H", data, start)[0]
    has_scale = bool(frame_word & 0x8000)
    stride = 18 if has_scale else 12
    _check_range(start + 2, (bone_count - 1) * stride, len(data), "Initial pose transforms")
    identity = StaticPose(Vector3(1.0, 1.0, 1.0), Vector3(0.0, 0.0, 0.0), Vector3(0.0, 0.0, 0.0))
    poses = [identity]
    cursor = start + 2
    for _ in range(1, bone_count):
        if has_scale:
            sx, sy, sz = struct.unpack_from("<3h", data, cursor)
            cursor += 6
            scale = Vector3(sx / 4096.0, sy / 4096.0, sz / 4096.0)
        else:
            scale = identity.scale
        rx, ry, rz, tx, ty, tz = struct.unpack_from("<6h", data, cursor)
        cursor += 12
        # PS1 angles use 4096 units per revolution. Coordinates remain source units.
        factor = math.tau / 4096.0
        poses.append(StaticPose(scale, Vector3(rx * factor, ry * factor, rz * factor),
                                Vector3(float(tx), float(ty), float(tz))))
    return tuple(poses), animation_index


def parse_mmd_static(data: bytes, skeleton: tuple[SkeletonNode, ...],
                     cancelled: Callable[[], bool] = lambda: False) -> StaticModel:
    if len(data) > MAX_FILE_SIZE:
        raise DiscFormatError("MMD exceeds the static-preview size limit.")
    if len(data) < 20:
        raise DiscFormatError("MMD header is truncated.")
    tmd_offset, animation_offset, tmd_id, flags, object_count = struct.unpack_from("<5I", data)
    if (tmd_offset, tmd_id, flags) != (8, 0x41, 0):
        raise DiscFormatError("Static preview supports relative-pointer MMD/TMD 0x41 containers only.")
    if not 1 <= object_count <= MAX_OBJECTS:
        raise DiscFormatError(f"MMD object count {object_count} exceeds the safety limit.")
    object_base = tmd_offset + 12
    _check_range(object_base, object_count * 28, animation_offset, "TMD object table")
    objects = []
    total_vertices = total_primitives = 0
    for object_index in range(object_count):
        if cancelled():
            raise Cancelled("Model parsing cancelled.")
        record = struct.unpack_from("<6Ii", data, object_base + object_index * 28)
        vertex_top, vertex_count, normal_top, normal_count, primitive_top, primitive_count, scale = record
        if scale != 0:
            raise DiscFormatError(f"Object {object_index} uses unsupported TMD scale exponent {scale}.")
        total_vertices += vertex_count
        total_primitives += primitive_count
        if total_vertices > MAX_VERTICES or total_primitives > MAX_PRIMITIVES:
            raise DiscFormatError("MMD geometry exceeds static-preview count limits.")
        vertex_offset, normal_offset, primitive_offset = (object_base + value for value in
                                                           (vertex_top, normal_top, primitive_top))
        _check_range(vertex_offset, vertex_count * 8, animation_offset, f"Object {object_index} vertices")
        _check_range(normal_offset, normal_count * 8, animation_offset, f"Object {object_index} normals")
        _check_range(primitive_offset, 0, animation_offset, f"Object {object_index} primitives")
        vertices = tuple(Vector3(float(x), float(y), float(z))
                         for x, y, z, _ in struct.iter_unpack("<4h", data[vertex_offset:vertex_offset + vertex_count * 8]))
        normals = tuple(Vector3(x / 4096.0, y / 4096.0, z / 4096.0)
                        for x, y, z, _ in struct.iter_unpack("<4h", data[normal_offset:normal_offset + normal_count * 8]))
        cursor = primitive_offset
        triangles = []
        for primitive_index in range(primitive_count):
            if cancelled():
                raise Cancelled("Model parsing cancelled.")
            _check_range(cursor, 4, animation_offset, f"Object {object_index} primitive header")
            output_words, input_words, primitive_flags, mode = struct.unpack_from("4B", data, cursor)
            packet_size = (input_words + 1) * 4
            _check_range(cursor, packet_size, animation_offset, f"Object {object_index} primitive packet")
            is_quad = mode == 0x3C
            if mode not in {0x34, 0x3C} or primitive_flags != 0:
                raise DiscFormatError(f"Unsupported primitive at object {object_index}, index {primitive_index}: "
                                      f"mode=0x{mode:02x}, flags=0x{primitive_flags:02x}.")
            expected = (12, 8, 36) if is_quad else (9, 6, 28)
            if (output_words, input_words, packet_size) != expected:
                raise DiscFormatError(f"Primitive {object_index}:{primitive_index} has inconsistent packet lengths.")
            position = cursor + 4
            uv0 = TexCoord(*struct.unpack_from("2B", data, position))
            clut = struct.unpack_from("<H", data, position + 2)[0]
            uv1 = TexCoord(*struct.unpack_from("2B", data, position + 4))
            tpage = struct.unpack_from("<H", data, position + 6)[0]
            uv2 = TexCoord(*struct.unpack_from("2B", data, position + 8))
            uvs = [uv0, uv1, uv2]
            position += 12
            if is_quad:
                uvs.append(TexCoord(*struct.unpack_from("2B", data, position)))
                position += 4
            count = 4 if is_quad else 3
            pairs = [struct.unpack_from("<HH", data, position + index * 4) for index in range(count)]
            for normal_index, vertex_index in pairs:
                if vertex_index >= vertex_count or normal_index >= normal_count:
                    raise DiscFormatError(f"Primitive {object_index}:{primitive_index} has an out-of-range index.")
            material = Material(tpage & 0x1F, (tpage >> 7) & 3, (tpage >> 5) & 3,
                                (clut & 0x3F) * 16, (clut >> 6) & 0x1FF,
                                bool(mode & 2), bool(primitive_flags & 2))
            order = ((2, 1, 0), (1, 2, 3)) if is_quad else ((2, 1, 0),)
            for a, b, c in order:
                triangles.append(Triangle((pairs[a][1], pairs[b][1], pairs[c][1]),
                                          (pairs[a][0], pairs[b][0], pairs[c][0]),
                                          (uvs[a], uvs[b], uvs[c]), material))
            cursor += packet_size
        objects.append(ModelObject(vertices, normals, tuple(triangles), primitive_count, scale))
    for index, node in enumerate(skeleton):
        if node.object_index is not None and node.object_index >= object_count:
            raise DiscFormatError(f"Skeleton node {index} references absent object {node.object_index}.")
    pose, animation_index = _parse_initial_pose(data, animation_offset, len(skeleton))
    return StaticModel(tuple(objects), skeleton, pose, animation_offset, animation_index,
                       ("Static assembly uses only the initial transform prefix of animation record 0; "
                        "animation instructions are not decoded.",))


def validate_texture_materials(model: StaticModel, texture: TimImage) -> tuple[int, ...]:
    if texture.clut_x is None or texture.clut_y is None:
        raise DiscFormatError("Textured MMD requires a paletted TIM with CLUT coordinates.")
    expected_page = texture.image_x // 64
    palettes = set()
    for obj in model.objects:
        for face in obj.triangles:
            material = face.material
            if material.color_mode != texture.mode:
                raise DiscFormatError("TMD texture color mode does not match its associated TIM.")
            if material.texture_page != expected_page:
                raise DiscFormatError("TMD texture page does not match the associated TIM VRAM origin.")
            if material.clut_x != texture.clut_x:
                raise DiscFormatError("TMD CLUT X does not match the associated TIM.")
            palette = material.clut_y - texture.clut_y
            if not 0 <= palette < texture.palette_count:
                raise DiscFormatError("TMD CLUT Y is outside the associated TIM palettes.")
            palettes.add(palette)
            for uv in face.uvs:
                if uv.u >= texture.image.width or uv.v >= texture.image.height:
                    raise DiscFormatError("TMD UV is outside the associated TIM image.")
    return tuple(sorted(palettes))
