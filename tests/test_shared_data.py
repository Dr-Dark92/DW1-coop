from PySide6.QtCore import QSettings

from dw1_workbench import shared_data as S


def native(tmp_path):
    return QSettings(str(tmp_path / "old.ini"), QSettings.Format.IniFormat)


def test_everything_lives_under_the_chosen_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(S.HOME_VARIABLE, str(tmp_path / "home"))
    assert S.data_root() == tmp_path / "home"
    assert S.saves_root() == tmp_path / "home" / "play"
    assert S.settings_path() == tmp_path / "home" / "settings.ini"
    settings = S.app_settings()
    settings.setValue("play/screen", "4:3"); settings.sync()
    assert S.app_settings().value("play/screen") == "4:3"


def test_home_folder_is_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv(S.HOME_VARIABLE, raising=False)
    monkeypatch.setattr(S.Path, "home", lambda: tmp_path)
    assert S.data_root() == tmp_path / ".dw1-coop"


def test_old_settings_come_once_and_never_over_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv(S.HOME_VARIABLE, str(tmp_path / "home"))
    old = native(tmp_path)
    old.setValue("play/use_imports", True); old.setValue("play/framerate", 60.0); old.sync()
    assert S.bring_in_settings(old) is True
    new = S.app_settings()
    assert new.value("play/use_imports", False, type=bool) is True and float(new.value("play/framerate")) == 60.0
    new.setValue("play/framerate", 30.0); new.sync()
    old.setValue("play/framerate", 90.0); old.sync()
    assert S.bring_in_settings(old) is False
    assert float(S.app_settings().value("play/framerate")) == 30.0


def old_store(tmp_path, name, files):
    folder = tmp_path / name / "disc"
    for relative, data in files.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(data)
    return tmp_path / name


def test_saves_never_overwrite_and_come_once(tmp_path, monkeypatch):
    monkeypatch.setenv(S.HOME_VARIABLE, str(tmp_path / "home"))
    here = S.saves_root() / "disc"; here.mkdir(parents=True)
    (here / "slot8.state").write_bytes(b"already here")
    old = old_store(tmp_path, "real", {"slot0.state": b"resume", "slot8.state": b"would overwrite", "inspections/a.png": b"p"})
    copied = S.bring_in_saves(old)
    assert (here / "slot0.state").read_bytes() == b"resume"
    assert (here / "inspections" / "a.png").read_bytes() == b"p"
    assert (here / "slot8.state").read_bytes() == b"already here"
    assert len(copied) == 2
    assert (old / "disc" / "slot8.state").read_bytes() == b"would overwrite"  # the old copy stays
    # A later save in the shared folder is not undone by another start.
    (here / "slot0.state").write_bytes(b"newer")
    (here / "slot0.state").unlink()
    assert S.bring_in_saves(old) == [] and not (here / "slot0.state").exists()


def test_two_old_copies_each_come_once(tmp_path, monkeypatch):
    monkeypatch.setenv(S.HOME_VARIABLE, str(tmp_path / "home"))
    S.bring_in_saves(old_store(tmp_path, "first", {"slot1.state": b"one"}))
    S.bring_in_saves(old_store(tmp_path, "second", {"slot1.state": b"two", "slot2.state": b"three"}))
    here = S.saves_root() / "disc"
    assert (here / "slot1.state").read_bytes() == b"one" and (here / "slot2.state").read_bytes() == b"three"


def test_nothing_to_bring_in(tmp_path, monkeypatch):
    monkeypatch.setenv(S.HOME_VARIABLE, str(tmp_path / "home"))
    assert S.bring_in_saves(tmp_path / "missing") == []
