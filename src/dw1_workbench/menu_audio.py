"""Short generated UI tones mixed into the core's normal stereo PCM stream."""
import math
from array import array


def tone_pcm(event, rate=44100):
    notes = {"open": ((520,.035),(780,.035),(1040,.05)),
             "navigate": ((880,.035),), "choose": ((880,.045),(1320,.065)),
             "back": ((520,.04),(350,.05))}.get(event, ())
    samples = array("h")
    for hz,duration in notes:
        count = int(rate * duration)
        for i in range(count):
            envelope = min(1, i / max(1, rate*.002), (count-i) / max(1, rate*.009))
            phase = 2*math.pi*hz*i/rate
            value = int(4200*envelope*(math.sin(phase)+.2*math.sin(phase*3)))
            samples.extend((value,value))
    return samples.tobytes()


class MenuAudio:
    def __init__(self, rate):
        self.rate = rate
        self.pending = array("h")

    def trigger(self, event):
        # New navigation replaces old queued bleeps, avoiding an input backlog.
        self.pending = array("h")
        self.pending.frombytes(tone_pcm(event,self.rate))

    def mix(self, audio):
        if not self.pending or not audio:
            return audio
        pcm = array("h"); pcm.frombytes(audio)
        count = min(len(pcm),len(self.pending))
        for i in range(count):
            pcm[i] = max(-32768,min(32767,pcm[i]+self.pending[i]))
        del self.pending[:count]
        return pcm.tobytes()
