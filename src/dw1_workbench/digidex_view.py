"""Windows Digi-Dex reader. Unknown entries never receive names, avatars or details."""
import html

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QIcon, QPixmap, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)
from .digidex import ELEMENTS, RANGES, STATUSES, entry_sections
from .game_menu_view import ELEMENT_COLORS, STATUS_COLORS, element_icon_image

# The in-game Dex window's colours: deep blue panels, gold rules and labels.
STYLE = """
QDialog { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #16325a, stop:1 #081326); color: #ffffff; }
QLabel { color: #f0c860; }
QLineEdit { background: #0c1c33; color: #ffffff; border: 1px solid #6e5a2c; border-radius: 3px; padding: 4px; }
QListWidget { background: #0c1c33; color: #ffffff; border: 1px solid #d9ba70; border-radius: 4px; font-size: 13px; }
QListWidget::item { padding: 3px; border-bottom: 1px solid #1a3152; }
QListWidget::item:selected { color: #ffe083;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3b6a9c, stop:1 #203f66); }
QTextBrowser { background: #0c1c33; color: #ffffff; border: 1px solid #d9ba70; border-radius: 4px; }
QScrollBar:vertical { background: #0c1c33; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #6e5a2c; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 1px solid #6e5a2c; }
QTabBar::tab { background: #0c1c33; color: #bcd8f2; padding: 9px 12px; }
QTabBar::tab:selected { background: #294b70; color: #ffe083; border-bottom: 2px solid #d9ba70; }
QCheckBox { color: #bcd8f2; padding: 4px 0; }
"""


class DigiDexDialog(QDialog):
    def __init__(self, snapshot, map_names=None, parent=None, avatars=None):
        super().__init__(parent)
        self.setWindowTitle("Digi-Dex")
        self.setStyleSheet(STYLE)
        self.resize(1080, 720)
        self.setMinimumSize(820, 540)
        self.entries = snapshot.get("entries", {})
        self.ids = snapshot.get("catalog") or sorted(int(k) for k in self.entries)
        self.moves = snapshot.get("moves") or {}
        self.map_names = map_names or {}
        self.avatars = avatars or {}  # kind -> QImage (avatars.py); missing while still rendering
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Digi-Dex · {len(self.entries)} discovered / {len(self.ids)} catalogued · Battle to unlock information"))
        self.search = QLineEdit(); self.search.setPlaceholderText("Search discovered Digimon")
        self.discovered_only = QCheckBox("Discovered only")
        sidebar = QWidget(); side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 8, 0)
        side_layout.addWidget(self.search); side_layout.addWidget(self.discovered_only)
        content = QSplitter()
        self.list = QListWidget(); self.list.setIconSize(QSize(40, 40))
        side_layout.addWidget(self.list, 1)
        sidebar.setMinimumWidth(190)
        self.avatar = QLabel(); self.avatar.setFixedSize(128, 128)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #24456b, stop:1 #0f2038);"
                                  "border: 1px solid #d9ba70; border-radius: 6px; color: #7f93a3; font-size: 40px;")
        self.title = QLabel(); self.title.setStyleSheet("color: #ffe083; font-size: 20px; font-weight: bold;")
        heading = QHBoxLayout(); heading.addWidget(self.avatar); heading.addWidget(self.title, 1)
        self.subtitle = QLabel("Select a Digimon"); self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color:#bcd8f2; padding:6px 0")
        self.tabs = QTabWidget()
        self.sections = {}
        for name in ("Overview", "Skills", "Decoded locations", "Observed encounters"):
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            for element in range(len(ELEMENTS)):
                browser.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"element:{element}"),
                                               element_icon_image(element, 18))
            self.sections[name] = browser
            self.tabs.addTab(browser, name)
        self.details = self.sections["Overview"]  # overview/locked-state compatibility, never hidden aggregate data
        right = QWidget(); detail = QVBoxLayout(right)
        detail.setContentsMargins(8, 0, 0, 0)
        detail.addLayout(heading); detail.addWidget(self.subtitle); detail.addWidget(self.tabs, 1)
        content.addWidget(sidebar); content.addWidget(right)
        content.setStretchFactor(0, 1); content.setStretchFactor(1, 3); content.setSizes([240, 800])
        layout.addWidget(content, 1)
        layout.addWidget(QLabel("Times are observed in-game hours. Decoded placements may depend on story/scripts."))
        self.search.textChanged.connect(self.refresh)
        self.discovered_only.toggled.connect(self.refresh)
        self.list.currentItemChanged.connect(self.show_entry)
        self.refresh()

    def refresh(self):
        selected = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.list.clear()
        term = self.search.text().casefold()
        for kind in self.ids:
            entry = self.entries.get(str(kind))
            if self.discovered_only.isChecked() and entry is None:
                continue
            if term and (entry is None or term not in f"{kind:03d} {entry['name']}".casefold()):
                continue
            item = QListWidgetItem(f"#{kind:03d}  " + (entry["name"] if entry else "??? — not battled"))
            item.setData(Qt.ItemDataRole.UserRole, kind)
            if entry is None:
                item.setForeground(Qt.GlobalColor.gray)
            elif kind in self.avatars:
                item.setIcon(QIcon(QPixmap.fromImage(self.avatars[kind])))
            self.list.addItem(item)
        if self.list.count():
            row = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole) == selected), 0)
            self.list.setCurrentRow(row)
        else:
            self.avatar.clear(); self.title.clear()
            self.subtitle.clear()
            self._empty("No discovered Digimon match your search." if term else "No encounters yet.")

    def _empty(self, text):
        for name, browser in self.sections.items():
            browser.setPlainText(text)
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, False)
        self.tabs.setCurrentIndex(0)

    def show_entry(self, item, previous=None):
        if item is None:
            return
        kind = item.data(Qt.ItemDataRole.UserRole)
        entry = self.entries.get(str(kind))
        if entry is None:
            self.avatar.setText("?"); self.title.setText("???")
            self.subtitle.setText(f"Resource #{kind:03d} · Not yet battled")
            self._empty("Information locked. Battle this Digimon to unlock it.")
            return
        image = self.avatars.get(kind)
        if image is not None:
            self.avatar.setPixmap(QPixmap.fromImage(image).scaled(
                120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.avatar.setText("…")
        self.title.setText(entry["name"])
        self.subtitle.setText(f"Resource #{kind:03d} · {len(entry.get('placements', []))} decoded placements · "
                              f"{len(entry.get('profiles', []))} observed battle variants")
        for index, (name, browser) in enumerate(self.sections.items()):
            self.tabs.setTabEnabled(index, True)
            browser.setHtml(self._html(entry, name))
            browser.verticalScrollBar().setValue(0)

    def _skill(self, text, move_id):
        move = self.moves.get(move_id) if move_id is not None else None
        if move is None:
            return f'<tr><td><b>{html.escape(text)}</b></td><td colspan="5">Metadata not available</td></tr>'
        element = move["element"]
        tint = ELEMENT_COLORS[element][0]
        status = STATUSES.get(move["status"], "—")
        status_color = STATUS_COLORS.get(move["status"], "#bcd8f2")
        return (f'<tr bgcolor="#152a43"><td><img src="element:{element}" width="18" height="18"> '
                f'<b>{html.escape(text)}</b></td><td style="color:{tint}">{ELEMENTS[element]}</td>'
                f'<td>{move["power"]}</td><td>{move["mp"]}</td>'
                f'<td>{RANGES.get(move["range"], "Unknown")}</td><td style="color:{status_color}">{status}</td></tr>')

    def _html(self, entry, section=None):
        groups = entry_sections(entry, self.map_names)
        pages = groups[section] if section else [page for group in groups.values() for page in group]
        if section == "Skills":
            skills = [row for page in pages for row in page if row[0] == "skill"]
            if skills:
                pages = [[("header", "Linked skills (species)", None), *skills]]  # one table, no in-game page breaks
        parts = ['<body style="color:#ffffff; font-size:14px; margin:12px">']
        if section == "Decoded locations":
            parts.append('<p style="color:#bcd8f2">Decoded map placements are possibilities, not confirmed sightings. '
                         'Story and scripts can control availability.</p>')
        elif section == "Observed encounters":
            parts.append('<p style="color:#bcd8f2">Recorded battle variants, including their stats and equipped techniques. '
                         'Observed hours do not establish an exclusive schedule.</p>')
        elif section == "Skills":
            parts.append('<p style="color:#bcd8f2">Species-linked techniques. Actual equipped moves are listed under Observed encounters. '
                         'MP values are the displayed in-game cost.</p>')
        if not pages:
            parts.append('<p>No decoded placements recorded.</p>' if section == "Decoded locations"
                         else '<p>No observed battle variants recorded.</p>')
        previous_header = None
        for page in pages:
            rows = []

            def flush():
                if rows:
                    parts.append('<table width="100%" cellspacing="2" cellpadding="7"><tr bgcolor="#29435e">'
                                 '<th align="left">Technique</th><th>Element</th><th>Power</th><th>MP</th>'
                                 '<th>Range</th><th>Status</th></tr>' + "".join(rows) + "</table>")
                    rows.clear()

            for style, text, skill in page:
                if style != "skill":
                    flush()
                if style == "header":
                    if section != "Skills" or text != previous_header:
                        parts.append(f'<h3 style="color:#ffe083; background:#3b2f0e; margin:18px 0 10px 0; '
                                     f'padding:7px; border-bottom:1px solid #d9ba70">{html.escape(text)}</h3>')
                    previous_header = text
                elif style == "skill":
                    rows.append(self._skill(text, skill))
                elif style == "note":
                    parts.append(f'<p style="color:#9dbbd6; margin:8px 0"><i>{html.escape(text)}</i></p>')
                elif style == "stat":
                    labels = {"HP": "Max HP", "MP": "Max MP", "Off": "Offense", "Def": "Defense"}
                    cells = "".join(f'<td width="25%" style="color:#f0c860">{html.escape(labels.get(label, label))}</td>'
                                    f'<td width="25%"><b>{html.escape(value)}</b></td>'
                                    for label, _, value in (part.rpartition(" ") for part in text.split("   ")))
                    parts.append(f'<table width="100%" cellpadding="5" cellspacing="0" bgcolor="#152a43"><tr>{cells}</tr></table>')
                elif ": " in text:
                    label, value = text.split(": ", 1)
                    parts.append(f'<p style="margin:7px 0"><span style="color:#f0c860">{html.escape(label)}:</span> '
                                 f"<b>{html.escape(value)}</b></p>")
                else:
                    parts.append(f'<p style="margin:2px 0">{html.escape(text)}</p>')
            flush()
        return "".join(parts) + "</body>"
