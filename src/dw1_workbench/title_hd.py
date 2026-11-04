"""The title pictures at full size: the game still shows its 320-wide copy, and the player draws the full picture over it.

A frame counts as a title screen by matching what the game has in that file, at any brightness since the game fades it. Only
pixels that match are replaced, so whatever the game draws on top (Press Start, the menu) stays.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .hd_background import OUTPUT_SCALE, Composed
from .tim import decode_tim
from .title_screens import SHOWN_ROWS, TITLE_SCREENS, TitleStore

SCREEN_W, SCREEN_H = 320, SHOWN_ROWS
BRIGHT = 64  # a channel this bright says how far the screen is faded
DARKEST = 0.03  # darker than this the screen is black, whatever it is showing
CLOSE = 12  # 8-bit levels: the game's texture sampling and fades round a little
TITLE_SHARE = 0.6  # share of the screen that must match
PROBE_STEP = 16  # checked on this sparse grid first: most frames aren't a title


def _fade(small: np.ndarray, expected: np.ndarray) -> float | None:
    """How bright the frame is against the picture, or None if nothing in the picture can tell."""
    bright = expected.max(axis=2) >= BRIGHT
    if not bright.any():
        return None
    channel = expected.argmax(axis=2)
    rows, columns = np.nonzero(bright)
    chosen = channel[rows, columns]
    return float(np.median(small[rows, columns, chosen] / expected[rows, columns, chosen]))


def match_map(small: np.ndarray, expected: np.ndarray, fade: float) -> np.ndarray:
    """Which pixels are the picture. One column either way: the game stretches 255 texels over 256 pixels."""
    wanted = expected * fade
    close = np.abs(small - wanted).max(axis=2) <= CLOSE
    close[:, 1:] |= np.abs(small[:, 1:] - wanted[:, :-1]).max(axis=2) <= CLOSE
    close[:, :-1] |= np.abs(small[:, :-1] - wanted[:, 1:]).max(axis=2) <= CLOSE
    return close


def _without_specks(match: np.ndarray) -> np.ndarray:
    """A lone pixel that didn't match is rounding, not something drawn on top."""
    other = ~match
    padded = np.pad(other, 1).astype(np.uint8)
    around = sum(np.roll(np.roll(padded, y, 0), x, 1) for y in (-1, 0, 1) for x in (-1, 0, 1))[1:-1, 1:-1]
    return match | (other & (around <= 2))


@dataclass(frozen=True)
class TitlePicture:
    expected: np.ndarray  # SCREEN_H x SCREEN_W x 3 floats, what the game draws
    path: Path


def _pixels(frame: np.ndarray) -> np.ndarray:
    return np.stack(((frame >> 16) & 255, (frame >> 8) & 255, frame & 255), axis=-1).astype(np.float32)


class TitleLayer:
    def __init__(self, pictures: list[TitlePicture], scale: int = OUTPUT_SCALE):
        self.pictures, self.scale = pictures, scale
        self._loaded: dict[Path, np.ndarray] = {}

    @classmethod
    def from_store(cls, store: TitleStore) -> "TitleLayer | None":
        """None when no title has a full-size picture."""
        pictures = []
        for screen in TITLE_SCREENS:
            tim, path = store.get(screen), store.picture_path(screen)
            if tim is not None and path.is_file():
                image = decode_tim(tim).image.convert("RGB").crop((0, 0, SCREEN_W, SCREEN_H))
                pictures.append(TitlePicture(np.asarray(image, np.float32), path))
        return cls(pictures) if pictures else None

    def prepare(self, frame: np.ndarray):
        """What `compose` needs for this frame (uint32 0x00RRGGBB), or None when it isn't a title screen."""
        height, width = frame.shape
        factor = width // SCREEN_W
        if not factor or width % SCREEN_W or height != SCREEN_H * factor or factor > self.scale or self.scale % factor:
            return None
        small = _pixels(frame[::factor, ::factor])
        for picture in self.pictures:
            step = PROBE_STEP
            fade = _fade(small[::step, ::step], picture.expected[::step, ::step])
            if fade is None or fade < DARKEST:
                continue
            if match_map(small[::step, ::step], picture.expected[::step, ::step], fade).mean() < TITLE_SHARE * 0.8:
                continue
            fade = _fade(small, picture.expected)
            match = match_map(small, picture.expected, fade)
            if match.mean() >= TITLE_SHARE:
                return frame, _without_specks(match), min(fade, 1.0), picture.path, factor
        return None

    def _picture(self, path: Path) -> np.ndarray:
        if path not in self._loaded:
            with Image.open(path) as image:
                sized = image.convert("RGB").resize((SCREEN_W * self.scale, SCREEN_H * self.scale), Image.LANCZOS)
            self._loaded[path] = np.asarray(sized, np.uint8)
        return self._loaded[path]

    def compose(self, job) -> Composed:
        frame, match, fade, path, factor = job
        picture = self._picture(path)
        lit = picture if fade >= 0.97 else ((picture.astype(np.uint16) * round(fade * 256)) >> 8).astype(np.uint8)
        pixels = (0xFF000000 | lit[..., 0].astype(np.uint32) << 16 | lit[..., 1].astype(np.uint32) << 8 | lit[..., 2])
        drawn = np.repeat(np.repeat(~match, self.scale, axis=0), self.scale, axis=1)
        upscale = self.scale // factor
        game = np.repeat(np.repeat(frame, upscale, axis=0), upscale, axis=1) | 0xFF000000
        pixels[drawn] = game[drawn]
        return Composed(pixels, float(match.mean()))
