"""Exercise native boot/allocation with a camera heap reservation, without installing any camera hooks."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile

from dw1_workbench.camera import RENDER_MAP_LOOP, RENDER_MAP_LOOP_WORDS
from dw1_workbench.camera_memory import BUFFER, END, CameraReservation, free_blocks
from dw1_workbench.extra_memory import extend_heap
from dw1_workbench.libretro import LibretroCore, BUTTON
from dw1_workbench.live import ram_offset, set_ram_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "bios", "disc", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("output must be outside the checkout")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"camera_hooks_installed": False, "native_frames": 0, "allocator_changes_while_reserved": 0}
    with tempfile.TemporaryDirectory(dir=args.output) as temporary:
        core = LibretroCore(args.core, args.bios, Path(temporary), {"beetle_psx_internal_resolution": "1x(native)"})
        claim = None
        try:
            core.start(args.disc)
            set_ram_size(core.ram_size)
            previous = None
            pattern = b"\xA5" * (END - BUFFER)
            expected = hashlib.sha256(pattern).digest()
            for frame in range(1800):
                core.buttons = (1 << BUTTON["B"]) if frame % 60 < 3 else 0
                core.run_frame()
                report["native_frames"] += 1
                if frame % 30:
                    continue
                native_code = struct.unpack("<4I", core.read_memory(ram_offset(RENDER_MAP_LOOP), 16))
                if native_code != RENDER_MAP_LOOP_WORDS:
                    continue  # game executable not loaded yet; do not touch BIOS memory structures
                if claim is None:
                    extend_heap(core.read_memory, core.write_memory, core.ram_size)
                    try:
                        candidate = CameraReservation(core)
                        candidate.reserve()
                    except ValueError:
                        continue
                    claim = candidate
                    core.write_memory(ram_offset(BUFFER), pattern)
                    report["reserved_at_frame"] = frame
                claim.check()
                if hashlib.sha256(core.read_memory(ram_offset(BUFFER), END - BUFFER)).digest() != expected:
                    raise ValueError("Native game modified reserved camera payload")
                current = [(b.address, b.following, b.units) for b in free_blocks(core)]
                if previous is not None and current != previous:
                    report["allocator_changes_while_reserved"] += 1
                previous = current
            if claim is None:
                raise ValueError("No verified free camera range became available during native boot")
            claim.release()
            report["released"] = not claim.active
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            core.stop()
    with (args.output / "report.json").open("x") as output:
        json.dump(report, output, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
