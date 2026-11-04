"""The two title pictures: your own in place of the disc's, kept in the project and written to the patched disc.

Each is an 8-bit TIM of 320 wide with one 256-colour palette, the same size as the original so it fits its place on the disc.
TITLE256 is the Press Start screen. Only its top 240 rows are shown: the game draws its Press Start text from the rows below,
so those stay the disc's unless the new picture has the full height. The chosen picture is also kept at its own size, for
the player to draw over the game's 320-wide one (title_hd.py).
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .background_import import OPAQUE_BLACK
from .disc import inspect_image
from .iso9660 import read_inventory
from .quicksave import _atomic_write
from .tim import decode_tim

SHOWN_ROWS = 240
COLOURS = 256
PALETTE_AT = 20  # TIM header 8, palette block header 12
PALETTE_BYTES = COLOURS * 2
PIXELS_AT = PALETTE_AT + PALETTE_BYTES + 12  # image block header 12
SAME_SHAPE = 0.01  # how far a picture's shape may be from the whole file's and still count as it (rounding when scaled up)


@dataclass(frozen=True)
class TitleScreen:
    name: str
    label: str

    @property
    def disc_path(self) -> str:
        return f"ETCNA/{self.name}.TIM"


TITLE_SCREENS = (TitleScreen("TITLE256", "Press Start screen"), TitleScreen("TITLE2", "Menu background"))


def tim_size(tim: bytes) -> tuple[int, int]:
    """The picture's size in pixels, for an 8-bit TIM with one 256-colour palette; ValueError for anything else."""
    if len(tim) < PIXELS_AT or struct.unpack_from("<2I", tim) != (0x10, 9) or struct.unpack_from("<2H", tim, 16) != (COLOURS, 1):
        raise ValueError("Not an 8-bit title picture")
    width, height = struct.unpack_from("<2H", tim, PIXELS_AT - 4)
    if len(tim) < PIXELS_AT + width * 2 * height:
        raise ValueError("The title picture is cut short")
    return width * 2, height


def _fitted(picture: Image.Image, width: int, height: int) -> Image.Image:
    """Scaled to fill this size, cropping any overflow equally from both sides; not resampled when it is already the size."""
    if picture.size == (width, height):
        return picture
    scale = max(width / picture.width, height / picture.height)
    scaled = picture.resize((max(round(picture.width * scale), width), max(round(picture.height * scale), height)), Image.LANCZOS)
    left, top = (scaled.width - width) // 2, (scaled.height - height) // 2
    return scaled.crop((left, top, left + width, top + height))


def _whole_shape(picture: Image.Image, width: int, height: int) -> bool:
    return abs(picture.width * height / (picture.height * width) - 1) <= SAME_SHAPE


def shown_part(picture: Image.Image, original: bytes) -> Image.Image:
    """The part of `picture` that is on screen, at the picture's own size."""
    width, height = tim_size(original)
    shown = min(height, SHOWN_ROWS)
    flat = _flat(picture)
    if _whole_shape(flat, width, height):
        return flat.crop((0, 0, flat.width, round(flat.height * shown / height)))
    scale = min(flat.width / width, flat.height / shown)
    crop = round(width * scale), round(shown * scale)
    left, top = (flat.width - crop[0]) // 2, (flat.height - crop[1]) // 2
    return flat.crop((left, top, left + crop[0], top + crop[1]))


def _flat(picture: Image.Image) -> Image.Image:
    rgba = picture.convert("RGBA")
    flat = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    flat.alpha_composite(rgba)
    return flat.convert("RGB")


def _word(red: int, green: int, blue: int) -> int:
    """A palette colour; zero would draw as see-through, so black is its opaque form."""
    return (red >> 3 | (green >> 3) << 5 | (blue >> 3) << 10) or OPAQUE_BLACK


def encode_title(original: bytes, picture: Image.Image) -> bytes:
    """`picture` as a TIM in the shape of `original`, the disc's. Any picture of the whole shape replaces the hidden rows too."""
    width, height = tim_size(original)
    flat = _flat(picture)
    canvas = decode_tim(original).image.convert("RGB")
    if _whole_shape(flat, width, height):
        canvas = flat if flat.size == (width, height) else flat.resize((width, height), Image.LANCZOS)
        shown = height
    else:
        shown = min(height, SHOWN_ROWS)
        canvas.paste(_fitted(flat, width, shown), (0, 0))
    colours = canvas.getcolors(COLOURS)
    if colours is not None:  # nothing to lose: every colour keeps its place in the palette
        table = sorted(colour for _, colour in colours)
        mapping = {colour: index for index, colour in enumerate(table)}
        data = canvas.tobytes()
        pixels = bytes(mapping[tuple(data[at:at + 3])] for at in range(0, len(data), 3))
    else:
        palette = canvas.quantize(COLOURS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        raw = palette.getpalette()[:COLOURS * 3]
        table = [tuple(raw[at:at + 3]) for at in range(0, len(raw), 3)]
        shown_part = canvas.crop((0, 0, width, shown)).quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
        indexed = palette.copy()
        indexed.paste(shown_part, (0, 0))  # text in the hidden rows stays clean; only the picture is dithered
        pixels = indexed.tobytes()
    words = [_word(*colour) for colour in table] + [OPAQUE_BLACK] * (COLOURS - len(table))
    result = bytearray(original)
    struct.pack_into(f"<{COLOURS}H", result, PALETTE_AT, *words)
    result[PIXELS_AT:PIXELS_AT + len(pixels)] = pixels
    return bytes(result)


def disc_titles(disc: Path) -> dict[str, bytes]:
    with inspect_image(Path(disc)).open() as reader:
        inventory = {entry.path: entry for entry in read_inventory(reader)}
        return {screen.name: reader.read_extent(inventory[screen.disc_path].extent, inventory[screen.disc_path].size, 1024 * 1024)
                for screen in TITLE_SCREENS}


class TitleStore:
    """<project>/overrides/title: the pictures in use in place of the disc's. No file means the disc's own."""

    def __init__(self, overrides: Path):
        self.directory = Path(overrides) / "title"

    def _path(self, screen: TitleScreen) -> Path:
        return self.directory / f"{screen.name}.TIM"

    def get(self, screen: TitleScreen) -> bytes | None:
        path = self._path(screen)
        return path.read_bytes() if path.is_file() else None

    def picture_path(self, screen: TitleScreen) -> Path:
        return self.directory / f"{screen.name}.png"

    def set(self, screen: TitleScreen, tim: bytes, shown: Image.Image | None = None):
        """`shown`: the on-screen part at full size, for the player's HD layer."""
        tim_size(tim)
        _atomic_write(self._path(screen), tim)
        self.picture_path(screen).unlink(missing_ok=True)
        if shown is not None:
            buffer = io.BytesIO()
            shown.save(buffer, "PNG")
            _atomic_write(self.picture_path(screen), buffer.getvalue())

    def remove(self, screen: TitleScreen):
        self._path(screen).unlink(missing_ok=True)
        self.picture_path(screen).unlink(missing_ok=True)

    def count(self) -> int:
        return sum(self._path(screen).is_file() for screen in TITLE_SCREENS)

    def replacements(self) -> dict[str, bytes]:
        """ISO path -> bytes, for building a patched disc."""
        result = {}
        for screen in TITLE_SCREENS:
            tim = self.get(screen)
            if tim is not None:
                tim_size(tim)
                result[screen.disc_path] = tim
        return result
