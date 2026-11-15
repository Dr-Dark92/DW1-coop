"""Bounded WebP metadata/timeline and single-worker frame decoding.

RIFF ANIM/ANMF fields: developers.google.com/speed/webp/docs/riff_container.
Pillow/libwebp performs actual pixel decoding, blending and disposal on the full canvas.
"""
from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path
import struct
import math

import numpy as np
from PIL import Image

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ANIMATION_PIXELS = 8 * 1024 * 1024
MAX_FRAMES = 600
MIN_FRAME_MS = 10  # preserve 17ms/60Hz clips; normalize only zero/very short durations to avoid spin


@dataclass(frozen=True)
class AnimationInfo:
    durations: tuple[int, ...]
    loops: int  # 0 infinite; otherwise total play count, as stored in ANIM
    ends: tuple[int, ...] = field(init=False)

    def __post_init__(self):
        if not self.durations or len(self.durations) > MAX_FRAMES or any(d <= 0 for d in self.durations):
            raise ValueError("Invalid background animation timeline")
        if not 0 <= self.loops <= 65535: raise ValueError("Invalid WebP loop count")
        object.__setattr__(self, "ends", tuple(accumulate(self.durations)))

    def frame_at(self, elapsed_ms):
        total = self.ends[-1]
        elapsed_ms = max(0, int(elapsed_ms))
        if self.loops and elapsed_ms >= total * self.loops:
            return len(self.durations) - 1
        return bisect_right(self.ends, elapsed_ms % total)


def webp_animation(data: bytes) -> AnimationInfo | None:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Background exceeds the 64 MiB compressed-file limit")
    if not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        return None
    if len(data) < 12 or struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("Malformed WebP RIFF extent")
    position, loops, durations = 12, None, []
    while position < len(data):
        if position + 8 > len(data): raise ValueError("Truncated WebP chunk header")
        tag, size = struct.unpack_from("<4sI", data, position)
        start = position + 8
        stop = start + size
        if stop + (size & 1) > len(data): raise ValueError("WebP chunk exceeds its container")
        if size & 1 and data[stop] != 0: raise ValueError("Invalid WebP chunk padding")
        if tag == b"ANIM":
            if loops is not None or size != 6: raise ValueError("Invalid WebP animation header")
            loops = struct.unpack_from("<H", data, start + 4)[0]
        elif tag == b"ANMF":
            if loops is None or size < 16: raise ValueError("Invalid WebP animation frame header")
            durations.append(max(MIN_FRAME_MS, int.from_bytes(data[start + 12:start + 15], "little")))
            if len(durations) > MAX_FRAMES: raise ValueError("Animated background exceeds 600 frames; shorten the clip")
        position = stop + (size & 1)
    if loops is not None:
        if not durations: raise ValueError("Animated WebP has no frames")
        return AnimationInfo(tuple(durations), loops)
    return None


@dataclass(frozen=True)
class AnimatedFrame:
    image: Image.Image  # immutable by ownership: loader never modifies this full composited source canvas
    animation: AnimationInfo
    index: int
    target_size: tuple[int, int]

    @property
    def nbytes(self):
        return self.image.width * self.image.height * 4

    def window(self, camera_x, camera_y, scale):
        width, height = 320 * scale, 240 * scale
        sx, sy = self.image.width / self.target_size[0], self.image.height / self.target_size[1]
        box = (camera_x * scale * sx, camera_y * scale * sy,
               (camera_x * scale + width) * sx, (camera_y * scale + height) * sy)
        # Only resample the visible screen; never resample a multi-megapixel whole map every animation tick.
        x0, y0 = max(0, math.ceil(-box[0] / sx)), max(0, math.ceil(-box[1] / sy))
        x1, y1 = min(width, math.floor((self.image.width - box[0]) / sx)), min(height, math.floor((self.image.height - box[1]) / sy))
        if x0 == y0 == 0 and x1 == width and y1 == height:
            image = self.image.resize((width, height), Image.Resampling.BICUBIC, box)
        else:
            image = Image.new("RGBA", (width, height))
            if x1 > x0 and y1 > y0:
                crop = (box[0] + x0 * sx, box[1] + y0 * sy, box[0] + x1 * sx, box[1] + y1 * sy)
                image.paste(self.image.resize((x1 - x0, y1 - y0), Image.Resampling.BICUBIC, crop), (x0, y0))
        return np.frombuffer(image.tobytes("raw", "BGRA"), "<u4").reshape(height, width)


class BackgroundDecoder:
    """Owned exclusively by PictureCache's one loader thread; only one open decoder at a time."""
    def __init__(self, cancelled=lambda: False, limit=128 * 1024 * 1024):
        self.cancelled = cancelled
        self.limit = limit
        self.opened = self.identity = self.animation = None
        self.independent = None
        self.data = None

    def close(self):
        if self.opened is not None: self.opened.close()
        self.opened = self.identity = self.animation = None
        self.independent = self.data = None

    def load(self, path, width, height, scale, frame_index=0):
        path = Path(path)
        stat = path.stat()
        identity = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        if identity != self.identity:
            self.close()
            if stat.st_size > MAX_FILE_BYTES: raise ValueError("Background exceeds 64 MiB; reduce the file size")
            # Metadata parsing is bounded and happens only in the loader, never on the emulator thread.
            with path.open("rb") as source:
                data = source.read(MAX_FILE_BYTES + 1)
            animation = webp_animation(data)
            from io import BytesIO
            opened = Image.open(BytesIO(data))
            try:
                pixels = opened.width * opened.height
                if pixels > (MAX_ANIMATION_PIXELS if animation else 32 * 1024 * 1024):
                    raise ValueError("Background dimensions exceed preview limit (animated: 8 megapixels)")
                if animation and (opened.format != "WEBP" or opened.n_frames != len(animation.durations)):
                    raise ValueError("WebP frame table disagrees with decoder")
                if getattr(opened, "n_frames", 1) > 1 and animation is None:
                    raise ValueError("Animated backgrounds require valid WebP animation metadata")
            except Exception:
                opened.close(); raise
            self.opened, self.identity, self.animation = opened, identity, animation
            if animation and opened.mode == "RGB":
                # Full-canvas VP8 frames WITHOUT ALPH have no blend/disposal dependency.
                # VP8L may hide alpha deltas even when the animation's final canvas is RGB: never shortcut it.
                ranges, position = [], 12
                while position < len(data):
                    tag, size = struct.unpack_from("<4sI", data, position)
                    start = position + 8
                    if tag == b"ANMF":
                        x, y, w, h = (int.from_bytes(data[start + at:start + at + 3], "little") for at in (0, 3, 6, 9))
                        if (x, y, w + 1, h + 1) != (0, 0, opened.width, opened.height) or data[start + 16:start + 20] != b"VP8 ":
                            ranges = []; break
                        ranges.append((start + 16, start + size))
                    position += 8 + size + (size & 1)
                if ranges:
                    self.independent, self.data = ranges, data
        if self.cancelled(): raise ValueError("Background decoding cancelled")
        index = frame_index if self.animation else 0
        if not 0 <= index < (len(self.animation.durations) if self.animation else 1):
            raise ValueError("Background frame index outside clip")
        if self.independent is not None:
            from io import BytesIO
            start, stop = self.independent[index]
            payload = self.data[start:stop]
            with Image.open(BytesIO(b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WEBP" + payload)) as frame:
                image = frame.convert("RGBA")
            return AnimatedFrame(image, self.animation, index, (width * 128 * scale, height * 128 * scale))
        if index < self.opened.tell(): self.opened.seek(0)
        for step in range(self.opened.tell(), index + 1):
            if self.cancelled(): raise ValueError("Background decoding cancelled")
            self.opened.seek(step); self.opened.load()
        target_size = (width * 128 * scale, height * 128 * scale)
        if self.animation:
            image = self.opened.convert("RGBA")
            if image.width * image.height * 4 > self.limit:
                raise ValueError("Animation frame exceeds the decoded cache limit")
            return AnimatedFrame(image, self.animation, index, target_size)
        if target_size[0] * target_size[1] * 4 > self.limit:
            raise ValueError("Resampled picture exceeds the decoded cache limit")
        image = self.opened.convert("RGB").resize(target_size, Image.Resampling.LANCZOS)
        rgb = np.asarray(image, np.uint8)
        packed = np.full(rgb.shape[:2], 0 if self.animation else 0xFF000000, np.uint32)
        for channel, shift in ((0, 16), (1, 8), (2, 0)) + (((3, 24),) if self.animation else ()):
            packed |= rgb[..., channel].astype(np.uint32) << shift
        packed.setflags(write=False)
        return packed
