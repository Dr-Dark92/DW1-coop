"""Evolution chart limited to forms the partner has been; Digi-Dex avatars drawn from the model renderer."""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench import evolution as E
from dw1_workbench import progression as P
from dw1_workbench.game_menu_view import evolution_menu_image
from dw1_workbench.live import ram_offset
from dw1_workbench.model_view import ModelSnapshot
from test_evolution import controller, press  # noqa: F401  (fixture)
from test_model_view import model_with_uvs


@pytest.fixture(scope="module")
def application():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def family(controller, tmp_path):
    """Botamon(1) -> Koromon(2) -> Agumon(3) -> Greymon(5)/Tyrannomon(9) -> MetalGreymon(6)."""
    core = controller.core
    for kind, stage, name in [(1, 1, b"Botamon"), (2, 2, b"Koromon"), (3, 3, b"Agumon"), (6, 5, b"MetalGreymon")]:
        core.write(ram_offset(P.DIGIMON_DATA + kind * 52), name + bytes(20 - len(name)))
        core.put(P.DIGIMON_DATA + kind * 52 + 29, "<B", stage)
    core.put(E.EVOLUTION_PATHS + 11 + 5, "6B", 3, 255, 255, 255, 255, 255)
    core.put(E.EVOLUTION_PATHS + 4 * 11 + 5, "6B", 6, 255, 255, 255, 255, 255)
    controller.history = E.EvolutionHistory(tmp_path / "evolution_history.json")
    return controller


def open_chart(controller):
    controller.before_frame(0)
    controller.selection = 6
    press(controller, "B")
    assert controller.menu.layout == "relation"
    return controller.menu.relation


def test_chart_shows_only_reached_forms_and_anonymous_next_forms(family):
    family.history.observe(family.core.read)  # Agumon, the current partner
    relation = open_chart(family)
    names = {node[0]: node[1] for node in relation["nodes"]}
    # No Botamon/Koromon/MetalGreymon; Agumon's linked forms (fixture: two Champions and an Ultimate) are anonymous.
    assert names == {3: "Agumon", 5: "???", 9: "???", 10: "???"}
    assert set(relation["hidden"]) == {5, 9, 10}
    assert relation["selected_name"] == "???" and family.menu.rows == ("???", "???")
    press(family, "Left")  # previous forms were never recorded
    assert family.menu.relation["selected"] == 3


def test_unreached_form_cannot_be_explored_but_can_be_evolved_into(family):
    family.history.observe(family.core.read)
    open_chart(family)
    press(family, "Right")  # pressing again on the side would explore the selected form
    assert family.menu.relation["focus"] == 3
    family.progress.exp = P.total_exp(20)  # below the evolution level: Cross explores instead
    press(family, "B")
    assert family.menu.relation["focus"] == 3
    family.progress.exp = P.total_exp(21)
    press(family, "B"); press(family, "B")
    assert family.number(E.EVOLUTION_TARGET) == 5


def test_recorded_forms_appear_with_names_and_links(family):
    core = family.core
    for kind in (1, 2, 5):
        core.put(P.PARTNER_ENTITY, "<i", kind)
        core.put(P.DIGIMON_DATA + 5 * 52 + 29, "<B", 4)
        assert family.history.observe(core.read)
    core.put(P.PARTNER_ENTITY, "<i", 3)
    relation = open_chart(family)
    names = {node[0]: node[1] for node in relation["nodes"]}
    assert names == {1: "Botamon", 2: "Koromon", 3: "Agumon", 5: "Greymon", 9: "???", 10: "???"}
    # MetalGreymon (never reached, and not linked from the current partner) is absent.
    assert {(1, 2), (2, 3), (3, 5), (3, 9), (3, 10)} == set(relation["edges"])


def test_history_ignores_title_screen_and_round_trips(family, tmp_path):
    core = family.core
    core.put(P.PARTNER_STATS + 16, "<h", 0)
    assert not family.history.observe(core.read)
    core.put(P.PARTNER_STATS + 16, "<h", 900)
    assert family.history.observe(core.read) and not family.history.observe(core.read)
    family.history.save()
    assert E.EvolutionHistory(tmp_path / "evolution_history.json").forms == {3}
    (tmp_path / "bad.json").write_text(json.dumps({"format": 1, "forms": [999]}))
    with pytest.raises(ValueError):
        E.EvolutionHistory(tmp_path / "bad.json")


def test_without_a_history_the_chart_is_unchanged(controller):
    controller.before_frame(0)
    controller.selection = 6
    press(controller, "B")
    assert controller.menu.relation["hidden"] == ()
    assert controller.menu.rows == ("Greymon", "Tyrannomon")


def avatar(application):
    palette = QImage(16, 16, QImage.Format.Format_ARGB32)
    palette.fill(QColor(255, 0, 0))
    return ModelSnapshot(model_with_uvs(((3, 3), (3, 9), (9, 5))), (palette,), 488).image(64)


def test_snapshot_renders_model_on_transparent_background(application):
    image = avatar(application)
    colours = [image.pixelColor(x, y) for x in range(64) for y in range(64)]
    assert sum(c.red() == 255 and c.alpha() == 255 for c in colours) > 50
    assert image.pixelColor(0, 0).alpha() == 0


def test_dex_list_and_page_draw_avatars_only_for_unlocked_rows(application):
    image = avatar(application)
    frame = QImage(320, 240, QImage.Format.Format_RGB32); frame.fill(QColor(0, 0, 0))
    listing = E.EvolutionMenu("Digi-Dex", ("??? (not battled)", "Agumon"), 1, "", icons=(None, 3))
    drawn = evolution_menu_image(frame, listing, avatars={3: image, 2: image})
    red_in = lambda img, top: sum(img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() < 60
                                  for x in range(72, 90) for y in range(top, top + 17))
    assert red_in(drawn, 43) == 0 and red_in(drawn, 61) > 5  # locked row: no avatar
    page = E.EvolutionMenu("Agumon", ("Rank: Rookie",), -1, "", False, avatar=3)
    drawn = evolution_menu_image(frame, page, avatars={3: image})
    assert sum(drawn.pixelColor(x, y).red() > 200 for x in range(26, 74) for y in range(44, 92)) > 50


def test_windows_dex_shows_avatar_only_when_unlocked(application):
    from dw1_workbench.digidex_view import DigiDexDialog
    entry = {"name": "Agumon", "stage": 3, "drop": {"name": "Meat", "chance": 10}, "profiles": []}
    dialog = DigiDexDialog({"catalog": [2, 3], "entries": {"3": entry}}, avatars={2: avatar(application),
                                                                                  3: avatar(application)})
    assert dialog.list.item(0).icon().isNull() and not dialog.list.item(1).icon().isNull()
    assert dialog.avatar.text() == "?"
    dialog.list.setCurrentRow(1)
    assert dialog.avatar.pixmap() is not None and not dialog.avatar.pixmap().isNull()
    dialog.close()


def test_move_table_and_styled_skill_rows(application):
    import struct
    from dw1_workbench import digidex as D
    from test_progression import Ram
    ram = Ram()
    for move, (power, mp, reach, element, status) in {61: (155, 40, 2, 0, 0), 12: (85, 15, 1, 2, 3)}.items():
        struct.pack_into("<ihBBBBB", ram.data, ram_offset(D.MOVE_DATA) + move * 16, 0, power, mp, 0, reach, element, status)
    for move in range(D.MOVE_NAME_COUNT):  # everything else: an invalid element
        if move not in (61, 12):
            ram.data[ram_offset(D.MOVE_DATA) + move * 16 + 9] = 255  # Move.special
    moves = D.read_moves(ram.read)
    assert moves == {61: {"element": 0, "power": 155, "mp": 120, "range": 2, "status": 0},
                     12: {"element": 2, "power": 85, "mp": 45, "range": 1, "status": 3}}
    assert D.skill_summary(moves[12]) == "Air · Pow 85 · MP 45 · Short · Stun"
    entry = {"name": "Agumon", "stage": 3, "drop": {"name": "Meat", "chance": 10}, "profiles": [],
             "skills": [{"id": 61, "name": "Fireball"}, {"id": 12, "name": "Static Elect"}]}
    page = D.entry_rows(entry)[1]
    assert page[0] == ("header", "Linked skills (species)", None)
    assert page[1:] == [("skill", "Fireball", 61), ("skill", "Static Elect", 12)]
    assert D.entry_pages(entry)[1] == ["Linked skills (species)", "Fireball", "Static Elect"]

    frame = QImage(320, 240, QImage.Format.Format_RGB32); frame.fill(QColor(0, 0, 0))
    menu = E.EvolutionMenu("Agumon", tuple(t for _, t, _ in page), -1, "", False,
                           styles=tuple(s for s, _, _ in page), skills=(None, moves[61], moves[12]))
    drawn = evolution_menu_image(frame, menu)
    fire = [drawn.pixelColor(x, y) for x in range(72, 88) for y in range(62, 76)]
    assert sum(c.red() > 180 and c.green() < 120 for c in fire) > 10  # the red Fire badge next to Fireball

    from dw1_workbench.digidex_view import DigiDexDialog
    dialog = DigiDexDialog({"catalog": [3], "entries": {"3": entry}, "moves": moves})
    text = dialog.details.toHtml()
    assert "element:0" in text and "element:2" in text and "Pow 155" in text and "Stun" in text
    assert dialog.title.text() == "Agumon"
    dialog.close()
