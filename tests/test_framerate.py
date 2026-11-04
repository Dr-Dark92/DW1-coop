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


def repeat_state(menu=None, visitors=()):
    from types import SimpleNamespace
    return SimpleNamespace(_previous_picture=None, _repeats=0, visitors=list(visitors),
                           evolution=SimpleNamespace(menu=menu) if menu is not None else None)


def test_the_same_picture_is_skipped_but_not_for_ever():
    from types import SimpleNamespace
    from dw1_workbench.player import MOST_REPEATS, EmulatorWorker
    state, frame = repeat_state(), SimpleNamespace(data=b"picture")
    results = [EmulatorWorker._is_repeat(state, frame) for _ in range(MOST_REPEATS + 3)]
    assert results[0] is False and all(results[1:MOST_REPEATS + 1])
    assert results[MOST_REPEATS + 1] is False  # redrawn now and then, for the map and bars over a still picture
    assert EmulatorWorker._is_repeat(state, SimpleNamespace(data=b"another")) is False


def test_a_menu_or_a_visitor_over_the_picture_always_counts():
    from types import SimpleNamespace
    from dw1_workbench.player import EmulatorWorker
    frame = SimpleNamespace(data=b"picture")
    for state in (repeat_state(menu=object()), repeat_state(visitors=[object()])):
        assert [EmulatorWorker._is_repeat(state, frame) for _ in range(3)] == [False, False, False]
