import struct

import pytest

from dw1_workbench.animation import (OP_KEYFRAME, OP_LOOP_END, OP_LOOP_START, OP_SOUND, OP_TEXTURE,
                                     AnimationPlayer, SoundEvent, TextureEvent, parse_animations)
from dw1_workbench.errors import DiscFormatError

TRANSLATE_X, ROTATE_Y = 6, 4


def keyframe(frame, *channels):
    """channels: (node, scale, {axis: value})"""
    words = [frame]
    for node, scale, values in channels:
        mask = sum(0x100 >> axis for axis in values)
        words += [0x8000 | (mask << 6) | node, scale & 0xFFFF] + [values[a] & 0xFFFF for a in sorted(values)]
    return words


def clip_bytes(instructions, frames=10, bones=2, scale=False):
    words = [frames | (0x8000 if scale else 0)]
    for _ in range(1, bones):
        words += ([4096, 4096, 4096] if scale else []) + [0, 0, 0, 0, 0, 0]
    return struct.pack(f"<{len(words) + len(instructions) + 1}H", *words, *instructions, 0)


def table(*clips, empty_second=False):
    """Pointer table followed by clips; returns (data, offset of the table).

    The game reads the first pointer as the table size, so only later slots may be empty."""
    slots = list(clips[:1]) + ([None] if empty_second else []) + list(clips[1:])
    header = 4 * len(slots)
    offsets, body = [], b""
    for clip in slots:
        offsets.append(0 if clip is None else header + len(body))
        body += clip or b""
    return struct.pack(f"<{len(slots)}I", *offsets) + body, 0


def run(instructions, ticks, **kwargs):
    data, offset = table(clip_bytes(instructions, **kwargs))
    clip = parse_animations(data, offset, kwargs.get("bones", 2))[0]
    player = AnimationPlayer(clip, kwargs.get("bones", 2))
    history = []
    for _ in range(ticks):
        player.tick()
        history.append([row[:] for row in player.sample])
    return player, history


def test_table_slots_initial_pose_and_instruction_kinds():
    instructions = (keyframe(1, (1, 4, {ROTATE_Y: 40})) + [OP_LOOP_START | 3] + [OP_SOUND | 2, (7 << 8) | 9]
                    + [OP_TEXTURE | 2, (8 << 8) | 32, (8 << 8) | 24, (0 << 8) | 32] + [OP_LOOP_END | 5, 2])
    data, offset = table(clip_bytes(instructions, scale=True), clip_bytes(keyframe(1)), empty_second=True)
    clip, empty, _ = parse_animations(data, offset, 2)
    assert empty is None and clip.index == 0 and clip.has_scale and clip.frame_count == 10
    assert clip.initial[1].scale == (4096, 4096, 4096)
    assert [i.opcode for i in clip.instructions] == [OP_KEYFRAME, OP_LOOP_START, OP_SOUND, OP_TEXTURE, OP_LOOP_END]
    assert clip.instructions[0].channels[0].values == ((ROTATE_Y, 40),)
    assert clip.instructions[2].payload == (9, 7)  # sound id, vab id
    assert clip.instructions[3].payload == (8, 32, 8, 24, 0, 32)  # src x/y, width/height, dest x/y
    assert clip.instructions[4].jump_frame == 2 and not clip.loops_forever


@pytest.mark.parametrize(("value", "scale", "steps"), [(10, 4, [2, 3, 2, 3]), (-10, 4, [-2, -3, -2, -3]),
                                                       (7, 7, [1] * 7), (3, 4, [0, 1, 1, 1])])
def test_remainder_stepping_matches_the_games_integer_arithmetic(value, scale, steps):
    # the velocity set at frame 1 first moves the bone on tick 2
    _, history = run(keyframe(1, (1, scale, {ROTATE_Y: value})), len(steps) + 1, frames=20)
    totals = [frame[1][ROTATE_Y] for frame in history]
    assert totals[0] == 0
    assert [b - a for a, b in zip(totals, totals[1:])] == steps
    assert totals[-1] == value


def test_velocity_persists_until_a_later_keyframe_changes_it():
    _, history = run(keyframe(1, (1, 1, {ROTATE_Y: 5})) + keyframe(4, (1, 1, {ROTATE_Y: 0})), 8, frames=20)
    assert [frame[1][ROTATE_Y] for frame in history] == [0, 5, 10, 15, 15, 15, 15, 15]


def test_finite_loop_repeats_then_continues_to_the_end():
    instructions = [OP_LOOP_START | 2] + keyframe(1, (1, 1, {ROTATE_Y: 1})) + [OP_LOOP_END | 3, 1]
    player, _ = run(instructions, 40, frames=5)
    # frames 1, 2, 3 -> jump: frame 1 runs again in the same tick, then 2, 3, 4, 5
    assert not player.running and player.ticks == 7
    assert player.frame == 5


def test_endless_loop_never_finishes():
    instructions = [OP_LOOP_START | 0xFF] + keyframe(1) + [OP_SOUND | 2, 0x0101] + [OP_LOOP_END | 4, 1]
    player, _ = run(instructions, 50, frames=6)
    assert player.running and player.frame <= 4
    sounds = [event.tick for event in player.events if isinstance(event, SoundEvent)]
    assert sounds[:3] == [2, 5, 8]  # the jump re-runs frame 1 in the tick that ends frame 4


def test_root_translation_moves_the_entity_not_the_root_bone():
    player, history = run(keyframe(1, (0, 2, {TRANSLATE_X: 9})), 3, frames=10)  # two moving ticks
    assert history[-1][0][TRANSLATE_X] == 0
    assert player.root_motion == [9, 0, 0]


def test_root_sideways_motion_is_zeroed_for_animations_0x23_and_0x24():
    clip = clip_bytes(keyframe(1, (0, 1, {TRANSLATE_X: 4, TRANSLATE_X + 2: 4})), frames=10)
    data, offset = table(*([clip] * 0x25))
    clips = parse_animations(data, offset, 2)
    for index, expected_x in ((0x22, 12), (0x23, 0), (0x24, 0)):
        player = AnimationPlayer(clips[index], 2)
        for _ in range(4):
            player.tick()
        assert player.root_motion == [expected_x, 0, 12]


def test_texture_and_sound_events_carry_decoded_fields():
    instructions = [OP_TEXTURE | 1, (8 << 8) | 32, (8 << 8) | 24, (0 << 8) | 32, OP_SOUND | 1, (4 << 8) | 16]
    player, _ = run(instructions, 1)
    texture, sound = player.events
    assert isinstance(texture, TextureEvent) and (texture.source, texture.size, texture.destination) == ((8, 32), (8, 24), (0, 32))
    assert isinstance(sound, SoundEvent) and (sound.vab_id, sound.sound_id) == (4, 16)


@pytest.mark.parametrize(("instructions", "message"), [
    ([0x5001, 0], "unsupported instruction"),
    (keyframe(1, (1, 0, {ROTATE_Y: 1})), "zero step count"),
    (keyframe(1, (5, 1, {ROTATE_Y: 1})), "targets bone 5"),
    ([OP_LOOP_END | 2, 1], "never started"),
])
def test_parser_rejects_malformed_or_unsupported_clips(instructions, message):
    data, offset = table(clip_bytes(instructions))
    with pytest.raises(DiscFormatError, match=message):
        parse_animations(data, offset, 2)


def test_parser_rejects_an_unterminated_stream():
    data, offset = table(clip_bytes(keyframe(1, (1, 1, {ROTATE_Y: 1}))))
    with pytest.raises(DiscFormatError, match="outside its bounded section"):
        parse_animations(data[:-2], offset, 2)
