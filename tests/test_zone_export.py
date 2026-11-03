from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from dw1_workbench.errors import Cancelled
from dw1_workbench.maps import parse_tfs
from dw1_workbench.zone_export import MapSource, export_package
from test_maps import make_tfs
from test_pathing import two_maps


def sources(entries, first, second, tamper=False):
    tfs_bytes = make_tfs()
    result = {}
    for index, scene in ((0, first), (1, second)):
        map_bytes = b"MAP%d" % index  # stand-in original bytes; only hashed and copied
        row = {"original_path": entries[index].map_path, "size": len(map_bytes), "extent": 30 + index, "offset": None,
               "decoder": "map", "decoder_version": "1",
               "content_hash": hashlib.sha256(map_bytes + (b"x" if tamper and index else b"")).hexdigest()}
        tfs_row = {"original_path": entries[index].tfs_path, "size": len(tfs_bytes),
                   "content_hash": hashlib.sha256(tfs_bytes).hexdigest()}
        result[index] = MapSource(entries[index], scene, parse_tfs(tfs_bytes), map_bytes, tfs_bytes, row, tfs_row)
    return result


def test_bundle_package_contents(tmp_path):
    entries, first, second = two_maps()
    names = {0: "Test Woods · TEST01", 1: "Test Town · TEST02"}
    manifest = export_package(tmp_path / "bundle", ["Test Woods", "Test Town"], sources(entries, first, second),
                              {0: first, 1: second}, entries, names, {3: "Agumon"},
                              {"release": "test", "source_sha256": "ab"})
    path = tmp_path / "bundle.dw1zone"
    with zipfile.ZipFile(path) as package:
        names_in = set(package.namelist())
        assert {"manifest.json", "routes.json", "zones/test-woods.json", "zones/test-town.json",
                "maps/000-TEST01/TEST01.MAP", "maps/000-TEST01/TEST01.TFS", "maps/000-TEST01/map.json",
                "maps/000-TEST01/pathing.json", "maps/000-TEST01/pathing.png", "maps/000-TEST01/floor-grid.png",
                "maps/000-TEST01/background-palette0.png"} <= names_in
        assert not any(name.startswith("observations/") for name in names_in)
        assert package.read("maps/001-TEST02/TEST02.MAP") == b"MAP1"
        document = json.loads(package.read("maps/000-TEST01/map.json"))
        assert document["files"]["map"]["sha256"] == hashlib.sha256(b"MAP0").hexdigest()
        assert document["digimon_placements"][0]["name"] == "Agumon"
        assert document["entrances"] == [{"from_map": 1, "from_label": "Test Town · TEST02", "slot": 0, "to_spawn": 0,
                                          "walk_through": True, "inside_package": True}]
        routes = json.loads(package.read("routes.json"))["routes"]
        assert {(route["from_map"], route["to_map"], route["distance"]) for route in routes} == {(0, 1, 10.0), (1, 0, 10.0)}
        stored = json.loads(package.read("manifest.json"))
    assert stored == manifest and manifest["kind"] == "bundle" and manifest["external_links"] == 0
    assert all(entry["sha256"] for entry in manifest["files"])


def test_single_zone_lists_links_to_outside(tmp_path):
    entries, first, second = two_maps()
    every = sources(entries, first, second)
    manifest = export_package(tmp_path / "zone.dw1zone", ["Test Woods"], {0: every[0]}, {0: first, 1: second}, entries,
                              {}, {}, {})
    with zipfile.ZipFile(tmp_path / "zone.dw1zone") as package:
        zone = json.loads(package.read("zones/test-woods.json"))
    assert manifest["kind"] == "zone" and manifest["external_links"] == 2
    assert zone["exits_to_outside"][0]["to_map"] == 1 and zone["entrances_from_outside"][0]["from_map"] == 1


def test_changed_source_and_cancel_leave_no_package(tmp_path):
    entries, first, second = two_maps()
    with pytest.raises(ValueError, match="no longer matches"):
        export_package(tmp_path / "bad", ["Test Woods", "Test Town"], sources(entries, first, second, tamper=True),
                       {}, entries, {}, {}, {})
    with pytest.raises(Cancelled):
        export_package(tmp_path / "bad", ["Test Woods"], sources(entries, first, second), {}, entries, {}, {}, {},
                       cancelled=lambda: True)
    assert list(tmp_path.iterdir()) == []
