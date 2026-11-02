from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .errors import DiscFormatError, ValidationError

DECODER_VERSION = "tim-v1"
MAX_PIXELS = 4096 * 4096


@dataclass(frozen=True)
class TimImage:
    image: Image.Image
    mode: int
    bits_per_pixel: int
    image_x: int
    image_y: int
    clut_x: int | None
    clut_y: int | None
    palette_count: int
    stp_present: bool
    consumed: int


def _color(value: int) -> tuple[int, int, int, int]:
    r, g, b = ((value >> shift) & 31 for shift in (0, 5, 10))
    # PS1 transparency is contextual. Preserve the useful conventional mapping:
    # zero = transparent; STP colors = 128 alpha; other colors = opaque.
    alpha = 0 if value == 0 else (128 if value & 0x8000 else 255)
    return ((r * 255 + 15) // 31, (g * 255 + 15) // 31, (b * 255 + 15) // 31, alpha)


def decode_tim(data: bytes, palette_index: int = 0) -> TimImage:
    if len(data) < 20 or data[:4] != b"\x10\x00\x00\x00":
        raise DiscFormatError("Not a PlayStation TIM image.")
    flags = struct.unpack_from("<I", data, 4)[0]
    if flags & ~0x0F or (flags & 7) > 3:
        raise DiscFormatError(f"Unsupported TIM flags 0x{flags:08x}.")
    mode, has_clut, pos = flags & 7, bool(flags & 8), 8
    if mode in (0, 1) and not has_clut:
        raise DiscFormatError("Indexed TIM image has no CLUT.")
    palette: list[int] = []
    clut_x = clut_y = None
    palette_count = 0
    if has_clut:
        if pos + 12 > len(data):
            raise DiscFormatError("Truncated TIM CLUT header.")
        block, clut_x, clut_y, colors, palette_count = struct.unpack_from("<IHHHH", data, pos)
        if block < 12 or pos + block > len(data) or block != 12 + colors * palette_count * 2:
            raise DiscFormatError("Malformed TIM CLUT block length.")
        if palette_index < 0 or palette_index >= palette_count:
            raise ValidationError(f"Palette {palette_index} is outside 0..{palette_count - 1}.")
        all_colors = struct.unpack_from(f"<{colors * palette_count}H", data, pos + 12)
        palette = list(all_colors[palette_index * colors:(palette_index + 1) * colors])
        pos += block
    if pos + 12 > len(data):
        raise DiscFormatError("Truncated TIM image header.")
    block, x, y, width_words, height = struct.unpack_from("<IHHHH", data, pos)
    expected = 12 + width_words * 2 * height
    if block != expected or pos + block > len(data) or not width_words or not height:
        raise DiscFormatError("Malformed or truncated TIM pixel block.")
    widths = (width_words * 4, width_words * 2, width_words, width_words * 2 // 3)
    width = widths[mode]
    if width <= 0 or width * height > MAX_PIXELS:
        raise DiscFormatError("TIM dimensions exceed the preview safety limit.")
    pixels = data[pos + 12:pos + block]
    rgba: list[tuple[int, int, int, int]] = []
    if mode == 0:
        for byte in pixels:
            rgba.extend((_color(palette[byte & 15]), _color(palette[byte >> 4])))
    elif mode == 1:
        rgba = [_color(palette[index]) for index in pixels]
    elif mode == 2:
        rgba = [_color(value) for value in struct.unpack(f"<{len(pixels)//2}H", pixels)]
    else:
        row_bytes = width_words * 2
        for row in range(height):
            line = pixels[row * row_bytes:(row + 1) * row_bytes]
            for i in range(0, width * 3, 3):
                rgba.append((line[i], line[i + 1], line[i + 2], 255))
    rgba = rgba[:width * height]
    if len(rgba) != width * height:
        raise DiscFormatError("TIM pixel data does not match its dimensions.")
    image = Image.new("RGBA", (width, height))
    image.putdata(rgba)
    return TimImage(image, mode, (4, 8, 16, 24)[mode], x, y, clut_x, clut_y,
                    palette_count, any(value & 0x8000 for value in palette), pos + block)


def export_png(tim: TimImage, destination: Path, source_id: str, source_hash: str,
               palette_index: int = 0, overwrite: bool = False) -> tuple[Path, Path]:
    destination = destination.resolve()
    metadata_path = destination.with_suffix(destination.suffix + ".json")
    if not overwrite and (destination.exists() or metadata_path.exists()):
        raise ValidationError("PNG or metadata export already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tim.image.save(destination, "PNG")
    metadata = {
        "source_resource_id": source_id, "source_sha256": source_hash,
        "decoder": "dw1-workbench TIM", "decoder_version": DECODER_VERSION,
        "bits_per_pixel": tim.bits_per_pixel, "palette_index": palette_index,
        "palette_count": tim.palette_count, "stp_semantics": "alpha 128 marks STP; zero word alpha 0",
        "image_vram": {"x": tim.image_x, "y": tim.image_y},
        "clut_vram": None if tim.clut_x is None else {"x": tim.clut_x, "y": tim.clut_y},
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return destination, metadata_path
