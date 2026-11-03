from __future__ import annotations

import io
import json
import struct
import zipfile

import pytest
from PIL import Image

from dw1_workbench.alltim import SLOT_SIZE
from dw1_workbench.characters import CharacterEntry
from dw1_workbench.digimon_package import (DigimonImportError, DigimonOverrides, DigimonSource, digest,
                                           export_package, prepare_import, read_package)
from dw1_workbench.errors import Cancelled, DiscFormatError
from dw1_workbench.tim import decode_tim
from test_mmd import make_executable, make_mmd


def source():
    colors = [0, 31, 0x83E0, 31] + [0] * 12
    clut = struct.pack('<IHHHH', 12 + 64, 0, 488, 16, 2) + struct.pack('<32H', *(colors + colors))
    pixels = bytes([0x31] * 128)  # duplicate red entries: unchanged PNG must preserve native indices
    image = struct.pack('<IHHHH', 12 + len(pixels), 320, 0, 4, 16) + pixels
    tim = struct.pack('<II', 0x10, 8) + clut + image
    texture = tim + bytes([0xEA]) * (SLOT_SIZE - len(tim))
    model = make_mmd()
    character = CharacterEntry(3, 'AGUM', 0, b'AGUM\0\0\0\0', 'CHDAT/MMD0/AGUM.MMD', 3, 'Agumon', 0, 3)
    model_row = {'id': 'model-3', 'original_path': character.model_path, 'parent_path': None,
                 'offset': None, 'extent': 40, 'size': len(model), 'content_hash': digest(model)}
    texture_row = {'id': 'texture-3', 'original_path': 'CHDAT/ALLTIM.TIM#003', 'parent_path': 'CHDAT/ALLTIM.TIM',
                   'offset': SLOT_SIZE * 3, 'extent': 50, 'size': SLOT_SIZE, 'content_hash': digest(texture)}
    return DigimonSource(character, make_executable(), model, texture, model_row, texture_row,
                         {'executable': 'SLUS_010.32', 'source_sha256': 'a' * 64, 'release': 'USA synthetic'})


def exported(tmp_path):
    original = source()
    path = export_package(tmp_path / 'agum', original)
    return original, path


def rewrite(path, destination, updates):
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            zout.writestr(name, updates.get(name, zin.read(name)))
    return destination


def edited_png(original, color=(0, 255, 0, 128)):
    image = decode_tim(original.texture).image
    image.putpixel((0, 0), color)
    buffer = io.BytesIO(); image.save(buffer, 'PNG')
    return buffer.getvalue()


def test_native_export_import_is_byte_identical_and_has_provenance(tmp_path):
    original, path = exported(tmp_path)
    manifest, members, edited = read_package(path)
    assert members['model.MMD'] == original.model
    assert members['texture.TIM'] == original.texture
    assert edited == set()
    assert manifest['resources']['TIM']['offset'] == 3 * SLOT_SIZE
    assert manifest['character']['resource_id'] == 3
    assert len(json.loads(members['skeleton.json'])['nodes']) == 2
    report = prepare_import(path, original)
    assert report.changed == ()
    store = DigimonOverrides(tmp_path / 'project', original.meta['source_sha256'])
    store.apply(report)
    assert store.records() == {}
    with pytest.raises(DigimonImportError, match='already exists'):
        export_package(path, original)


def test_png_edits_preserve_palettes_padding_and_unedited_duplicate_indices(tmp_path):
    original, path = exported(tmp_path)
    package = rewrite(path, tmp_path / 'painted.dw1digimon', {'texture.png': edited_png(original)})
    report = prepare_import(package, original)
    assert report.changed == ('TIM',)
    tim = decode_tim(report.texture)
    assert tim.image.getpixel((0, 0)) == (0, 255, 0, 128)
    assert tim.image.getpixel((1, 0)) == (255, 0, 0, 255)
    # Only the low nibble of the first pixel byte changed. Every CLUT row and padding byte is retained.
    changed = [i for i, (a, b) in enumerate(zip(report.texture, original.texture)) if a != b]
    assert changed == [8 + 12 + 64 + 12]
    assert report.texture[changed[0]] == 0x32


def test_override_persistence_slot_isolation_restore_and_hash_check(tmp_path):
    original, path = exported(tmp_path)
    package = rewrite(path, tmp_path / 'edited.dw1digimon', {'texture.png': edited_png(original)})
    report = prepare_import(package, original)
    store = DigimonOverrides(tmp_path / 'project', original.meta['source_sha256'])
    store.apply(report)
    reopened = DigimonOverrides(tmp_path / 'project', original.meta['source_sha256'])
    assert reopened.file(3, 'TIM') == report.texture
    alltim = bytearray(180 * SLOT_SIZE)
    alltim[3 * SLOT_SIZE:4 * SLOT_SIZE] = original.texture
    replacements = reopened.replacements(bytes(alltim), {3: original.model_row})
    merged = replacements['CHDAT/ALLTIM.TIM']
    assert merged[:3 * SLOT_SIZE] == alltim[:3 * SLOT_SIZE]
    assert merged[3 * SLOT_SIZE:4 * SLOT_SIZE] == report.texture
    assert merged[4 * SLOT_SIZE:] == alltim[4 * SLOT_SIZE:]
    record = reopened.records()[3]
    assert record['files']['TIM']['source']['content_hash'] == digest(original.texture)
    # An unchanged package removes the older override, restoring original disc content.
    reopened.apply(prepare_import(path, original))
    assert reopened.records() == {}
    reopened.apply(report)
    blob = reopened.directory / reopened.records()[3]['files']['TIM']['file']
    blob.write_bytes(b'corrupt')
    with pytest.raises(DigimonImportError, match='changed on disk'):
        reopened.file(3, 'TIM')


def test_compatible_native_vertex_edit_and_layout_rejection(tmp_path):
    original, path = exported(tmp_path)
    model = bytearray(original.model)
    vertex_offset = 20 + struct.unpack_from('<I', model, 20)[0]
    struct.pack_into('<h', model, vertex_offset, 5)
    package = rewrite(path, tmp_path / 'vertex.dw1digimon', {'model.MMD': bytes(model)})
    report = prepare_import(package, original)
    assert report.changed == ('MMD',)
    assert struct.unpack_from('<h', report.model, vertex_offset)[0] == 5
    package = rewrite(path, tmp_path / 'larger.dw1digimon', {'model.MMD': bytes(model) + b'1234'})
    with pytest.raises(DigimonImportError, match='byte length'):
        prepare_import(package, original)
    moved = bytearray(original.model)
    # Valid in-bounds vertices read from the old normal array, but executable allocation/layout stays fixed.
    struct.pack_into('<I', moved, 20, struct.unpack_from('<I', moved, 28)[0])
    package = rewrite(path, tmp_path / 'layout.dw1digimon', {'model.MMD': bytes(moved)})
    with pytest.raises(DigimonImportError, match='header/object layout'):
        prepare_import(package, original)


@pytest.mark.parametrize('bad', ['wrong-disc', 'wrong-character', 'missing', 'traversal', 'duplicate', 'version'])
def test_bad_packages_are_rejected_before_import(tmp_path, bad):
    original, path = exported(tmp_path)
    manifest, files, _ = read_package(path)
    if bad == 'wrong-disc': manifest['source']['source_sha256'] = 'b' * 64
    if bad == 'wrong-character': manifest['character']['resource_id'] = 4
    if bad == 'version': manifest['version'] = 999
    target = tmp_path / 'bad.dw1digimon'
    with zipfile.ZipFile(target, 'w') as archive:
        for name, data in files.items():
            if bad == 'missing' and name == 'model.MMD': continue
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest))
        if bad == 'traversal': archive.writestr('../escape.MMD', b'bad')
        if bad == 'duplicate':
            with pytest.warns(UserWarning, match='Duplicate'):
                archive.writestr('model.MMD', b'bad')
    with pytest.raises(DigimonImportError):
        prepare_import(target, original)
    assert not (tmp_path / 'escape.MMD').exists()


def test_texture_conflicts_wrong_dimensions_and_malformed_model(tmp_path):
    original, path = exported(tmp_path)
    replacement = bytearray(original.texture); replacement[20] ^= 1
    package = rewrite(path, tmp_path / 'both.dw1digimon', {'texture.TIM': bytes(replacement),
                                                          'texture.png': edited_png(original)})
    with pytest.raises(DigimonImportError, match='Both'):
        prepare_import(package, original)
    buffer = io.BytesIO(); Image.new('RGBA', (1, 1)).save(buffer, 'PNG')
    package = rewrite(path, tmp_path / 'size.dw1digimon', {'texture.png': buffer.getvalue()})
    with pytest.raises(DigimonImportError, match='native dimensions'):
        prepare_import(package, original)
    broken = bytearray(original.model); struct.pack_into('<I', broken, 4, 0xFFFFFFFF)
    package = rewrite(path, tmp_path / 'broken.dw1digimon', {'model.MMD': bytes(broken)})
    with pytest.raises((DiscFormatError, DigimonImportError)):
        prepare_import(package, original)


def test_cancel_after_staging_keeps_previous_index_and_cleans_new_blobs(tmp_path):
    original, path = exported(tmp_path)
    package = rewrite(path, tmp_path / 'edited.dw1digimon', {'texture.png': edited_png(original)})
    report = prepare_import(package, original)
    store = DigimonOverrides(tmp_path / 'project', original.meta['source_sha256'])
    store.apply(prepare_import(path, original))
    before = store.index.read_bytes()
    stop = False
    def progress(value, text):
        nonlocal stop
        stop = True
    with pytest.raises(Cancelled):
        store.apply(report, cancelled=lambda: stop, progress=progress)
    assert store.index.read_bytes() == before
    assert list(store.directory.glob('*.TIM')) == []
    assert list(store.directory.glob('*.partial')) == []


def test_export_cancellation_and_failed_index_write_leave_no_partial_apply(tmp_path, monkeypatch):
    original = source()
    stop = False
    def progress(value, text):
        nonlocal stop
        if value >= 30: stop = True
    with pytest.raises(Cancelled):
        export_package(tmp_path / 'cancelled', original, cancelled=lambda: stop, progress=progress)
    assert not (tmp_path / 'cancelled.dw1digimon').exists()
    assert not list(tmp_path.glob('*.partial'))
    _, path = exported(tmp_path)
    report = prepare_import(rewrite(path, tmp_path / 'edited.dw1digimon', {'texture.png': edited_png(original)}), original)
    store = DigimonOverrides(tmp_path / 'project', original.meta['source_sha256'])
    import dw1_workbench.digimon_package as packages
    atomic = packages._atomic_write
    def fail_index(path, data):
        if path.name == 'imports.json': raise OSError('simulated disk failure')
        atomic(path, data)
    monkeypatch.setattr(packages, '_atomic_write', fail_index)
    with pytest.raises(OSError, match='disk failure'):
        store.apply(report)
    assert store.records() == {}
    assert not list(store.directory.glob('*.TIM'))


def test_changed_source_and_unsafe_blob_cannot_be_reused(tmp_path):
    original, path = exported(tmp_path)
    store = DigimonOverrides(tmp_path / 'project', original.meta['source_sha256'])
    report = prepare_import(rewrite(path, tmp_path / 'edited.dw1digimon', {'texture.png': edited_png(original)}), original)
    store.apply(report)
    with pytest.raises(DigimonImportError, match='source identity'):
        DigimonOverrides(tmp_path / 'project', 'other').records()
    index = json.loads(store.index.read_text())
    index['characters']['3']['files']['TIM']['file'] = '../../outside.TIM'
    store.index.write_text(json.dumps(index))
    with pytest.raises(DigimonImportError, match='Unsafe'):
        store.file(3, 'TIM')


def test_animation_decode_can_cancel_inside_a_clip():
    from dw1_workbench.animation import parse_animations
    from test_animation import clip_bytes, table
    data, offset = table(clip_bytes([0x4001, 0] * 500))
    checks = 0
    def cancelled():
        nonlocal checks
        checks += 1
        return checks > 10
    with pytest.raises(Cancelled, match='Animation parsing'):
        parse_animations(data, offset, 2, cancelled)
    assert checks == 11
