import numpy as np

from dw1_workbench.player import PlayConfig, stretched_audio


def tone(frames):
    left = np.arange(frames, dtype="<i2") * 10
    return np.column_stack((left, -left)).astype("<i2").tobytes()


def test_double_speed_halves_the_audio_and_keeps_its_shape():
    out = np.frombuffer(stretched_audio(tone(100), 2.0), dtype="<i2").reshape(-1, 2)
    assert len(out) == 50 and out[0].tolist() == [0, 0]
    assert out[10].tolist() == [200, -200] and out[49, 0] == 980


def test_half_speed_doubles_the_audio():
    assert len(stretched_audio(tone(100), 0.5)) == 200 * 4


def test_same_length_comes_back_untouched():
    data = tone(100)
    assert stretched_audio(data, 1.001) is data


def test_framerate_defaults_to_the_game_s_own():
    assert PlayConfig.__dataclass_fields__["framerate"].default == 0.0
