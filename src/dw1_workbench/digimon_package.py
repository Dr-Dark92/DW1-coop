"""Native Digimon packages (one character, or a stage bundle of them) and validated, same-slot replacement imports.

MMD already contains geometry and animation records. The executable still owns the
skeleton, names and gameplay parameters; those are reference data, never imported.
No glTF-to-MMD conversion or cross-character retargeting is implied here.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import struct
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from . import __version__
from .alltim import SLOT_SIZE
from .animation import DECODER_VERSION as ANIMATION_VERSION, OP_SOUND, OP_TEXTURE, parse_animations
from .characters import CharacterEntry, parse_character_table
from .errors import Cancelled, DiscFormatError, ValidationError
from .mmd import DECODER_VERSION as MODEL_VERSION, parse_mmd_static, parse_usa_skeleton, validate_texture_materials
from .quicksave import _atomic_write
from .tim import DECODER_VERSION as TEXTURE_VERSION, _color, decode_tim

FORMAT = "dw1-digimon-package"
VERSION = 1
SUFFIX = ".dw1digimon"
IMPORT_VERSION = "native-digimon-import-v1"
MAX_MEMBER = 16 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
BUNDLE_FORMAT = "dw1-digimon-bundle"
BUNDLE_SUFFIX = ".dw1digimons"
MAX_BUNDLE_PACKAGES = 180
MAX_BUNDLE_TOTAL = 512 * 1024 * 1024
MEMBERS = {"model.MMD", "texture.TIM", "texture.png", "texture.png.json", "skeleton.json", "animations.json"}


class DigimonImportError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_cancel(cancelled):
    if cancelled():
        raise Cancelled("Digimon operation cancelled; no import was applied.")


@dataclass(frozen=True)
class DigimonSource:
    character: CharacterEntry
    executable: bytes
    model: bytes
    texture: bytes  # complete fixed-size ALLTIM slot, including original padding
    model_row: dict
    texture_row: dict
    meta: dict


@dataclass(frozen=True)
class DigimonImport:
    source: DigimonSource
    package: str
    model: bytes
    texture: bytes
    edited: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def changed(self):
        return tuple(kind for kind, data, original in (("MMD", self.model, self.source.model),
                                                       ("TIM", self.texture, self.source.texture)) if data != original)


def load_source(catalogue, character_id, cancelled=lambda: False, progress=lambda value, text: None):
    """Read the verified catalogue association on the caller's background thread."""
    from .project import layout_from_catalogue
    rows = {row['id']: dict(row) for row in catalogue.resources()}
    character = rows[character_id]
    if character['detected_format'] != 'DW1 character resource entry':
        raise DigimonImportError('Select a verified character resource entry.')
    related = {item['kind']: rows[item['target_id']] for item in catalogue.associations()
               if item['source_id'] == character_id and item['target_id'] in rows}
    if not {'model_container', 'texture'} <= related.keys():
        raise DigimonImportError('This character has no complete model/texture association.')
    executable = next(row for row in rows.values() if row['original_path'] == character['parent_path']
                      and row['offset'] is None)
    with layout_from_catalogue(catalogue).open() as reader:
        def read(row):
            check_cancel(cancelled)
            if row['size'] > MAX_MEMBER:
                raise DigimonImportError('Source resource exceeds the Digimon operation size limit.')
            parent = next((r for r in rows.values() if r['original_path'] == row['parent_path']
                           and r['offset'] is None), row)
            data = reader.read_extent_range(row['extent'], parent['size'], row['offset'] or 0,
                                            row['size'], cancelled=cancelled)
            if digest(data) != row['content_hash']:
                raise DigimonImportError(f"Source hash mismatch: {row['original_path']}")
            return data
        progress(2, 'Reading verified Digimon resources')
        exe = read(executable)
        entry = parse_character_table(exe)[int(character['original_path'].split('/')[-2])]
        return DigimonSource(entry, exe, read(related['model_container']), read(related['texture']),
                             related['model_container'], related['texture'], catalogue.meta())


def _source_check(source):
    char = source.character
    if source.meta.get("executable") != "SLUS_010.32" or not source.meta.get("source_sha256"):
        raise DigimonImportError("Digimon packages require a verified USA SLUS_010.32 project and source identity.")
    if not (0 <= char.resource_id < 180 and char.texture_slot == char.resource_id):
        raise DigimonImportError("The character has no supported same-index texture association.")
    if (source.model_row["original_path"] != char.model_path
            or source.texture_row["parent_path"] != "CHDAT/ALLTIM.TIM"
            or source.texture_row["offset"] != char.texture_slot * SLOT_SIZE
            or len(source.texture) != SLOT_SIZE):
        raise DigimonImportError("The model/ALLTIM provenance does not match this character's verified association.")
    for data, row in ((source.model, source.model_row), (source.texture, source.texture_row)):
        if len(data) != row["size"] or digest(data) != row["content_hash"]:
            raise DigimonImportError(f"{row['original_path']} no longer matches the catalogue; reopen/re-import the disc.")


def _decode(source, model, texture, cancelled):
    check_cancel(cancelled)
    try:
        skeleton = parse_usa_skeleton(source.executable, source.character.resource_id)
        parsed = parse_mmd_static(model, skeleton, cancelled)
        tim = decode_tim(texture)
        validate_texture_materials(parsed, tim)
        check_cancel(cancelled)
        clips = parse_animations(model, struct.unpack_from('<I', model, 4)[0], len(skeleton), cancelled)
    except (DiscFormatError, ValidationError, struct.error, IndexError) as exc:
        raise DigimonImportError(f'Native Digimon data is invalid or unsupported: {exc}') from exc
    check_cancel(cancelled)
    return skeleton, parsed, tim, clips


def _json(data):
    return json.dumps(data, indent=2, ensure_ascii=True).encode() + b'\n'


def _provenance(row):
    return {key: row.get(key) for key in ("id", "original_path", "parent_path", "extent", "offset", "size",
                                         "content_hash", "decoder", "decoder_version", "mapping_evidence", "confidence")}


def _package_bytes(source, model, texture, cancelled, progress):
    """The finished .dw1digimon ZIP for one character, built in memory."""
    _source_check(source)
    model = source.model if model is None else model
    texture = source.texture if texture is None else texture
    progress(10, "Validating Digimon model, animation and texture")
    skeleton, geometry, tim, clips = _decode(source, model, texture, cancelled)
    picture = io.BytesIO(); tim.image.save(picture, "PNG")
    payloads = {
        "model.MMD": model, "texture.TIM": texture, "texture.png": picture.getvalue(),
        "texture.png.json": _json({"source_resource_id": source.texture_row["id"], "source_sha256": digest(texture),
                                   "palette_index": 0, "provenance": _provenance(source.texture_row)}),
        "skeleton.json": _json({"nodes": [asdict(node) for node in skeleton],
                                 "note": "Executable-owned reference; edits are not imported."}),
        "animations.json": _json({"clips": [{"index": clip.index, "offset": clip.offset, "size": clip.size,
                                              "frames": clip.frame_count} if clip else None for clip in clips],
                                   "note": "Reference only; animation data is inside model.MMD."}),
    }
    if sum(map(len, payloads.values())) > MAX_TOTAL or any(
            len(data) > (MAX_MANIFEST if name.endswith('.json') else MAX_MEMBER) for name, data in payloads.items()):
        raise DigimonImportError('Export exceeds the native package size limits.')
    manifest = {"format": FORMAT, "version": VERSION,
                "created": datetime.now(timezone.utc).isoformat(timespec='seconds'),
                "tool_version": __version__,
                "parsers": {"model": MODEL_VERSION, "texture": TEXTURE_VERSION, "animation": ANIMATION_VERSION},
                "source": {key: source.meta.get(key) for key in ("executable", "release", "source_sha256")},
                "character": {"resource_id": source.character.resource_id, "internal_name": source.character.internal_name,
                              "display_name": source.character.display_name, "stage": source.character.stage},
                "resources": {"MMD": _provenance(source.model_row), "TIM": _provenance(source.texture_row)},
                "counts": {"objects": len(geometry.objects), "vertices": geometry.vertex_count,
                           "triangles": geometry.triangle_count, "animation_slots": len(clips)},
                "files": [{"path": name, "size": len(data), "sha256": digest(data)} for name, data in payloads.items()],
                "limits": "Same resource ID, executable skeleton, native model layout, texture size/VRAM layout. "
                          "Edit native MMD/TIM, or palette-preserving texture.png. No glTF conversion."}
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        for done, (name, data) in enumerate(payloads.items()):
            check_cancel(cancelled)
            progress(20 + done * 10, f"Exporting {name}")
            archive.writestr(name, data)
        archive.writestr('manifest.json', _json(manifest))
    return result.getvalue()


def _publish(destination, suffix, data, cancelled):
    """Write a new export atomically; never replace an existing file or write inside the repository."""
    destination = Path(destination).resolve().with_suffix(suffix)
    repository = Path(__file__).resolve().parents[2]
    if destination.is_relative_to(repository):
        raise DigimonImportError("Packages contain game data; choose a destination outside the source repository.")
    if destination.exists():
        raise DigimonImportError(f"Export already exists: {destination}")
    handle, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.digimon.', suffix='.partial')
    os.close(handle)
    try:
        Path(temporary).write_bytes(data)
        check_cancel(cancelled)
        os.link(temporary, destination)  # atomic publication, refuses overwrite even if another writer raced
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def export_package(destination, source, model=None, texture=None, cancelled=lambda: False,
                   progress=lambda value, text: None):
    """Export a native package atomically without replacing an existing destination."""
    destination = _publish(destination, SUFFIX, _package_bytes(source, model, texture, cancelled, progress), cancelled)
    progress(100, "Digimon package exported")
    return destination


def stage_characters(catalogue, stages):
    """Verified character rows whose executable stage is one of `stages`, in resource-ID order."""
    rows = [dict(row) for row in catalogue.resources() if row['detected_format'] == 'DW1 character resource entry']
    executable = next((dict(row) for row in catalogue.resources() if rows and row['original_path'] == rows[0]['parent_path']
                       and row['offset'] is None), None)
    if executable is None:
        return []
    from .project import layout_from_catalogue
    with layout_from_catalogue(catalogue).open() as reader:
        table = parse_character_table(reader.read_extent(executable['extent'], executable['size']))
    chosen = []
    for row in rows:
        resource_id = int(row['original_path'].split('/')[-2])
        if resource_id < len(table) and table[resource_id].stage in stages:
            chosen.append((resource_id, row))
    return [row for _, row in sorted(chosen, key=lambda item: item[0])]


def export_bundle(destination, catalogue, stages, overrides=None, cancelled=lambda: False,
                  progress=lambda value, text: None):
    """One .dw1digimons file holding a .dw1digimon package for every supported character of the given stages.

    Characters whose model or texture this importer cannot round-trip are listed in the manifest as skipped.
    Imported overrides, when given, are exported instead of the disc originals, as for single packages."""
    rows = stage_characters(catalogue, stages)
    if not rows:
        raise DigimonImportError("No verified characters have the selected evolution stage.")
    members, listed, skipped, meta = {}, [], [], catalogue.meta()
    for done, row in enumerate(rows):
        check_cancel(cancelled)
        name = row['original_path'].split('/')[-1]
        progress(5 + done * 90 // len(rows), f"Packaging {name} ({done + 1}/{len(rows)})")
        try:
            source = load_source(catalogue, row['id'], cancelled)
            resource_id = source.character.resource_id
            model = overrides.file(resource_id, 'MMD') if overrides else None
            texture = overrides.file(resource_id, 'TIM') if overrides else None
            data = _package_bytes(source, model, texture, cancelled, lambda value, text: None)
        except Cancelled:
            raise
        except (DigimonImportError, DiscFormatError, ValidationError, StopIteration, KeyError) as exc:
            skipped.append({"character": row['original_path'].split('#')[-1], "reason": str(exc) or type(exc).__name__})
            continue
        path = f"packages/{resource_id:03d}-{name}{SUFFIX}"
        members[path] = data
        listed.append({"path": path, "size": len(data), "sha256": digest(data), "resource_id": resource_id,
                       "internal_name": source.character.internal_name, "display_name": source.character.display_name,
                       "stage": source.character.stage})
    if not members:
        raise DigimonImportError("None of the selected characters can be packaged: " +
                                 "; ".join(f"{item['character']}: {item['reason']}" for item in skipped[:3]))
    manifest = {"format": BUNDLE_FORMAT, "version": VERSION,
                "created": datetime.now(timezone.utc).isoformat(timespec='seconds'), "tool_version": __version__,
                "source": {key: meta.get(key) for key in ("executable", "release", "source_sha256")},
                "stages": list(stages), "packages": listed, "skipped": skipped}
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_STORED) as archive:  # members are already compressed
        for path, data in members.items():
            archive.writestr(path, data)
        archive.writestr('bundle.json', _json(manifest))
    destination = _publish(destination, BUNDLE_SUFFIX, result.getvalue(), cancelled)
    progress(100, "Digimon bundle exported")
    return destination, len(listed), skipped


def read_bundle(path, cancelled=lambda: False):
    """The bundle manifest and its member packages (bytes), bounded and hash-listed; nothing is extracted."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) > MAX_BUNDLE_PACKAGES + 1 or 'bundle.json' not in names:
                raise DigimonImportError("Bundle has duplicate members, too many members or no bundle.json.")
            if sum(info.file_size for info in infos) > MAX_BUNDLE_TOTAL:
                raise DigimonImportError("Digimon bundle exceeds the total size limit.")
            files = {}
            for info in infos:
                check_cancel(cancelled)
                if (info.file_size > (MAX_MANIFEST * 4 if info.filename == 'bundle.json' else MAX_TOTAL)
                        or info.flag_bits & 1 or (info.external_attr >> 16) & 0xF000 == 0xA000
                        or not (info.filename == 'bundle.json'
                                or re.fullmatch(r'packages/\d{3}-[A-Za-z0-9_]+' + re.escape(SUFFIX), info.filename))):
                    raise DigimonImportError(f"Unsupported or oversized bundle member: {info.filename}")
                files[info.filename] = archive.read(info)
        manifest = json.loads(files.pop('bundle.json'))
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        raise DigimonImportError(f"Cannot read Digimon bundle: {exc}") from exc
    if (not isinstance(manifest, dict) or manifest.get('format') != BUNDLE_FORMAT
            or type(manifest.get('version')) is not int or manifest['version'] != VERSION):
        raise DigimonImportError("Unsupported Digimon bundle format or version.")
    listed = manifest.get('packages')
    if (not isinstance(listed, list) or not all(isinstance(item, dict) and isinstance(item.get('path'), str)
                                                 and type(item.get('resource_id')) is int for item in listed)):
        raise DigimonImportError("Malformed Digimon bundle package list.")
    if sorted(item['path'] for item in listed) != sorted(files):
        raise DigimonImportError("Bundle package list does not match its members.")
    ids = [item['resource_id'] for item in listed]
    if len(ids) != len(set(ids)):
        raise DigimonImportError("Bundle lists the same character more than once.")
    return manifest, {item['path']: (item['resource_id'], files[item['path']]) for item in listed}


@dataclass(frozen=True)
class BundleImport:
    path: Path
    manifest: dict
    reports: tuple  # DigimonImport per member, resource-ID order

    @property
    def package(self):
        return self.path.name

    @property
    def changed(self):
        return tuple(report for report in self.reports if report.changed)


def prepare_bundle_import(path, catalogue, cancelled=lambda: False, progress=lambda value, text: None):
    """Validate every member package exactly as a single import; any failure rejects the whole bundle."""
    manifest, members = read_bundle(path, cancelled)
    identity = manifest.get('source') or {}
    meta = catalogue.meta()
    if any(identity.get(key) != meta.get(key) for key in ('executable', 'source_sha256')):
        raise DigimonImportError("Bundle belongs to a different disc/release; use its original analysis project.")
    rows = {int(row['original_path'].split('/')[-2]): row['id'] for row in catalogue.resources()
            if row['detected_format'] == 'DW1 character resource entry'}
    reports = []
    for done, (member, (resource_id, data)) in enumerate(sorted(members.items(), key=lambda item: item[1][0])):
        progress(5 + done * 90 // len(members), f"Validating {member.split('/')[-1]} ({done + 1}/{len(members)})")
        if resource_id not in rows:
            raise DigimonImportError(f"{member}: character {resource_id} is not in this catalogue.")
        try:
            source = load_source(catalogue, rows[resource_id], cancelled)
            reports.append(prepare_import(io.BytesIO(data), source, cancelled, name=member.split('/')[-1]))
        except DigimonImportError as exc:
            raise DigimonImportError(f"{member}: {exc}") from exc
    progress(100, "Digimon bundle validated")
    return BundleImport(Path(path), manifest, tuple(reports))


def read_package(path, cancelled=lambda: False):
    """Read bounded members in memory; never extract archive paths onto disk."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if (len(names) != len(set(names)) or len(names) > 7
                    or any(name not in MEMBERS | {'manifest.json'} for name in names)):
                raise DigimonImportError("Package has duplicate, unsafe or unrecognized members.")
            if sum(info.file_size for info in infos) > MAX_TOTAL:
                raise DigimonImportError("Digimon package exceeds the total decompressed size limit.")
            files = {}
            for info in infos:
                check_cancel(cancelled)
                limit = MAX_MANIFEST if info.filename.endswith('.json') else MAX_MEMBER
                if info.file_size > limit or info.flag_bits & 1 or (info.external_attr >> 16) & 0xF000 == 0xA000:
                    raise DigimonImportError(f"Unsupported or oversized archive member: {info.filename}")
                chunks = bytearray()
                with archive.open(info) as stream:
                    while block := stream.read(256 * 1024):
                        check_cancel(cancelled)
                        chunks.extend(block)
                        if len(chunks) > limit:
                            raise DigimonImportError("Archive data exceeds its size limit.")
                files[info.filename] = bytes(chunks)
        manifest = json.loads(files.pop('manifest.json'))
        if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
                or type(manifest.get('version')) is not int or manifest['version'] != VERSION):
            raise DigimonImportError("Unsupported Digimon package format or version.")
        if any(not isinstance(manifest.get(key), dict) for key in ('source', 'character', 'resources')):
            raise DigimonImportError('Package source, character and resource provenance must be objects.')
        if (type(manifest['character'].get('resource_id')) is not int
                or any(not isinstance(manifest['resources'].get(kind), dict) for kind in ('MMD', 'TIM'))):
            raise DigimonImportError('Package character ID or native resource provenance is malformed.')
        listed = manifest.get('files')
        if not isinstance(listed, list) or not all(isinstance(item, dict) for item in listed):
            raise DigimonImportError("Malformed Digimon package file list.")
        if any(not isinstance(item.get('path'), str) or type(item.get('size')) is not int or item['size'] < 0
               or not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
               for item in listed):
            raise DigimonImportError('Package file list requires paths, nonnegative sizes and SHA-256 hashes.')
        paths = [item.get('path') for item in listed]
        if len(paths) != len(set(paths)) or set(paths) != set(files) or not {'model.MMD', 'texture.TIM'} <= set(paths):
            raise DigimonImportError("Package file list is duplicated, incomplete or mismatched.")
        edited = {item['path'] for item in listed if len(files[item['path']]) != item.get('size')
                  or digest(files[item['path']]) != item.get('sha256')}
        return manifest, files, edited
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        raise DigimonImportError(f"Cannot read Digimon package: {exc}") from exc


def _png_texture(png, metadata, original, source, cancelled):
    """Repaint indices through a verified existing palette; preserve all CLUT rows and unchanged indices."""
    first = decode_tim(original)
    if (not isinstance(metadata, dict) or metadata.get('source_resource_id') != source.texture_row['id']
            or metadata.get('source_sha256') != digest(original)):
        raise DigimonImportError("PNG provenance does not match this package's native texture.")
    palette_index = metadata.get('palette_index')
    if type(palette_index) is not int or not 0 <= palette_index < first.palette_count or first.mode not in (0, 1):
        raise DigimonImportError("PNG import requires a valid existing indexed TIM palette.")
    tim = decode_tim(original, palette_index)
    with Image.open(io.BytesIO(png)) as opened:
        if opened.size != tim.image.size:
            raise DigimonImportError(f"Texture PNG must retain native dimensions {tim.image.width}×{tim.image.height}.")
        edited = opened.convert('RGBA')
    block, _, _, colors, rows = struct.unpack_from('<IHHHH', original, 8)
    palette = [_color(value) for value in struct.unpack_from(f'<{colors}H', original, 20 + palette_index * colors * 2)]
    image_start = 8 + block + 12
    result = bytearray(original)
    flatten = lambda image: list(image.get_flattened_data() if hasattr(image, 'get_flattened_data') else image.getdata())
    before, after = flatten(tim.image), flatten(edited)
    nearest = {}; approximated = 0
    for i, (old, new) in enumerate(zip(before, after)):
        if i % 1024 == 0:
            check_cancel(cancelled)
        if old == new:
            continue
        if new not in nearest:
            candidates = [index for index, color in enumerate(palette) if color[3] == new[3]]
            if not candidates:
                raise DigimonImportError("PNG alpha must match a palette entry (0, 128 or 255); no compatible colour exists.")
            nearest[new] = min(candidates, key=lambda index: sum((palette[index][j] - new[j]) ** 2 for j in range(3)))
        index = nearest[new]
        approximated += palette[index] != new
        if first.mode == 0:
            at, shift = image_start + i // 2, (i % 2) * 4
            result[at] = (result[at] & ~(15 << shift)) | index << shift
        else:
            result[image_start + i] = index
    return bytes(result), f"PNG mapped through palette {palette_index}; {approximated} texels approximated. All palettes preserved."


def prepare_import(path, source, cancelled=lambda: False, progress=lambda value, text: None, name=None):
    _source_check(source)
    progress(5, "Reading Digimon package")
    manifest, files, edited = read_package(path, cancelled)
    identity = manifest.get('source') or {}
    character = manifest.get('character') or {}
    if any(identity.get(key) != source.meta.get(key) for key in ('executable', 'source_sha256')):
        raise DigimonImportError("Package belongs to a different disc/release; use its original analysis project.")
    if (character.get('resource_id'), character.get('internal_name')) != (source.character.resource_id, source.character.internal_name):
        raise DigimonImportError("Select the character ID named in this package; cross-character retargeting is unsupported.")
    for kind, row in (('MMD', source.model_row), ('TIM', source.texture_row)):
        recorded = (manifest.get('resources') or {}).get(kind) or {}
        if any(recorded.get(key) != row.get(key) for key in ('original_path', 'offset', 'size', 'content_hash')):
            raise DigimonImportError(f"{kind} source provenance does not match the catalogue.")
    notes = []
    for name in sorted(edited - {'model.MMD', 'texture.TIM', 'texture.png'}):
        raise DigimonImportError(f"{name} is reference/provenance data; edits are not supported.")
    model, texture = files['model.MMD'], files['texture.TIM']
    if 'texture.png' in edited:
        if 'texture.TIM' in edited:
            raise DigimonImportError("Both texture.TIM and texture.png were edited; keep one texture edit.")
        metadata = json.loads(files.get('texture.png.json', b'{}'))
        texture, note = _png_texture(files['texture.png'], metadata, texture, source, cancelled)
        notes.append(note)
    progress(35, "Validating native model, palette addressing and animation references")
    original = _decode(source, source.model, source.texture, cancelled)
    if len(model) != len(source.model):
        raise DigimonImportError("MMD replacement must retain its original byte length in this import version.")
    if len(texture) != SLOT_SIZE:
        raise DigimonImportError(f"Texture must retain its complete {SLOT_SIZE:#x}-byte ALLTIM slot.")
    parsed = _decode(source, model, texture, cancelled)
    old_tim, new_tim = original[2], parsed[2]
    fields = ('mode', 'image_x', 'image_y', 'clut_x', 'clut_y', 'palette_count', 'consumed')
    if old_tim.image.size != new_tim.image.size or any(getattr(old_tim, f) != getattr(new_tim, f) for f in fields):
        raise DigimonImportError("Texture dimensions, colour mode, palettes and VRAM placement must remain unchanged.")
    # Stable native layout keeps executable-owned object bindings and allocation assumptions intact.
    count = len(original[1].objects)
    table_end = 20 + count * 28
    if model[:table_end] != source.model[:table_end]:
        raise DigimonImportError("MMD header/object layout changed; this import version supports edits within the existing layout.")
    old_clips, new_clips = original[3], parsed[3]
    if len(old_clips) != len(new_clips) or [c is None for c in old_clips] != [c is None for c in new_clips]:
        raise DigimonImportError("Animation slot IDs and empty slots must remain unchanged.")
    for old, new in zip(old_clips, new_clips):
        check_cancel(cancelled)
        if old is not None:
            refs = lambda clip: [(i.opcode, i.payload) for i in clip.instructions if i.opcode in (OP_SOUND, OP_TEXTURE)]
            if refs(old) != refs(new):
                raise DigimonImportError("Animation sound/texture-copy references changed; external resource edits are unsupported.")
    if model != source.model:
        notes.append("Native geometry/animation edit uses the existing executable skeleton and model layout.")
    notes.append("IDs, skeleton, stats, evolution stage and gameplay parameters are retained from the executable.")
    check_cancel(cancelled)
    progress(100, "Digimon package validated")
    return DigimonImport(source, name or Path(path).name, model, texture, tuple(sorted(edited)), tuple(notes))


class DigimonOverrides:
    """Content-addressed assets, with one atomically replaced index marking a committed import."""
    def __init__(self, project_dir, source_sha256):
        project_dir = Path(project_dir).resolve()
        self.directory = project_dir / 'overrides' / 'digimon'
        if not self.directory.resolve().is_relative_to(project_dir):
            raise DigimonImportError('Digimon override storage escapes the project directory.')
        self.index = self.directory / 'imports.json'
        self.source_sha256 = source_sha256

    def _load(self):
        if not self.index.exists():
            return {'format': 1, 'source_sha256': self.source_sha256, 'characters': {}}
        try:
            data = json.loads(self.index.read_text())
            if data['format'] != 1 or data['source_sha256'] != self.source_sha256 or not isinstance(data['characters'], dict):
                raise ValueError('source identity or index format changed')
            return data
        except (ValueError, KeyError, TypeError) as exc:
            raise DigimonImportError(f"Cannot reuse Digimon overrides: {exc}") from exc

    def records(self):
        return {int(key): value for key, value in self._load()['characters'].items()}

    def file(self, resource_id, kind):
        record = self.records().get(resource_id, {}).get('files', {}).get(kind)
        if record is None:
            return None
        name = record.get('file', '')
        if kind not in ('MMD', 'TIM') or not re.fullmatch(r'[0-9a-f]{64}\.' + kind, name):
            raise DigimonImportError('Unsafe Digimon override filename.')
        path = self.directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise DigimonImportError('Digimon overrides must be regular files inside their project.')
        data = read_bounded(path, MAX_MEMBER)
        if len(data) != record['size'] or digest(data) != record['sha256']:
            raise DigimonImportError('Imported Digimon data changed on disk; remove or re-import it.')
        return data

    def apply(self, report, cancelled=lambda: False, progress=lambda value, text: None):
        self.apply_many([report], cancelled, progress)
        return report

    def apply_many(self, reports, cancelled=lambda: False, progress=lambda value, text: None):
        """Stage every changed asset, then commit all records with one index write (or none on failure)."""
        if any(report.source.meta['source_sha256'] != self.source_sha256 for report in reports):
            raise DigimonImportError('Import source identity differs from this project.')
        data = self._load(); created = []
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            for done, report in enumerate(reports):
                files = {}
                for kind, content, row in (('MMD', report.model, report.source.model_row),
                                           ('TIM', report.texture, report.source.texture_row)):
                    check_cancel(cancelled)
                    if kind not in report.changed:
                        continue
                    name = digest(content) + '.' + kind
                    target = self.directory / name
                    if target.exists():
                        if target.is_symlink() or read_bounded(target, MAX_MEMBER) != content:
                            raise DigimonImportError('An existing override blob is corrupt; nothing was applied.')
                    else:
                        _atomic_write(target, content); created.append(target)
                    files[kind] = {'file': name, 'size': len(content), 'sha256': digest(content), 'source': _provenance(row)}
                progress(5 + done * 90 // len(reports), f"Staging {report.source.character.internal_name}")
                key = str(report.source.character.resource_id)
                if files:
                    data['characters'][key] = {'internal_name': report.source.character.internal_name,
                                               'model_path': report.source.character.model_path, 'files': files,
                                               'package': report.package, 'importer': IMPORT_VERSION,
                                               'imported': datetime.now(timezone.utc).isoformat(), 'notes': report.notes}
                else:
                    data['characters'].pop(key, None)
            check_cancel(cancelled)
            self.directory.mkdir(parents=True, exist_ok=True)
            _atomic_write(self.index, _json(data))
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        progress(100, 'Digimon import applied')
        return reports

    def remove(self, resource_id):
        data = self._load()
        data['characters'].pop(str(resource_id), None)
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.index, _json(data))
        # Unreferenced content-addressed blobs are retained, so existing readers remain safe.

    def replacements(self, alltim, model_rows):
        """Merge imported slots into a fresh ALLTIM copy; never overwrite another character's slot."""
        if len(alltim) != 180 * SLOT_SIZE:
            raise DigimonImportError('Unexpected ALLTIM size; refusing texture replacement.')
        result = {}; texture = bytearray(alltim); has_texture = False
        for resource_id, record in self.records().items():
            if not 0 <= resource_id < 180 or resource_id not in model_rows:
                raise DigimonImportError('Imported character is absent from this catalogue.')
            for kind, info in record['files'].items():
                row = model_rows[resource_id] if kind == 'MMD' else None
                if kind == 'MMD' and (info['source']['content_hash'] != row['content_hash']
                                      or record['model_path'] != row['original_path']):
                    raise DigimonImportError('Imported model source changed; re-import the package.')
                content = self.file(resource_id, kind)
                if kind == 'MMD':
                    result[row['original_path']] = content
                else:
                    start = resource_id * SLOT_SIZE
                    if len(content) != SLOT_SIZE or digest(alltim[start:start + SLOT_SIZE]) != info['source']['content_hash']:
                        raise DigimonImportError('Imported texture source changed; re-import the package.')
                    texture[start:start + SLOT_SIZE] = content; has_texture = True
        if has_texture:
            result['CHDAT/ALLTIM.TIM'] = bytes(texture)
        return result


def read_bounded(path, limit=MAX_MEMBER, cancelled=lambda: False):
    data = bytearray()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(min(256 * 1024, limit + 1 - len(data))):
            check_cancel(cancelled)
            data.extend(chunk)
            if len(data) > limit:
                raise DigimonImportError(f"Input exceeds the {limit:,}-byte limit.")
    return bytes(data)
