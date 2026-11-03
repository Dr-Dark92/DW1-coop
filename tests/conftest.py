from __future__ import annotations

import struct
from pathlib import Path

import pytest


def both16(value: int) -> bytes:
    return value.to_bytes(2, "little") + value.to_bytes(2, "big")


def both32(value: int) -> bytes:
    return value.to_bytes(4, "little") + value.to_bytes(4, "big")


def record(extent: int, size: int, name: bytes, directory=False) -> bytes:
    length = 33 + len(name) + (len(name) % 2 == 0)
    out = bytearray(length); out[0] = length; out[1] = 0
    out[2:10] = both32(extent); out[10:18] = both32(size)
    out[18:25] = bytes((126, 1, 1, 0, 0, 0, 0)); out[25] = 2 if directory else 0
    out[28:32] = both16(1); out[32] = len(name); out[33:33+len(name)] = name
    return bytes(out)


def make_iso(path: Path, executable=b"SLUS_010.32", bad_exe=False) -> Path:
    sectors = [bytearray(2048) for _ in range(32)]
    system = b"BOOT = cdrom:\\" + executable + b";1\r\nTCB = 4\r\nEVENT = 10\r\nSTACK = 801FFF00\r\n"
    exe = (b"NOT EXE!" if bad_exe else b"PS-X EXE") + bytes(2048 - 8)
    directory = b"".join((record(20, 2048, b"\x00", True), record(20, 2048, b"\x01", True),
                           record(21, len(system), b"SYSTEM.CNF;1"), record(22, len(exe), executable + b";1")))
    sectors[20][:len(directory)] = directory; sectors[21][:len(system)] = system; sectors[22][:len(exe)] = exe
    pvd = sectors[16]; pvd[0:7] = b"\x01CD001\x01"; pvd[80:88] = both32(len(sectors)); pvd[128:132] = both16(2048)
    root = record(20, 2048, b"\x00", True); pvd[156:156+len(root)] = root
    sectors[17][0:7] = b"\xffCD001\x01"
    path.write_bytes(b"".join(sectors)); return path


def iso_to_raw(iso: Path, raw: Path) -> Path:
    output = bytearray()
    data = iso.read_bytes()
    for index in range(0, len(data), 2048):
        sector = bytearray(2352); sector[:12] = b"\x00" + b"\xff" * 10 + b"\x00"; sector[15] = 2
        sector[16:20] = sector[20:24] = b"\x00\x00\x08\x00"
        sector[24:2072] = data[index:index+2048]; output += sector
    raw.write_bytes(output); return raw


@pytest.fixture
def synthetic_iso(tmp_path):
    return make_iso(tmp_path / "synthetic.iso")


@pytest.fixture(scope="session")
def fake_core(tmp_path_factory) -> Path:
    """tests/fake_core.c built as a shared library; skipped where no C compiler is available."""
    import shutil
    import subprocess
    import sys
    compiler = next((shutil.which(name) for name in ("cc", "gcc", "clang") if shutil.which(name)), None)
    if compiler is None:
        pytest.skip("no C compiler to build the fake libretro core")
    output = tmp_path_factory.mktemp("core") / ("fake_core.dll" if sys.platform == "win32" else "fake_core.so")
    source = Path(__file__).with_name("fake_core.c")
    subprocess.run([compiler, "-shared", "-fPIC", "-O1", "-o", str(output), str(source)], check=True)
    return output
