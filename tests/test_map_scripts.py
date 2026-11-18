import struct

from dw1_workbench.map_scripts import Scripts, simulate


def script_file(sections: dict[int, bytes]) -> bytes:
    """A script: its (offset, section) table ended by 0xFFFF, then each section's code."""
    table_size = (len(sections) + 1) * 4
    table, code = b"", b""
    for number, body in sections.items():
        table += struct.pack("<HH", table_size + len(code), number)
        code += body
    return table + struct.pack("<HH", 0, 0xFFFF) + code


def scripts_for(screen: int, own: int, entry: bytes, head_extra: bytes = b"") -> Scripts:
    head = struct.pack("<BBHH", 0xFB, 0, own, screen) + head_extra + b"\xfe\x00"
    maphead = script_file({screen + 1: head})
    body = script_file({0xFE: entry})
    data = bytearray(2048 * 4)
    starts = []
    for number in range(2048):
        starts.append(len(data))
        if number == own:
            data += body
    struct.pack_into("<2048I", data, 0, *starts)
    data += b"\0" * 4  # the next script's start is past this one's end
    return Scripts(bytes(data), maphead)


def visibility(instance: int, flag: int) -> bytes:
    return bytes((0x57, 0, instance, flag))


def test_trigger_test_picks_the_branch_for_a_finished_or_new_game():
    # IF trigger 0x13F set; jump if false to the else branch; hide 24 and 25; jump to the end; else: show 3
    then = visibility(24, 1) + visibility(25, 1)
    head_size = 12  # IF: 19 00, (00 00 3f 01), (18 00 offset), 19 00
    table = 8
    else_at = table + head_size + len(then) + 4
    end_at = else_at + 4
    entry = (b"\x19\x00" + struct.pack("<BBH", 0x00, 0, 0x13F) + struct.pack("<BBH", 0x18, 0, else_at) + b"\x19\x00"
             + then + struct.pack("<BBH", 0x16, 0, end_at) + visibility(3, 0) + b"\xfe\x00")
    scripts = scripts_for(139, 134, entry)
    assert scripts.map_head(139)[1] == 134
    assert simulate(scripts, 139, True) == {24: 1, 25: 1}
    assert simulate(scripts, 139, False) == {3: 0}


def test_text_is_stepped_over_and_maphead_commands_count_too():
    text = b"\x1a\x00" + "\x82\x60\x82\x61\r\0".encode("latin-1") + b"\0\0"
    scripts = scripts_for(5, 9, text + visibility(7, 0) + b"\xfe\x00", head_extra=visibility(1, 1))
    assert simulate(scripts, 5, True) == {1: 1, 7: 0}


def test_unknown_screen_or_unreadable_script_sets_nothing():
    scripts = scripts_for(5, 9, b"\x10\x02" + visibility(7, 0) + b"\xfe\x00")  # a menu: the walk stops
    assert simulate(scripts, 6, True) == {}
    assert simulate(scripts, 5, True) == {}
