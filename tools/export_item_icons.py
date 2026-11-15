"""Export USA DW1 item sprites using the executable's actual palette assignments.

Read-only disc access. Output must be a new directory outside the checkout.
Evidence: jype0/dw_decomp src/main/utils2.c setItemTexture/renderItemSprite,
src/main/item.c renderOverworldItem, config/us/symbols.txt (MIT; no code copied).
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

from dw1_workbench.characters import LOAD_ADDRESS
from dw1_workbench.disc import inspect_image
from dw1_workbench.errors import DiscFormatError
from dw1_workbench.iso9660 import read_inventory
from dw1_workbench.items import parse_item_names
from dw1_workbench.tim import decode_tim

PALETTE_OFFSET = 0x80127BDC - LOAD_ADDRESS
VERSION = "usa-item-icons-v1"


def decode_icons(executable, atlas):
    names = parse_item_names(executable)
    palettes = executable[PALETTE_OFFSET:PALETTE_OFFSET + 128]
    if len(palettes) != 128:
        raise DiscFormatError("Truncated USA item palette table")
    first = decode_tim(atlas)
    if (first.bits_per_pixel, first.image.size, first.image_x, first.image_y,
            first.clut_x, first.clut_y, first.palette_count) != (4, (256, 128), 320, 0, 224, 488, 24):
        raise DiscFormatError("Unsupported item atlas layout")
    if any(index >= first.palette_count for index in palettes):
        raise DiscFormatError("Item palette index outside atlas")
    decoded = {index: decode_tim(atlas, index).image for index in set(palettes)}
    for item_id, palette in enumerate(palettes):
        x, y = item_id % 16 * 16, item_id // 16 * 16
        yield item_id, names[item_id], palette, decoded[palette].crop((x, y, x + 16, y + 16))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("Output must be outside the checkout")
    with inspect_image(args.disc).open() as reader:
        entries = {entry.path: entry for entry in read_inventory(reader)}
        exe, atlas = entries.get("SLUS_010.32"), entries.get("ETCDAT/ITEM.TIM")
        if exe is None or atlas is None:
            parser.error("Requires USA SLUS_010.32 and ETCDAT/ITEM.TIM")
        executable = reader.read_extent(exe.extent, exe.size, 2 * 1024 * 1024)
        data = reader.read_extent(atlas.extent, atlas.size, 1024 * 1024)
    icons = list(decode_icons(executable, data))  # validate before creating output
    args.output.mkdir(parents=True, exist_ok=False)
    sheet = Image.new("RGBA", (16 * 80, 8 * 88), (28, 32, 42, 255))
    draw = ImageDraw.Draw(sheet)
    records = []
    for item_id, name, palette, icon in icons:
        filename = f"{item_id:03d}.png"
        icon.save(args.output / filename)
        x, y = item_id % 16 * 80, item_id // 16 * 88
        sheet.alpha_composite(icon.resize((48, 48), Image.Resampling.NEAREST), (x + 16, y))
        draw.text((x + 2, y + 50), f"{item_id:03d}", fill="white")
        draw.text((x + 2, y + 64), name[:12], fill="white")
        records.append(dict(item_id=item_id, name=name, palette_index=palette,
                            clut_xy=[224, 488 + palette], uv=[item_id % 16 * 16, item_id // 16 * 16],
                            png=filename, size=[16, 16],
                            native_uv_span=[15 if item_id % 16 == 15 else 16, 16]))
    sheet.save(args.output / "contact-sheet.png")
    metadata = dict(decoder=VERSION, source=str(args.disc.resolve()),
                    atlas=dict(path=atlas.path, extent=atlas.extent, size=atlas.size,
                               sha256=hashlib.sha256(data).hexdigest()),
                    executable=dict(path=exe.path, sha256=hashlib.sha256(executable).hexdigest(),
                                    palette_file_offset=PALETTE_OFFSET),
                    transparency="TIM zero transparent; STP alpha128 approximation, not PS1 framebuffer blending",
                    note="Full16x16 cells exported. Native last-column UV span15; ground may use15x15 when enlarged.",
                    items=records)
    (args.output / "items.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Exported {len(records)} item cells to {args.output}")


if __name__ == "__main__":
    main()
