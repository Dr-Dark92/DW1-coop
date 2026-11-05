"""Fixed camera framing: more of the world in the same frame (×2 in the player, off by default; experimental).

Uses the legacy tile renderer with a corrected GTE origin and render-only projection. FrameController only keeps it
on while walking around: battles, map changes and saves go back to the game's own camera and code first.
"""
from __future__ import annotations

import struct

from .camera import (CameraZoom, CALL_SITES, VIEWPORT_DISTANCE, jal, sync_instruction_cache,
                     SET_POS_DATA_POLY_FT4, RENDER_MAP_LOOP, RENDER_MAP_LOOP_WORDS,
                     RENDER_MAP_AFTER_LOOP, NATIVE_UPLOADS, RA, j, jr, engine_params, ONE)
from .camera_memory import CameraReservation, BUFFER, PARAMS, END, saved_hold
from . import fixed_engine_code as engine
from .hd_background import GAME_STATE, SCRIPT_MAP_CHANGE_STATE, MAP_SIZE
from .libretro import LibretroError
from .live import CURRENT_SCREEN, ram_offset

FIXED_SCALE = 230
DOUBLE_FRAME = 128  # ×2: twice the world across and down
SETTLE_FRAMES = 60  # of calm exploration before the frame first goes on: the game's heap must be extended by then
RETURN_FRAMES = 2  # when it comes back, with its memory held, so it's on again during the fade
GTE_CR = b"\x02CR" + struct.pack("<I", 128)
GAME_LOOP, GAME_LOOP_END = 0x800EFB68, 0x800EFD30
GS_SET_PROJECTION, RENDER_OBJECTS = 0x800981C8, 0x800A315C
GS_SORT_SPRITE = 0x80096F90
GS_SORT_SPRITE_HEAD = (0x27BDFF50, 0xAFB00098)  # addiu sp, -176; sw s0, 152(sp): safe to run from elsewhere
SPRITE_STUB = PARAMS + 0x100 + 8  # FixedState.sprite_entry in camera_engine.c
NATIVE_STUBS = PARAMS + 0x100 + 32  # FixedState.native_entries
# Measure through the projection to move the game's own camera or decide where the partner walks, so they get the
# game's own projection: tickCameraFollowPlayer, initializeDrawingOffsets, tickCameraMovement, moveCameraByDiff,
# isTileOffScreen, and handleBattleStart, which also re-tests who's on screen (fixed_native_5).
NATIVE_CAMERA = (0x800D6F64, 0x800D6A4C, 0x800D80A0, 0x800D892C, 0x800D3078, 0x800E847C)
# renderMapOverlays only draws pieces near the game's own view: addi rt, rs, immediate of each of its four limits.
OVERLAY_LIMITS = {0x800B4460: (0x2282FFD8, -40, 40, "left"), 0x800B4468: (0x20620168, 360, 40, "right"),
                  0x800B4474: (0x22BEFFC4, -60, 60, "top"), 0x800B447C: (0x2057012C, 300, 60, "bottom")}
GUARD = b"DW1F" * 4


def jumps(word):
    """A branch or jump can't be moved: it would land somewhere else, or lose its delay slot."""
    opcode = word >> 26
    return opcode in (1, 2, 3, 4, 5, 6, 7) or (opcode == 0 and word & 0x3F in (8, 9))


def gte_projection_offset(state):
    """Verified Beetle gte.c StateRegs CR[32]; reject ambiguous/truncated layouts."""
    if state.count(GTE_CR) != 1:
        raise ValueError("Expected one verified Beetle GTE CR field")
    offset = state.index(GTE_CR) + len(GTE_CR) + 24 * 4
    if offset + (32 - 24) * 4 > len(state):
        raise ValueError("Truncated GTE projection fields")
    return offset


class FixedFramingProof(CameraZoom):
    """8 MB core only. A scene change raises from tick(); FrameController is what answers it."""

    def __init__(self, core, frame_scale=FIXED_SCALE):
        super().__init__(core)
        self.frame_scale = frame_scale
        self.reservation = None

    def install(self):
        if self.installed:
            return
        if self.core.ram_size != 8 * 1024 * 1024:
            raise ValueError("this needs the 8 MB core")
        self._scene = self.core.read_memory(ram_offset(CURRENT_SCREEN), 1)
        self._check_scene()
        width, height = struct.unpack("<bb", self.core.read_memory(ram_offset(MAP_SIZE), 2))
        if (width <= 0 or height <= 0 or width * height > 35
                or width * 128 * self.frame_scale < 320 * 256 or height * 128 * self.frame_scale < 240 * 256):
            raise ValueError("Map artwork cannot cover this fixed expanded view")
        state = self.core.serialize()
        offset = gte_projection_offset(state)
        self._native_gte = state[offset:offset + 12]
        if any(self._native_gte[:8]):
            raise ValueError("Nonzero baseline GTE origin is not verified by this proof")
        self._native_projection = self.core.read_memory(ram_offset(VIEWPORT_DISTANCE), 4)
        code = self.core.read_memory(ram_offset(GAME_LOOP), GAME_LOOP_END - GAME_LOOP)

        def only_call(target, name):
            calls = [GAME_LOOP + n for n in range(0, len(code), 4) if struct.unpack_from("<I", code, n)[0] == jal(target)]
            if len(calls) != 1:
                raise ValueError(f"Expected one USA gameLoop {name} call")
            return calls[0]

        self._projection_call = only_call(GS_SET_PROJECTION, "projection")
        render_call = only_call(RENDER_OBJECTS, "renderObjects")
        if self._read_words(GS_SORT_SPRITE, 2) != GS_SORT_SPRITE_HEAD:
            raise ValueError("GsSortSprite is not the expected native USA code")
        if any(self._read_u32(address) != limit[0] for address, limit in OVERLAY_LIMITS.items()):
            raise ValueError("renderMapOverlays is not the expected native USA code")
        heads = [self._read_words(address, 2) for address in NATIVE_CAMERA]
        if any(jumps(word) for head in heads for word in head):
            raise ValueError("A camera function is not the expected native USA code")
        for site in CALL_SITES:
            if self._read_u32(site) != jal(SET_POS_DATA_POLY_FT4):
                raise ValueError("Foreground call is not the expected native USA code")
        if struct.unpack("<4I", self.core.read_memory(ram_offset(RENDER_MAP_LOOP), 16)) != RENDER_MAP_LOOP_WORDS:
            raise ValueError("Map renderer is not the expected native USA code")
        if any(struct.unpack("<2I", self.core.read_memory(ram_offset(address), 8)) != words
               for address, words in NATIVE_UPLOADS.items()):
            raise ValueError("Tile upload code is not the expected native USA code")
        self._hold_memory()
        self._guards = (BUFFER + 0xFFF0, END - 16)
        try:
            self._write(BUFFER, bytes(END - BUFFER))  # only memory removed from the allocator's free list
            self._write(engine.LOAD_ADDRESS, engine.CODE)
            self._write(PARAMS, engine_params(self.frame_scale))
            self._write(PARAMS + 62, struct.pack("<h", 1))
            self._write(SPRITE_STUB, struct.pack("<4I", *GS_SORT_SPRITE_HEAD, j(GS_SORT_SPRITE + 8), 0))
            for address in self._guards:
                self._write(address, GUARD)
            for index, (address, head) in enumerate(zip(NATIVE_CAMERA, heads)):
                self._write(NATIVE_STUBS + index * 16, struct.pack("<4I", *head, j(address + 8), 0))
                self._patch(address, [j(engine.NATIVE_ENTRIES[index]), 0])
            self._patch(self._projection_call, [jal(engine.PROJECTION_ENTRY)])
            self._patch(render_call, [jal(engine.RENDER_ENTRY)])
            self._patch(GS_SORT_SPRITE, [j(engine.SPRITE_ENTRY), 0])
            for address, (word, _limit, margin, side) in OVERLAY_LIMITS.items():
                self._patch(address, [word & 0xFFFF0000 | self._overlay_limit(side, margin) & 0xFFFF])
            for site in CALL_SITES:
                self._patch(site, [jal(engine.FOREGROUND_ENTRY)])
            self._patch(RENDER_MAP_LOOP, [jal(engine.ENTRY), 0, j(RENDER_MAP_AFTER_LOOP), 0])
            for address in NATIVE_UPLOADS:
                self._patch(address, [jr(RA), 0])
            self.scale = self.frame_scale
            self._checks = 0
            self.installed = True
            self._sync()
        except Exception:
            for address, data in self._original.items():
                self._write(address, data)
            self._sync()
            self._original.clear()
            self.release_memory()
            self.installed = False
            raise

    def _read_words(self, address, count):
        return struct.unpack(f"<{count}I", self.core.read_memory(ram_offset(address), 4 * count))

    def _overlay_limit(self, side, margin):
        """Relative to the camera. The frame's anchor can put the view anywhere from its left edge to its right."""
        size = 320 if side in ("left", "right") else 240
        seen = size * ONE // self.frame_scale
        return margin + seen if side in ("right", "bottom") else -(margin + seen - size)

    def _hold_memory(self):
        """The game may allocate during a battle or map change, so the memory stays held between uses."""
        if self.reservation is not None and self.reservation.active:
            try:
                self.reservation.check()
                return
            except ValueError:  # the game rebuilt its heap: the old hold means nothing now
                self.reservation.active = False
        self.reservation = CameraReservation(self.core)
        self.reservation.reserve()

    def release_memory(self):
        if self.reservation is not None and self.reservation.active:
            self.reservation.release()

    def _sync(self):
        words = [address + offset for address, data in self._original.items() for offset in range(0, len(data), 4)]
        words.extend(range(engine.LOAD_ADDRESS, engine.LOAD_ADDRESS + len(engine.CODE), 4))
        words.extend(range(SPRITE_STUB, SPRITE_STUB + 16, 4))
        words.extend(range(NATIVE_STUBS, NATIVE_STUBS + 16 * len(NATIVE_CAMERA), 4))
        sync_instruction_cache(self.core, words)

    @property
    def engine_frames(self):
        return self._read_u32(PARAMS + 4) if self.installed else 0

    @property
    def engine_uploads(self):
        return self._read_u32(PARAMS + 8) if self.installed else 0

    def _check_scene(self):
        if (self.core.read_memory(ram_offset(CURRENT_SCREEN), 1) != self._scene
                or self.core.read_memory(ram_offset(GAME_STATE), 1) != b"\0"
                or self.core.read_memory(ram_offset(SCRIPT_MAP_CHANGE_STATE), 2) != b"\0\0"):
            raise ValueError("Fixed framing proof stops at battles, transitions or scene changes")

    def tick(self):
        if not self.installed:
            return
        self._check_scene()
        # A frame can end while the game is drawing, when the value is the scaled one.
        native = struct.unpack("<i", self._native_projection)[0]
        if struct.unpack("<i", self.core.read_memory(ram_offset(VIEWPORT_DISTANCE), 4))[0] not in (native, (native * self.frame_scale) >> 8):
            raise ValueError("Native RAM projection changed; stop the framing proof")
        if any(self.core.read_memory(ram_offset(address), len(GUARD)) != GUARD for address in self._guards):
            raise ValueError("Camera memory guard changed; stop the framing proof")
        self._checks += 1
        if self._checks % 30 == 0:
            self.reservation.check()

    def set_scale(self, scale):
        raise ValueError("This experiment has one fixed framing, not an adjustable zoom")

    def uninstall(self, release=True):
        """The game's own code, camera and tile ring, whatever else went wrong; `release` also gives the memory back."""
        if not self.installed:
            return
        for address, data in self._original.items():
            self._write(address, data)
        self._write(PARAMS + 0x100, bytes(8))  # a frame can end inside fixed_sort_sprite, which sets this offset back
        self._sync()
        self._restore_native_ring()
        state = bytearray(self.core.serialize())
        offset = gte_projection_offset(state)
        state[offset:offset + 12] = self._native_gte
        self.core.unserialize(bytes(state))
        self._original.clear()
        self.installed = False
        if release:
            self.release_memory()


class FrameController:
    """Called between frames: the frame is on while walking around, and the game's own camera everywhere else."""

    def __init__(self, core, notice, frame_scale=DOUBLE_FRAME):
        self.core, self.notice, self.frame_scale = core, notice, frame_scale
        self.forget()

    @property
    def active(self) -> bool:
        return self.proof.installed

    def forget(self):
        """The RAM was replaced (a state was loaded): nothing of ours is in it."""
        self.proof = FixedFramingProof(self.core, self.frame_scale)
        self._calm = 0
        self._refused_on = None

    def _read(self, address, size):
        return self.core.read_memory(ram_offset(address), size)

    def tick(self):
        if self.proof.installed:
            try:
                self.proof.tick()
            except ValueError:
                self.suspend()
            return
        if any(self._read(GAME_STATE, 1)) or any(self._read(SCRIPT_MAP_CHANGE_STATE, 2)):
            self._calm = 0
            self._refused_on = None
            return
        screen = self._read(CURRENT_SCREEN, 1)
        if screen == self._refused_on:
            return
        self._calm += 1
        reservation = self.proof.reservation
        held = saved_hold(self.core) if reservation is None else reservation.active
        if self._calm < (RETURN_FRAMES if held else SETTLE_FRAMES):
            return
        try:
            self.proof.install()
        except (ValueError, LibretroError, struct.error) as exc:
            if self._calm < SETTLE_FRAMES:
                return  # a map may still be loading: try again next frame, and only then call it final
            self._refused_on = screen
            self.notice(f"Normal camera here: {exc}")

    def suspend(self, for_save=False):
        """The game's own code and camera. The frame returns straight after a save, and later after anything else.
        The memory stays held, in saves too: it costs the game nothing and can't be reclaimed once it's been used."""
        self._calm = SETTLE_FRAMES if for_save else 0
        try:
            if self.proof.installed:
                self.proof.uninstall(release=False)
        except (ValueError, LibretroError) as exc:
            self.notice(f"×2 camera: {exc}")
