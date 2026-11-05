"""Fixed camera framing investigation on a disposable core, never the user's running game.

Explicit inputs only. Produces native / experimental captures and evidence outside Git.
This does not enable normal-play zoom or certify battle/transition safety.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from collections import OrderedDict

from dw1_workbench.camera import CameraZoom, VIEWPORT_DISTANCE, DRAWING_OFFSET_X, ENGINE_PARAMS
from dw1_workbench.hd_background import GAME_STATE, SCRIPT_MAP_CHANGE_STATE
from dw1_workbench.inspection import InspectionStore
from dw1_workbench.libretro import LibretroCore, BUTTON
from dw1_workbench.live import ram_offset, read_live_state, set_ram_size
from dw1_workbench.player import frame_image
from dw1_workbench.fixed_framing import FixedFramingProof, gte_projection_offset
from dw1_workbench.camera_memory import PARAMS as FIXED_PARAMS, BUFFER as FIXED_BUFFER

SCALE = 230  # one fixed 230/256 = 89.84375% framing, no adjustable range


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "bios", "disc", "inspections", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--inspection", required=True)
    parser.add_argument("--legacy", action="store_true", help="reproduce the old misplacement for comparison")
    parser.add_argument("--direction", choices=("Up", "Down", "Left", "Right"))
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--scale", type=int, default=SCALE, help="frame scale out of 256: 230 is 90%%, 128 is ×2")
    args = parser.parse_args()
    if not 30 <= args.frames <= 1800:
        parser.error("frames must be 30–1800")
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("output must be outside the source checkout")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    source = InspectionStore(args.inspections).state(args.inspection)
    report = dict(experimental=True, enabled_in_normal_play=False, scale=args.scale, denominator=256,
                  legacy=args.legacy,
                  direction=args.direction, emulator_frames=args.frames,
                  state_sha256=hashlib.sha256(source).hexdigest(), inputs={key: str(getattr(args, key))
                  for key in ("core", "bios", "disc", "inspection")}, runs=[])
    with tempfile.TemporaryDirectory(prefix="framing-core-", dir=args.output) as temporary:
        core = LibretroCore(args.core, args.bios, Path(temporary),
                            {"beetle_psx_internal_resolution": "2x", "beetle_psx_dither_mode": "disabled"})
        try:
            core.start(args.disc)
            report["core_version"] = core.info.version
            report["core_options"] = dict(core.options)
            from dw1_workbench.camera_engine_code import SOURCE_SHA256
            report["engine_source_sha256"] = SOURCE_SHA256
            set_ram_size(core.ram_size)
            for _ in range(8): core.run_frame()
            core.unserialize(source)
            for _ in range(30): core.run_frame()
            if core.read_memory(ram_offset(GAME_STATE), 1)[0] != 0:
                raise ValueError("Baseline must be exploration, not battle or an unknown scene")
            if int.from_bytes(core.read_memory(ram_offset(SCRIPT_MAP_CHANGE_STATE), 2), "little"):
                raise ValueError("Baseline must not be inside a map transition")
            baseline = core.serialize()
            logical_runs, native_frames = {}, OrderedDict()
            for name in ("native", "fixed90"):
                core.unserialize(baseline)
                zoom = CameraZoom(core) if args.legacy else FixedFramingProof(core, args.scale)
                if name == "fixed90":
                    if args.legacy: zoom.install(args.scale)
                    else: zoom.install()
                snapshots, render_samples, logical = [], [], {}
                frame = None
                for number in range(args.frames):
                    core.buttons = (1 << BUTTON[args.direction]) if args.direction else 0
                    current, _audio = core.run_frame()
                    if current is not None: frame = current
                    if name == "fixed90": zoom.tick()
                    live = read_live_state(core.read_memory)
                    world = dict(map=live.map_index, tamer=asdict(live.tamer) if live.tamer else None,
                                 partner=asdict(live.partner) if live.partner else None,
                                 npcs=[(n.slot, n.character_id, asdict(n.position)) for n in live.npcs])
                    # clock.c: hour/minute/current-frame identify a game-clock tick, not an emulated vblank.
                    clock = tuple(int.from_bytes(core.read_memory(ram_offset(address), 2), "little")
                                  for address in (0x80134EBC, 0x80134EBE, 0x80134F08))
                    logical[clock] = world
                    if name == "native" and frame is not None:
                        native_frames[clock] = frame
                        native_frames.move_to_end(clock)
                        while len(native_frames) > 32:
                            native_frames.popitem(last=False)
                    if number in (29, 59, 119):
                        state = core.serialize()
                        render_samples.append(dict(frame=number, gte=list(struct.unpack_from("<iiI", state, gte_projection_offset(state))),
                                                   offset=list(struct.unpack("<ii", core.read_memory(ram_offset(DRAWING_OFFSET_X), 8)))))
                        live = read_live_state(core.read_memory)
                        snapshots.append(dict(frame=number, map=live.map_index,
                                              tamer=asdict(live.tamer) if live.tamer else None,
                                              partner=asdict(live.partner) if live.partner else None,
                                              npcs=[(n.slot, n.character_id, asdict(n.position)) for n in live.npcs]))
                if frame is None: raise ValueError("Core produced no video")
                core.buttons = 0
                frame_image(frame).save(str(args.output / f"{name}.png"))
                entry = dict(name=name, snapshots=snapshots, render_samples=render_samples, size=[frame.width, frame.height],
                             projection=int.from_bytes(core.read_memory(ram_offset(VIEWPORT_DISTANCE), 4), "little"),
                             drawing_offset=list(struct.unpack("<ii", core.read_memory(ram_offset(DRAWING_OFFSET_X), 8))))
                if name == "fixed90":
                    entry["engine_frames"] = zoom.engine_frames
                    entry["engine_uploads"] = zoom.engine_uploads
                    params = ENGINE_PARAMS if args.legacy else FIXED_PARAMS
                    entry["engine_status"] = list(struct.unpack("<ii", core.read_memory(ram_offset(params + 244), 8)))
                    if not args.legacy:
                        zoom.reservation.check()
                        entry["reserved_buffer"] = hex(FIXED_BUFFER)
                        entry["heap_ownership_checked"] = True
                report["runs"].append(entry)
                logical_runs[name] = logical
                if name == "fixed90" and clock in native_frames:
                    frame_image(native_frames[clock]).save(str(args.output / "native-matched-clock.png"))
                if name == "fixed90":
                    zoom.uninstall()
                    entry["restored_projection"] = int.from_bytes(core.read_memory(ram_offset(VIEWPORT_DISTANCE), 4), "little")
            report["world_snapshots_equal"] = report["runs"][0]["snapshots"] == report["runs"][1]["snapshots"]
            shared = logical_runs["native"].keys() & logical_runs["fixed90"].keys()
            # Exclude partial first/last ticks; the final world update may still be in flight at the vblank boundary.
            shared = sorted(shared)[1:-1]
            report["matched_clock_ticks"] = len(shared)
            report["matched_clock_world_equal"] = all(logical_runs["native"][k] == logical_runs["fixed90"][k] for k in shared)
            report["mismatched_clock_ticks"] = [k for k in shared if logical_runs["native"][k] != logical_runs["fixed90"][k]]
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            core.stop()  # no user save/card path was ever supplied
    with (args.output / "report.json").open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
