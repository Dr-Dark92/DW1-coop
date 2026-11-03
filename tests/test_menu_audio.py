from array import array
from dw1_workbench.menu_audio import MenuAudio, tone_pcm


def test_tones_are_distinct_stereo_and_mix_without_clipping():
    tones = [tone_pcm(event) for event in ("open","navigate","choose","back")]
    assert len(set(tones)) == 4
    for raw in tones:
        samples = array("h");samples.frombytes(raw)
        assert samples and max(samples)>0 and min(samples)<0
        assert samples[::2] == samples[1::2]
    audio = MenuAudio(44100)
    audio.trigger("choose")
    raw = array("h",[32760]*1000).tobytes()
    mixed = array("h");mixed.frombytes(audio.mix(raw))
    assert max(mixed)<=32767 and min(mixed)>=-32768
    for _ in range(100): audio.mix(bytes(4096))
    assert not audio.pending
