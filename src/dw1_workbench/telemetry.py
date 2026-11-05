"""Opt-in, bounded runtime diagnostics. No screenshots, RAM, save states or game data."""
from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import threading
import time


class Telemetry:
    def __init__(self, enabled=False, capacity=2048, clock=time.monotonic_ns):
        self.enabled, self.capacity, self.clock = enabled, capacity, clock
        self._lock = threading.Lock()
        self._samples = {}  # fixed metric names supplied by code, never scene-dependent names
        self._counts = Counter()
        self._events = deque(maxlen=256)
        self.started = clock()
        self._scene = None
        self._transition_started = self._battle_started = None

    @contextmanager
    def measure(self, name):
        if not self.enabled:
            yield
            return
        start = self.clock()
        try:
            yield
        finally:
            self.duration(name, self.clock() - start)

    def duration(self, name, nanoseconds):
        if self.enabled:
            with self._lock:
                self._samples.setdefault(name, deque(maxlen=self.capacity)).append(max(0, nanoseconds) / 1e6)
                self._counts[name + ".total"] += 1

    def count(self, name, amount=1):
        if self.enabled:
            with self._lock:
                self._counts[name] += amount

    def event(self, name, **fields):
        if self.enabled:
            with self._lock:
                self._events.append(dict(event=name, elapsed_ms=(self.clock() - self.started) / 1e6, **fields))

    def snapshot(self):
        with self._lock:
            samples = {k: sorted(v) for k, v in self._samples.items()}
            counts, events = dict(self._counts), list(self._events)
        metrics = {}
        for name, values in samples.items():
            metrics[name] = dict(samples=len(values), **{
                f"p{p}_ms": values[max(0, math.ceil(len(values) * p / 100) - 1)] for p in (50, 95, 99)})
        return dict(format=1, elapsed_seconds=(self.clock() - self.started) / 1e9,
                    metrics=metrics, counts=counts, events=events)

    def memory(self, pid=None):
        """Current Linux RSS, not peak; unavailable platforms are explicitly marked."""
        if not self.enabled:
            return
        try:
            values = Path(f"/proc/{pid if pid is not None else 'self'}/statm").read_text().split()
            rss = int(values[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, AttributeError, IndexError):
            rss = None
        self.event("memory", rss_bytes=rss, role="avatars" if pid is not None else "player", pid=pid or os.getpid())

    def observe_scene(self, map_index, game_state, transition):
        """USA RAM observations, not inferred wins/defeats. Mid-sequence startup is marked partial."""
        if not self.enabled:
            return
        now = self.clock()
        scene = (map_index, game_state, bool(transition))
        previous = self._scene
        if scene == previous:
            return
        self.event("runtime.scene", map=map_index, game_state=game_state, transition=bool(transition),
                   initial=previous is None)
        self.count("runtime.scene_changes")
        if previous is not None:
            if transition and not previous[2]:
                self._transition_started = now
            elif not transition and self._transition_started is not None:
                self.duration("zone.script_transition", now - self._transition_started)
                self._transition_started = None
            in_battle, was_battle = game_state in (1, 2, 3), previous[1] in (1, 2, 3)
            if in_battle and not was_battle:
                self._battle_started = now
                self.event("battle.entry", map=map_index)
            elif not in_battle and was_battle:
                if self._battle_started is not None:
                    self.duration("battle.duration", now - self._battle_started)
                self.event("battle.exit", map=map_index, partial=self._battle_started is None)
                self._battle_started = None
        self._scene = scene

    def discontinuity(self, reason):
        self.event("runtime.discontinuity", reason=reason)
        self._scene = None
        self._transition_started = self._battle_started = None

    def write(self, directory):
        """Exclusive report creation; never overwrite a prior run or a user file."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"runtime-{os.getpid()}-{time.monotonic_ns()}.json"
        with path.open("x", encoding="utf-8") as output:
            json.dump(self.snapshot(), output, indent=2)
        return path
