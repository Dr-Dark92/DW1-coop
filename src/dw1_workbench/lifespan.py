"""Pause normal hourly lifespan loss, without restoring lives or reviving a partner.

Frame-local snapshots deliberately do not survive save loads, resets or evolution.
Native age/evolution timers, sickness and battle consequences remain active.
"""
import struct

from .live import ram_offset
from .progression import GAME_STATE, HOUR, LIFETIME, PARTNER_ENTITY, PARTNER_LIVES, PARTNER_STATS


class LifespanPause:
    def __init__(self, read, write):
        self.read, self.write = read, write

    def _short(self, address):
        return struct.unpack("<h", self.read(ram_offset(address), 2))[0]

    def _put(self, value):
        self.write(ram_offset(LIFETIME), struct.pack("<h", value))

    def before_frame(self):
        kind = struct.unpack("<i", self.read(ram_offset(PARTNER_ENTITY), 4))[0]
        life = self._short(LIFETIME)
        if (not 0 < kind < 180 or life <= 0 or self._short(PARTNER_STATS + 16) <= 0
                or self._short(GAME_STATE) != 0):
            return None
        lives = self.read(ram_offset(PARTNER_LIVES), 1)
        # Avoid the native death check seeing zero in the hourly-decrement frame.
        if life == 1:
            self._put(2)
        return kind, life, lives, self._short(HOUR)

    def after_frame(self, snapshot):
        if snapshot is None:
            return
        kind, life, lives, hour = snapshot
        current_kind = struct.unpack("<i", self.read(ram_offset(PARTNER_ENTITY), 4))[0]
        current = self._short(LIFETIME)
        if (kind != current_kind or lives != self.read(ram_offset(PARTNER_LIVES), 1)
                or self._short(GAME_STATE) != 0 or current <= 0):
            return  # never revive or overwrite battle/evolution changes
        prepared = max(2, life)
        if current == prepared and life == 1:
            self._put(life)  # roll back the temporary guard when no hour passed
        elif current == prepared - 1 and self._short(HOUR) != hour:
            self._put(life)
