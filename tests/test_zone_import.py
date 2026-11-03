from __future__ import annotations

import hashlib
import json
import zipfile

from dw1_workbench.zone_export import export_package
from dw1_workbench.zone_import import OverrideStore, check_package
from test_pathing import two_maps
from test_zone_export import sources


def exported(tmp_path):
    entries, first, second = two_maps()
    every = sources(entries, first, second)
    export_package(tmp_path / "pkg", ["Test Woods", "Test Town"], every, {0: first, 1: second}, entries, {}, {},
                   {"executable": "SLUS_010.32", "source_sha256": "disc"})
    originals = {index: {"MAP": {"content_hash": source.map_row["content_hash"], "size": len(source.map_bytes)},
                         "TFS": {"content_hash": source.tfs_row["content_hash"], "size": len(source.tfs_bytes)}}
                 for index, source in every.items()}
    room = {index: {"MAP": 2048, "TFS": 2048 * 40} for index in every}
    return entries, tmp_path / "pkg.dw1zone", originals, room


def rewrite(package, target, name, data, fix_manifest=True):
    with zipfile.ZipFile(package) as zin, zipfile.ZipFile(target, "w") as zout:
        manifest = json.loads(zin.read("manifest.json"))
        for info in zin.infolist():
            if info.filename not in (name, "manifest.json"):
                zout.writestr(info.filename, zin.read(info.filename))
        zout.writestr(name, data)
        if fix_manifest:
            for entry in manifest["files"]:
                if entry["path"] == name:
                    entry["sha256"], entry["size"] = hashlib.sha256(data).hexdigest(), len(data)
        zout.writestr("manifest.json", json.dumps(manifest))
    return target


META = {"executable": "SLUS_010.32", "source_sha256": "disc"}


def test_unchanged_package_imports_nothing(tmp_path, monkeypatch):
    entries, package, originals, room = exported(tmp_path)
    # the export tests use stand-in MAP bytes, so parsing returns the scene they stand for
    monkeypatch.setattr("dw1_workbench.zone_import.parse_map", lambda data, entry: two_maps()[1])
    report = check_package(package, META, entries, originals, room)
    assert report.ok and report.changed_maps == []
    assert [item.status for item in report.maps] == [{"MAP": "unchanged", "TFS": "unchanged"}] * 2
    store = OverrideStore(tmp_path / "project")
    assert store.apply(report) == [] and store.records() == {}


def test_changed_map_is_stored_replaced_and_removed(tmp_path, monkeypatch):
    entries, package, originals, room = exported(tmp_path)
    # stand-in MAP bytes are not a real MAP, so let parsing accept whatever the package holds
    monkeypatch.setattr("dw1_workbench.zone_import.parse_map", lambda data, entry: two_maps()[1])
    edited = rewrite(package, tmp_path / "edited.dw1zone", "maps/000-TEST01/TEST01.MAP", b"EDITED")
    report = check_package(edited, META, entries, originals, room)
    assert report.ok and [(item.name, item.changed) for item in report.changed_maps] == [("TEST01", ["MAP"])]
    store = OverrideStore(tmp_path / "project")
    store.apply(report)
    assert store.file(0, "MAP") == b"EDITED"
    assert store.replacements(entries) == {"MAP/MAP1/TEST01.MAP": b"EDITED"}
    store.apply(check_package(package, META, entries, originals, room))  # the unchanged package restores the disc
    assert store.records() == {} and not (store.directory / "000-TEST01.MAP").exists()


def test_problems_block_the_import(tmp_path, monkeypatch):
    entries, package, originals, room = exported(tmp_path)
    monkeypatch.setattr("dw1_workbench.zone_import.parse_map", lambda data, entry: two_maps()[1])
    too_big = rewrite(package, tmp_path / "big.dw1zone", "maps/000-TEST01/TEST01.MAP", bytes(4096))
    report = check_package(too_big, META, entries, originals, room)
    assert not report.ok and "holds 2,048" in report.maps[0].problems[0]
    other = check_package(package, {"executable": "SLUS_010.32", "source_sha256": "other"}, entries, originals, room)
    assert "different disc image" in other.problems[0]


def test_hand_edited_files_without_a_manifest_update_are_imported(tmp_path, monkeypatch):
    entries, package, originals, room = exported(tmp_path)
    monkeypatch.setattr("dw1_workbench.zone_import.parse_map", lambda data, entry: two_maps()[1])
    edited = rewrite(package, tmp_path / "edited.dw1zone", "maps/000-TEST01/TEST01.MAP", b"HEXEDIT", fix_manifest=False)
    edited = rewrite(edited, tmp_path / "edited2.dw1zone", "maps/000-TEST01/pathing.png", b"scribbles", fix_manifest=False)
    report = check_package(edited, META, entries, originals, room)
    assert report.ok and report.maps[0].changed == ["MAP"]
    assert "TEST01.MAP edited in the package" in report.maps[0].notes
    assert any(note.startswith("pathing.png edited but ignored") for note in report.maps[0].notes)


def test_edited_background_picture_becomes_the_tfs(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from dw1_workbench.maps import parse_tfs, render_background
    entries, package, originals, room = exported(tmp_path)
    scene = two_maps()[1]
    monkeypatch.setattr("dw1_workbench.zone_import.parse_map", lambda data, entry: scene)
    with zipfile.ZipFile(package) as archive:
        picture = Image.open(io.BytesIO(archive.read("maps/000-TEST01/background-palette0.png"))).convert("RGBA")
    picture = picture.resize((picture.width * 2, picture.height * 2))  # a repaint at a larger size
    for x in range(40, 80):
        for y in range(40, 80):
            picture.putpixel((x, y), (0, 0, 255, 255))
    buffer = io.BytesIO(); picture.save(buffer, "PNG")
    edited = rewrite(package, tmp_path / "painted.dw1zone", "maps/000-TEST01/background-palette0.png", buffer.getvalue(),
                     fix_manifest=False)
    report = check_package(edited, META, entries, originals, room)
    item = report.maps[0]
    assert report.ok and item.changed == ["TFS"], item.problems
    assert any("background repainted" in note and "scaled from 512×256" in note for note in item.notes)
    tfs = parse_tfs(item.files["TFS"])
    assert len(item.files["TFS"]) == originals[0]["TFS"]["size"]
    assert render_background(tfs, scene.setup, 0).getpixel((30, 30))[:3] == (0, 0, 255)


def tim(depth, x, width_units, height, pixels, clut=None):
    import struct
    flags = depth | (8 if clut else 0)
    out = struct.pack("<II", 0x10, flags)
    if clut:
        cx, cy, words = clut
        out += struct.pack("<IHHHH", 12 + 2 * len(words), cx, cy, len(words), 1) + struct.pack(f"<{len(words)}H", *words)
    return out + struct.pack("<IHHHH", 12 + len(pixels), x, 0, width_units, height) + pixels


def test_foreground_pieces_are_repainted_or_kept():
    from types import SimpleNamespace
    from dw1_workbench.background_import import _repaint_foreground
    from dw1_workbench.maps import SpriteInstance, SpriteObject
    # 8-bit image at VRAM x 384 (page 0), 4 texels × 2 rows, background palette
    eight = tim(1, 384, 2, 2, bytes([5, 7, 9, 9, 5, 5, 9, 7]))
    # 4-bit image at x 448 (4-bit page 1), 4 texels × 1 row, with a 16-colour CLUT row: 0 transparent, 1 red, 2 blue
    four = tim(0, 448, 1, 1, bytes([0x21, 0x02]), clut=(0, 486, [0, 0x001F, 0x7C00] + [0x03E0] * 13))
    data = eight + four
    scene = SimpleNamespace(
        image_offsets=(0,), object_image_offsets=(len(eight),),
        objects=(SpriteObject((0, 0), (2, 2), (0, 0, 0), 0xFFFF, 4),   # static, background palette
                 SpriteObject((256, 0), (4, 1), (0, 0, 0), 0, 4),      # static, own 4-bit palette
                 SpriteObject((2, 0), (2, 2), (0, 0, 0), 0xFFFF, 4)),  # animated with object 0's frame
        instances=(SpriteInstance(((0, 1),), (10, 20), 0), SpriteInstance(((1, 1),), (50, 60), 0),
                   SpriteInstance(((2, 1), (0, 1)), (90, 90), 0)))
    old_words = [0x7FFF] * 256; old_words[7] = 0; old_words[9] = 0x001F
    new_words = [0] + [0x03E0] * 254 + [0x001F]
    # the original background under the 4-bit piece looks like its art (red, blue, blue), so it is a cut-out
    under = lambda x, y: (255, 0, 0) if x == 50 else (0, 0, 255)
    out, counts, reasons, remapped = _repaint_foreground(data, scene, 1, old_words, new_words, lambda x, y: 42,
                                                         lambda x, y: (0, 0, 255), under)
    # object 0 is shown by the animated instance too, so both of its pieces keep their art (nearest colours)
    assert reasons["animated"] == 2 and counts["background palette"] == 0
    assert out[20:28] == bytes([1, 0, 255, 255, 1, 1, 255, 0])
    # the 4-bit piece is repainted blue (index 2) where it was not transparent
    four_pixels = out[len(eight) + 8 + 12 + 32 + 12:]
    assert four_pixels == bytes([0x22, 0x02]) and counts["own palette"] == 4 and remapped == 8

    _, counts, reasons, _ = _repaint_foreground(data, scene, 1, old_words, new_words, lambda x, y: 42,
                                                lambda x, y: (0, 0, 255), lambda x, y: (0, 255, 0))
    assert reasons["separate art"] == 1 and counts["own palette"] == 0  # art not found in the background is kept
