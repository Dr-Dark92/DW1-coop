from pathlib import Path

import pytest

from dw1_workbench.catalog import open_catalogue
from dw1_workbench.disc import inspect_image
from dw1_workbench.errors import Cancelled, ValidationError
from dw1_workbench.iso9660 import identify_release, read_inventory, safe_export
from dw1_workbench.project import import_project, source_state
from conftest import make_iso


def test_inventory_and_release(synthetic_iso):
    layout = inspect_image(synthetic_iso)
    with layout.open() as reader:
        entries = read_inventory(reader)
        release = identify_release(reader, entries)
    assert {e.path for e in entries} == {"SYSTEM.CNF", "SLUS_010.32"}
    assert release.executable == "SLUS_010.32"


def test_rejects_wrong_release_and_bad_exe(tmp_path):
    wrong = make_iso(tmp_path / "wrong.iso", b"SLES_000.01")
    with inspect_image(wrong).open() as reader:
        entries = read_inventory(reader)
        with pytest.raises(ValidationError, match="only verifies"):
            identify_release(reader, entries)
    broken = make_iso(tmp_path / "broken.iso", bad_exe=True)
    with inspect_image(broken).open() as reader:
        with pytest.raises(ValidationError, match="PS-X EXE"):
            identify_release(reader, read_inventory(reader))


def test_catalogue_persistence_reopen_and_changed_source(synthetic_iso, tmp_path):
    project = tmp_path / "analysis.dw1project"
    catalogue = import_project(synthetic_iso, project)
    assert catalogue.meta()["import_status"] == "complete"
    reopened = open_catalogue(project)
    assert len(reopened.resources()) == 2
    assert source_state(reopened)[0]
    synthetic_iso.write_bytes(synthetic_iso.read_bytes() + b"changed")
    assert not source_state(reopened)[0]


def test_cancellation_records_cancelled_state(synthetic_iso, tmp_path):
    project = tmp_path / "cancelled.dw1project"
    with pytest.raises(Cancelled):
        import_project(synthetic_iso, project, cancelled=lambda: True)
    catalogue = open_catalogue(project)
    assert catalogue.meta()["import_status"] == "cancelled"
    assert "cancel" in catalogue.meta()["cancellation"].lower()


def test_export_no_overwrite(synthetic_iso, tmp_path):
    layout = inspect_image(synthetic_iso)
    with layout.open() as reader:
        entry = next(e for e in read_inventory(reader) if e.path == "SYSTEM.CNF")
        target = tmp_path / "export" / "SYSTEM.CNF"
        safe_export(reader, entry, target)
        with pytest.raises(ValidationError, match="already exists"):
            safe_export(reader, entry, target)
