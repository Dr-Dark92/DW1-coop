import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench.digidex import entry_rows, entry_sections
from dw1_workbench.digidex_view import DigiDexDialog
from dw1_workbench.evolution import EvolutionMenu
from dw1_workbench.game_menu_view import evolution_menu_image
from dw1_workbench.menu_icons import menu_icon, NAMES, PALETTE


def entry():
    stats = dict(max_hp=500, max_mp=200, offense=50, defense=40, speed=30, brains=20)
    skills = [dict(id=i, name=f"Technique {i}") for i in range(20)]
    return dict(name="Testmon <&>", stage=3, drop=dict(name="Meat", chance=10), skills=skills,
                placements=[dict(name="Possible map", map=109, stats=stats)],
                profiles=[dict(map=110, location="Seen map", hours=[6, 21], stats=stats, skills=skills)])


def test_added_sprites_are_small_shaded_pixel_art_not_antialiased_glyphs():
    images = []
    allowed = {QColor(value).rgba() for value in PALETTE.values()}
    for name in NAMES:
        image = menu_icon(name)
        assert (image.width(), image.height()) == (20, 20)
        colors = {image.pixelColor(x, y).rgba() for x in range(20) for y in range(20)}
        opaque = {c for c in colors if c >> 24}
        assert 5 <= len(opaque) <= 16
        assert opaque <= allowed
        assert all((c >> 24) in (0, 255) for c in colors)
        assert QColor(PALETTE["ink"]).rgba() in opaque
        images.append(bytes(image.constBits()))
    assert len(set(images)) == 5


def test_native_six_still_use_the_supplied_game_sprites():
    app = QApplication.instance() or QApplication([])
    icons = []
    for n in range(6):
        image = QImage(20, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor(30 + n * 25, 40, 50))
        icons.append(image)
    rows = ("Items", "Digimon", "Player", "Praise", "Scold", "Sleep", *NAMES)
    frame = QImage(320, 240, QImage.Format.Format_RGB32); frame.fill(QColor("black"))
    result = evolution_menu_image(frame, EvolutionMenu("Menu", rows, 0, "", layout="grid"), tuple(icons))
    for n in range(6):
        assert result.pixelColor(48 + (n % 3) * 75 + 30, 42 + (n // 3) * 41 + 10) == QColor(30 + n * 25, 40, 50)


def test_dex_pages_are_sectioned_and_long_equipped_lists_never_overflow():
    data = entry()
    groups = entry_sections(data)
    assert list(groups) == ["Overview", "Skills", "Decoded locations", "Observed encounters"]
    assert all(len(page) <= 9 for page in entry_rows(data))
    assert [skill for page in groups["Skills"] for style, _, skill in page if style == "skill"] == list(range(20))
    assert [skill for page in groups["Observed encounters"] for style, _, skill in page if style == "skill"] == list(range(20))
    assert any("Decoded placement" in text for page in groups["Decoded locations"] for _, text, _ in page)
    assert not any("Possible map" in text for page in groups["Observed encounters"] for _, text, _ in page)


def test_tabs_filter_and_locked_entries_never_keep_stale_data():
    app = QApplication.instance() or QApplication([])
    dialog = DigiDexDialog(dict(catalog=[2, 3], entries={"3": entry()}))
    try:
        assert [dialog.tabs.tabText(i) for i in range(4)] == list(entry_sections(entry()))
        dialog.list.setCurrentRow(1)
        assert dialog.title.text() == "Testmon <&>"
        assert "Rank: Rookie" in dialog.sections["Overview"].toPlainText()
        assert "Technique 19" in dialog.sections["Skills"].toPlainText()
        assert "Technique 19" not in dialog.sections["Overview"].toPlainText()
        assert "Possible map" in dialog.sections["Decoded locations"].toPlainText()
        assert "Seen map" in dialog.sections["Observed encounters"].toPlainText()
        dialog.discovered_only.setChecked(True)
        assert dialog.list.count() == 1 and dialog.list.currentItem().data(256) == 3
        dialog.discovered_only.setChecked(False)
        dialog.list.setCurrentRow(0)
        assert all("locked" in browser.toPlainText() for browser in dialog.sections.values())
        assert all(not dialog.tabs.isTabEnabled(i) for i in range(1, 4))
        dialog.search.setText("Testmon")
        assert dialog.list.count() == 1
        assert all(dialog.tabs.isTabEnabled(i) for i in range(4))
        dialog.search.setText("absent")
        assert all("No discovered" in browser.toPlainText() for browser in dialog.sections.values())
        assert dialog.avatar.pixmap() is None or dialog.avatar.pixmap().isNull()
    finally:
        dialog.close()
