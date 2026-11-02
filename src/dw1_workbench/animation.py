"""DW1 MMD animation records: bounded parsing and an exact frame stepper.

The stepping follows the game's tickAnimation/tickMomentum in the matching decompilation
jype0/dw_decomp aaf69a4 (src/main/anim.c), cross-checked with DW1-SydPatches b9671fa
(src/Model.cpp): keyframes set per-bone, per-axis velocities (integer quotient plus a
Bresenham-style remainder), velocities are applied before the instructions of each frame,
and loops jump back by frame number. Root translation is not a bone channel: it is stepped
once per frame in applyRootMomentum and moves the entity. (SydPatches also steps its
remainder counter in tickMomentum; the decompiled original does not.)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import DiscFormatError
from .mmd import MAX_OBJECTS

DECODER_VERSION = "mmd-animation-v1"
MAX_ANIMATIONS = 1024
MAX_INSTRUCTIONS = 20_000
MAX_FRAME = 0x0FFF
INFINITE_LOOP = 0xFF
AXES = 9  # scale xyz, rotation xyz, translation xyz
TICKS_PER_SECOND = 20  # converter time base; not established from game code

OP_KEYFRAME, OP_LOOP_START, OP_LOOP_END, OP_TEXTURE, OP_SOUND = 0x0000, 0x1000, 0x2000, 0x3000, 0x4000


@dataclass(frozen=True)
class BonePose:
    scale: tuple[int, int, int]
    rotation: tuple[int, int, int]
    translation: tuple[int, int, int]


@dataclass(frozen=True)
class Channel:
    node: int
    scale: int
    values: tuple[tuple[int, int], ...]  # (axis 0..8, value)


@dataclass(frozen=True)
class Instruction:
    opcode: int
    frame: int
    channels: tuple[Channel, ...] = ()
    loop_count: int = 0
    jump_frame: int = 0
    payload: tuple[int, ...] = ()  # texture: src x/y, w/h, dest x/y bytes; sound: sound id, vab id


@dataclass(frozen=True)
class AnimationClip:
    index: int
    offset: int
    size: int
    frame_count: int
    has_scale: bool
    initial: tuple[BonePose, ...]
    instructions: tuple[Instruction, ...]

    @property
    def loops_forever(self) -> bool:
        return any(i.opcode == OP_LOOP_START and i.loop_count in (0, INFINITE_LOOP) for i in self.instructions)


def _check_range(offset: int, size: int, limit: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > limit or size > limit - offset:
        raise DiscFormatError(f"{label} is outside its bounded section.")


def parse_clip(data: bytes, index: int, start: int, end: int, bone_count: int) -> AnimationClip:
    """Parse one animation record occupying data[start:end]."""
    _check_range(start, 2, end, f"Animation {index} header")
    (frame_word,) = struct.unpack_from("<H", data, start)
    has_scale = bool(frame_word & 0x8000)
    cursor = start + 2

    def word(signed: bool = False) -> int:
        nonlocal cursor
        _check_range(cursor, 2, end, f"Animation {index} data")
        (value,) = struct.unpack_from("<h" if signed else "<H", data, cursor)
        cursor += 2
        return value

    root = BonePose((4096, 4096, 4096), (0, 0, 0), (0, 0, 0))
    initial = [root]
    for _ in range(1, bone_count):
        scale = (word(True), word(True), word(True)) if has_scale else (4096, 4096, 4096)
        rotation = (word(True), word(True), word(True))
        translation = (word(True), word(True), word(True))
        initial.append(BonePose(scale, rotation, translation))
    instructions = []
    while True:
        if len(instructions) > MAX_INSTRUCTIONS:
            raise DiscFormatError(f"Animation {index} exceeds the instruction safety limit.")
        head = word()
        if head == 0:
            break
        opcode, frame = head & 0xF000, head & 0x0FFF
        if opcode == OP_KEYFRAME:
            channels = []
            while cursor + 2 <= end and struct.unpack_from("<H", data, cursor)[0] & 0x8000:
                entry = word()
                node, mask = entry & 0x3F, (entry >> 6) & 0x1FF
                scale = word(True)
                if node >= bone_count:
                    raise DiscFormatError(f"Animation {index} keyframe targets bone {node} of {bone_count}.")
                if scale == 0:
                    raise DiscFormatError(f"Animation {index} keyframe has a zero step count.")
                values = tuple((axis, word(True)) for axis in range(AXES) if mask & (0x100 >> axis))
                channels.append(Channel(node, scale, values))
            # A keyframe with no channels is valid: the game's channel loop simply runs zero times.
            instructions.append(Instruction(opcode, frame, tuple(channels)))
        elif opcode == OP_LOOP_START:
            # The game also reads a loop start's low 12 bits as its frame when it is the next instruction.
            instructions.append(Instruction(opcode, frame, loop_count=head & 0xFF))
        elif opcode == OP_LOOP_END:
            instructions.append(Instruction(opcode, frame, jump_frame=word()))
        elif opcode == OP_TEXTURE:
            raw = data[cursor:cursor + 6]
            _check_range(cursor, 6, end, f"Animation {index} texture instruction")
            cursor += 6
            # little-endian words: (srcX<<8|srcY), (width<<8|height), (destX<<8|destY)
            instructions.append(Instruction(opcode, frame, payload=(raw[1], raw[0], raw[3], raw[2], raw[5], raw[4])))
        elif opcode == OP_SOUND:
            value = word()
            instructions.append(Instruction(opcode, frame, payload=(value & 0xFF, value >> 8)))
        else:
            # The game's dispatch has no case for these and would stall on them.
            raise DiscFormatError(f"Animation {index} has unsupported instruction 0x{head:04x}.")
    loop_depth = 0
    for instruction in instructions:
        if instruction.opcode == OP_LOOP_START:
            loop_depth += 1
        elif instruction.opcode == OP_LOOP_END:
            if loop_depth == 0:
                raise DiscFormatError(f"Animation {index} ends a loop it never started.")
            loop_depth -= 1
    return AnimationClip(index, start, cursor - start, frame_word & 0x7FFF, has_scale, tuple(initial),
                         tuple(instructions))


def parse_animations(data: bytes, animation_offset: int, bone_count: int) -> list[AnimationClip | None]:
    """Parse the MMD animation table; empty slots are None, as in the game's pointer table."""
    if not 1 <= bone_count <= MAX_OBJECTS:
        raise DiscFormatError(f"Bone count {bone_count} is outside the safety limit.")
    _check_range(animation_offset, 4, len(data), "Animation pointer table")
    (first,) = struct.unpack_from("<I", data, animation_offset)
    if first < 4 or first % 4 or first // 4 > MAX_ANIMATIONS:
        raise DiscFormatError("Animation pointer table has an invalid first offset.")
    count = first // 4
    _check_range(animation_offset, count * 4, len(data), "Animation pointer table")
    offsets = struct.unpack_from(f"<{count}I", data, animation_offset)
    starts = sorted({offset for offset in offsets if offset})
    clips: list[AnimationClip | None] = []
    for index, offset in enumerate(offsets):
        if not offset:
            clips.append(None)
            continue
        if offset < count * 4:
            raise DiscFormatError(f"Animation {index} starts inside the pointer table.")
        start = animation_offset + offset
        following = [s for s in starts if s > offset]
        end = animation_offset + following[0] if following else len(data)
        _check_range(start, 0, len(data), f"Animation {index}")
        clips.append(parse_clip(data, index, start, min(end, len(data)), bone_count))
    return clips


@dataclass
class _Axis:
    delta: int = 0
    sub_delta: int = 0
    scale: int = 0
    sub_scale: int = 0
    sub_value: int = 0


@dataclass
class TextureEvent:
    tick: int
    source: tuple[int, int]
    size: tuple[int, int]
    destination: tuple[int, int]


@dataclass
class SoundEvent:
    tick: int
    vab_id: int
    sound_id: int


@dataclass
class AnimationPlayer:
    """Steps a clip one game frame at a time, exactly as the game's tick does."""
    clip: AnimationClip
    bone_count: int
    frame: int = 1
    ticks: int = 0
    running: bool = True
    channels: list[list[int]] = field(default_factory=list)  # per bone: scale xyz, rotation xyz, translation xyz
    root_motion: list[int] = field(default_factory=lambda: [0, 0, 0])  # root translation channel totals
    events: list = field(default_factory=list)

    def __post_init__(self):
        self.reset()

    def reset(self):
        self.frame, self.ticks, self.running = 1, 0, True
        self.channels = [list(p.scale) + list(p.rotation) + list(p.translation) for p in self.clip.initial]
        self.momentum = [[_Axis() for _ in range(AXES)] for _ in range(self.bone_count)]
        self.root_motion = [0, 0, 0]
        self.root_velocity = [0, 0, 0]
        self.events = []
        self._pointer = 0
        self._loop_start = 0
        self._loop_count = 0
        self._next_frame = 1

    def _instruction_frame(self, pointer: int) -> int:
        """Frame of the instruction at pointer (0 for the end marker, as the game reads it)."""
        if pointer >= len(self.clip.instructions):
            return 0
        return self.clip.instructions[pointer].frame

    @staticmethod
    def _set_velocity(state: _Axis, value: int, scale: int):
        # handleKeyFrameInstruction: C division and remainder truncate toward zero; the remainder is
        # spread as +/-1 steps so that `scale` frames add exactly `value`.
        quotient = abs(value) // abs(scale)
        state.delta = quotient if (value >= 0) == (scale > 0) else -quotient
        remainder = value - state.delta * scale
        if remainder:
            state.sub_value = -1 if remainder < 1 else 1
            state.scale = scale
            state.sub_scale = scale
        state.sub_delta = abs(remainder)

    def _apply_root_motion(self):
        # applyRootMomentum: the game rotates this velocity by the root bone's matrix before moving the
        # entity; root_velocity keeps the unrotated per-frame value for the caller to transform.
        velocity = [0, 0, 0]
        for axis in range(3):
            if axis == 0 and self.clip.index in (0x23, 0x24):
                continue
            state = self.momentum[0][6 + axis]
            velocity[axis] = state.delta
            if state.sub_delta:
                state.sub_scale -= state.sub_delta
                if state.sub_scale <= 0:
                    velocity[axis] += state.sub_value
                    state.sub_scale += state.scale
            self.root_motion[axis] += velocity[axis]
        self.root_velocity = velocity

    def pose(self, bone: int) -> BonePose:
        values = self.channels[bone]
        return BonePose(tuple(values[0:3]), tuple(values[3:6]), tuple(values[6:9]))

    def tick(self):
        if not self.running:
            return
        self.ticks += 1
        for bone in range(self.bone_count):
            for axis, state in enumerate(self.momentum[bone]):
                if state.delta == 0 and state.sub_delta == 0:
                    continue
                if bone == 0 and axis >= 6:
                    break  # root translation is applied below, not to the root bone
                value = state.delta
                if state.sub_delta:
                    state.sub_scale -= state.sub_delta
                    if state.sub_scale < 1:
                        state.sub_scale += state.scale
                        value += state.sub_value
                self.channels[bone][axis] += value
            if bone == 0:
                self._apply_root_motion()
        self.sample = [row[:] for row in self.channels]  # pose after this frame's motion, before its instructions
        self.sample_root = self.root_motion[:]
        instructions = self.clip.instructions
        if self._pointer < len(instructions) and instructions[self._pointer].opcode == OP_LOOP_START:
            self._loop_count = instructions[self._pointer].loop_count
            self._pointer += 1
            self._loop_start = self._pointer
            self._next_frame = self._instruction_frame(self._pointer)
        guard = 0
        while self.frame == self._next_frame:
            guard += 1
            if guard > MAX_INSTRUCTIONS:
                raise DiscFormatError("Animation instructions do not advance.")
            instruction = instructions[self._pointer]
            if instruction.opcode == OP_SOUND:
                self.events.append(SoundEvent(self.ticks, instruction.payload[1], instruction.payload[0]))
                self._pointer += 1
            elif instruction.opcode == OP_TEXTURE:
                p = instruction.payload
                self.events.append(TextureEvent(self.ticks, (p[0], p[1]), (p[2], p[3]), (p[4], p[5])))
                self._pointer += 1
            elif instruction.opcode == OP_LOOP_END:
                if self._loop_count != INFINITE_LOOP:
                    self._loop_count = (self._loop_count - 1) & 0xFF
                if self._loop_count == 0:
                    self._pointer += 1
                else:
                    self.frame = instruction.jump_frame
                    self._pointer = self._loop_start
            elif instruction.opcode == OP_LOOP_START:
                self._loop_count = instruction.loop_count
                self._pointer += 1
                self._loop_start = self._pointer
            else:
                for channel in instruction.channels:
                    for axis, value in channel.values:
                        self._set_velocity(self.momentum[channel.node][axis], value, channel.scale)
                self._pointer += 1
            self._next_frame = self._instruction_frame(self._pointer)
        if self.frame == self.clip.frame_count:
            self.running = False
        else:
            self.frame += 1
