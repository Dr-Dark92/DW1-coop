"""Windows Digi-Dex reader. Unknown entries never receive names, avatars or details."""
import html

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QIcon, QPixmap, QTextDocument
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QTextBrowser,
                               QVBoxLayout)
from .digidex import ELEMENTS, RANGES, STATUSES, entry_rows
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
"""


class DigiDexDialog(QDialog):
    def __init__(self, snapshot, map_names=None, parent=None, avatars=None):
        super().__init__(parent)
        self.setWindowTitle("Digi-Dex")
        self.setStyleSheet(STYLE)
        self.resize(860, 600)
        self.entries = snapshot.get("entries", {})
        self.ids = snapshot.get("catalog") or sorted(int(k) for k in self.entries)
        self.moves = snapshot.get("moves") or {}
        self.map_names = map_names or {}
        self.avatars = avatars or {}  # kind -> QImage (avatars.py); missing while still rendering
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Discovered {len(self.entries)} Digimon. Battle a Digimon to unlock its information."))
        self.search = QLineEdit(); self.search.setPlaceholderText("Search discovered Digimon")
        layout.addWidget(self.search)
        content = QHBoxLayout()
        self.list = QListWidget(); self.list.setIconSize(QSize(40, 40))
        self.avatar = QLabel(); self.avatar.setFixedSize(128, 128)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #24456b, stop:1 #0f2038);"
                                  "border: 1px solid #d9ba70; border-radius: 6px; color: #7f93a3; font-size: 40px;")
        self.title = QLabel(); self.title.setStyleSheet("color: #ffe083; font-size: 20px; font-weight: bold;")
        heading = QHBoxLayout(); heading.addWidget(self.avatar); heading.addWidget(self.title, 1)
        self.details = QTextBrowser()
        for element in range(len(ELEMENTS)):
            self.details.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"element:{element}"),
                                                element_icon_image(element, 18))
        detail = QVBoxLayout(); detail.addLayout(heading); detail.addWidget(self.details, 1)
        content.addWidget(self.list, 1); content.addLayout(detail, 2)
        layout.addLayout(content)
        layout.addWidget(QLabel("Times are observed in-game hours. Decoded placements may depend on story/scripts."))
        self.search.textChanged.connect(self.refresh)
        self.list.currentItemChanged.connect(self.show_entry)
        self.refresh()

    def refresh(self):
        self.list.clear()
        term = self.search.text().casefold()
        for kind in self.ids:
            entry = self.entries.get(str(kind))
            if term and (entry is None or term not in entry["name"].casefold()):
                continue
            item = QListWidgetItem(f"#{kind:03d}  " + (entry["name"] if entry else "??? — not battled"))
            item.setData(Qt.ItemDataRole.UserRole, kind)
            if entry is None:
                item.setForeground(Qt.GlobalColor.gray)
            elif kind in self.avatars:
                item.setIcon(QIcon(QPixmap.fromImage(self.avatars[kind])))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.avatar.clear(); self.title.clear()
            self.details.setPlainText("No discovered Digimon match your search." if term else "No encounters yet.")

    def show_entry(self, item, previous=None):
        if item is None:
            return
        kind = item.data(Qt.ItemDataRole.UserRole)
        entry = self.entries.get(str(kind))
        if entry is None:
            self.avatar.setText("?"); self.title.setText("???")
            self.details.setPlainText("Information locked. Battle this Digimon to unlock it.")
            return
        image = self.avatars.get(kind)
        if image is not None:
            self.avatar.setPixmap(QPixmap.fromImage(image).scaled(
                120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.avatar.setText("…")
        self.title.setText(entry["name"])
        self.details.setHtml(self._html(entry))

    def _skill(self, text, move_id):
        move = self.moves.get(move_id) if move_id is not None else None
        if move is None:
            return f'<tr><td></td><td><b>{html.escape(text)}</b></td><td></td></tr>'
        element = move["element"]
        tint = ELEMENT_COLORS[element][0]
        facts = [f'<span style="color:{tint}">{ELEMENTS[element]}</span>',
                 f"Pow {move['power']}" if move["power"] else "No damage", f"MP {move['mp']}",
                 RANGES.get(move["range"], "")]
        if move["status"] in STATUSES:
            facts.append(f'<span style="color:{STATUS_COLORS[move["status"]]}">{STATUSES[move["status"]]}</span>')
        return (f'<tr><td width="24"><img src="element:{element}" width="18" height="18"></td>'
                f'<td style="border-left:3px solid {tint}; padding-left:6px"><b>{html.escape(text)}</b></td>'
                f'<td style="color:#bcd8f2">{" · ".join(f for f in facts if f)}</td></tr>')

    def _html(self, entry):
        parts = ['<body style="color:#ffffff; font-size:13px">']
        for page in entry_rows(entry, self.map_names):
            rows = []
            for style, text, skill in page:
                if style == "header":
                    parts.append(f'<h3 style="color:#ffe083; background:#3b2f0e; margin:10px 0 4px 0; '
                                 f'padding:3px 6px; border-bottom:1px solid #d9ba70">{html.escape(text)}</h3>')
                elif style == "skill":
                    rows.append(self._skill(text, skill))
                elif style == "note":
                    parts.append(f'<p style="color:#8fb0cf; margin:2px 0"><i>{html.escape(text)}</i></p>')
                elif style == "stat":
                    cells = "".join(f'<td style="color:#f0c860; padding-right:4px">{html.escape(label)}</td>'
                                    f'<td style="padding-right:16px"><b>{html.escape(value)}</b></td>'
                                    for label, _, value in (part.rpartition(" ") for part in text.split("   ")))
                    parts.append(f"<table><tr>{cells}</tr></table>")
                elif ": " in text:
                    label, value = text.split(": ", 1)
                    parts.append(f'<p style="margin:2px 0"><span style="color:#f0c860">{html.escape(label)}:</span> '
                                 f"<b>{html.escape(value)}</b></p>")
                else:
                    parts.append(f'<p style="margin:2px 0">{html.escape(text)}</p>')
            if rows:
                parts.append('<table cellspacing="0" cellpadding="3">' + "".join(rows) + "</table>")
        return "".join(parts) + "</body>"
