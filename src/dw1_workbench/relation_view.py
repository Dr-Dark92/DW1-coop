from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView)

from .maps import ZoneLink

NODE_FILL = QColor("#2f6f9f")
NODE_UNLINKED = QColor("#6b7280")
NODE_SELECTED = QColor("#e0a020")
WALK_PEN = QColor("#3fa060")
SCRIPT_PEN = QColor("#9a9fa8")


class ZoneNode(QGraphicsRectItem):
    def __init__(self, zone: str, map_count: int, linked: bool, on_open: Callable[[str], None]):
        self.label = QGraphicsSimpleTextItem(f"{zone}\n{map_count} map{'s' if map_count != 1 else ''}")
        font = QFont(); font.setPointSize(9); self.label.setFont(font)
        bounds = self.label.boundingRect()
        super().__init__(-bounds.width() / 2 - 8, -bounds.height() / 2 - 5, bounds.width() + 16, bounds.height() + 10)
        self.zone, self.on_open, self.fill = zone, on_open, NODE_FILL if linked else NODE_UNLINKED
        self.label.setParentItem(self); self.label.setPos(-bounds.width() / 2, -bounds.height() / 2)
        self.label.setBrush(QBrush(QColor("white")))
        self.setBrush(QBrush(self.fill)); self.setPen(QPen(QColor("#1d2430"), 1))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable); self.setZValue(2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setBrush(QBrush(NODE_SELECTED if value else self.fill))
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        self.on_open(self.zone)


class RelationView(QGraphicsView):
    """Zone relation map: nodes are area-name zones, edges are warps between their maps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#20242b")))
        self.nodes: dict[str, ZoneNode] = {}
        self.script_edges: list[QGraphicsLineItem] = []

    def wheelEvent(self, event: QWheelEvent):
        factor = math.pow(1.0015, event.angleDelta().y())
        self.scale(factor, factor)

    def show_relations(self, zones: dict[str, int], links: list[ZoneLink],
                       positions: dict[str, tuple[float, float]], on_open: Callable[[str], None]):
        scene = self.scene(); scene.clear(); self.nodes.clear(); self.script_edges.clear()
        linked = sorted(positions)
        for zone, count in zones.items():
            node = ZoneNode(zone, count, zone in positions, on_open)
            node.setToolTip(f"{zone}: {count} map(s). Double-click to open in the browser.")
            self.nodes[zone] = node
        if linked:
            xs, ys = [positions[z][0] for z in linked], [positions[z][1] for z in linked]
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
            scale = 2600.0 / span
            origin = (min(xs), min(ys))
            centres = {z: [(positions[z][0] - origin[0]) * scale, (positions[z][1] - origin[1]) * scale] for z in linked}
            self._separate(centres)
            placed = {z: QPointF(*centres[z]) for z in linked}
        else:
            placed = {}
        unlinked = sorted(zone for zone in zones if zone not in placed)
        tray_top = (max((p.y() for p in placed.values()), default=0.0)) + 220
        if unlinked:
            title = scene.addSimpleText("Zones with no decoded warp links (entered through map scripts):")
            title.setBrush(QBrush(QColor("#d0d4da"))); title.setPos(-80, tray_top - 70)
        for i, zone in enumerate(unlinked):
            placed[zone] = QPointF((i % 5) * 300, tray_top + (i // 5) * 80)
        for link in links:
            a, b = placed[link.source], placed[link.target]
            walk = link.walk_through > 0
            pen = QPen(WALK_PEN if walk else SCRIPT_PEN, 1.5 + min(link.walk_through, 12) * 0.4 if walk else 1.2)
            if not walk:
                pen.setStyle(Qt.PenStyle.DashLine)
            edge = scene.addLine(a.x(), a.y(), b.x(), b.y(), pen)
            edge.setZValue(0)
            directions = ", ".join(f"{name} ×{count}" for name, count in sorted((link.directions or {}).items()))
            edge.setToolTip(f"{link.source} ↔ {link.target}\n{link.walk_through} walk-through exit(s)"
                            f"{f' ({link.source} → {link.target}: {directions})' if directions else ''}\n"
                            f"{link.scripted} warp-table link(s) without exit tiles (map script or unused)")
            if not walk:
                self.script_edges.append(edge)
        for zone, node in self.nodes.items():
            node.setPos(placed[zone]); scene.addItem(node)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-80, -80, 80, 80))
        self.fit_all()
        # A whole-map fit makes labels unreadable; start at a legible zoom around the hub instead.
        if self.transform().m11() < 0.75:
            self.resetTransform(); self.scale(0.75, 0.75)
            hub = self.nodes.get("File City") or next(iter(self.nodes.values()), None)
            if hub:
                self.centerOn(hub)

    def fit_all(self):
        self.resetTransform()
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _separate(self, centres: dict[str, list[float]], margin: float = 18.0, iterations: int = 300):
        """Push overlapping node rectangles apart along the axis of least overlap."""
        sizes = {zone: self.nodes[zone].rect() for zone in centres}
        zones = list(centres)
        for _ in range(iterations):
            moved = False
            for i, a in enumerate(zones):
                for b in zones[i + 1:]:
                    (ax, ay), (bx, by) = centres[a], centres[b]
                    overlap_x = (sizes[a].width() + sizes[b].width()) / 2 + margin - abs(ax - bx)
                    overlap_y = (sizes[a].height() + sizes[b].height()) / 2 + margin - abs(ay - by)
                    if overlap_x <= 0 or overlap_y <= 0:
                        continue
                    moved = True
                    if overlap_x < overlap_y:
                        shift = overlap_x / 2 * (1 if ax >= bx else -1)
                        centres[a][0] += shift; centres[b][0] -= shift
                    else:
                        shift = overlap_y / 2 * (1 if ay >= by else -1)
                        centres[a][1] += shift; centres[b][1] -= shift
            if not moved:
                break

    def set_script_links_visible(self, visible: bool):
        for edge in self.script_edges:
            edge.setVisible(visible)

    def select_zone(self, zone: str):
        node = self.nodes.get(zone)
        if node:
            self.scene().clearSelection(); node.setSelected(True); self.centerOn(node)

    def selected_zone(self) -> str | None:
        selected = [item for item in self.scene().selectedItems() if isinstance(item, ZoneNode)]
        return selected[0].zone if selected else None
