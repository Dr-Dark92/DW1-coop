from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (QBrush, QColor, QImage, QMatrix4x4, QMouseEvent,
                           QPainter, QPainterPath, QPen, QQuaternion,
                           QTransform, QVector3D, QWheelEvent)
from PySide6.QtWidgets import QWidget

from .mmd import StaticModel


class StaticModelView(QWidget):
    """Small dependency-free Qt raster preview; decoded coordinates are never modified."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self.model: StaticModel | None = None
        self.palettes: tuple[QImage, ...] = ()
        self.clut_y_base = 0
        self.textured = True
        self.wireframe = False
        self.yaw, self.pitch, self.zoom = -35.0, -12.0, 1.0
        self._last = None
        self._triangles = []
        self._center = QVector3D()
        self._radius = 1.0

    def reset_view(self):
        self.yaw, self.pitch, self.zoom = -35.0, -12.0, 1.0
        self.update()

    def set_modes(self, textured: bool, wireframe: bool):
        self.textured, self.wireframe = textured, wireframe
        self.update()

    @staticmethod
    def _local_matrix(pose):
        matrix = QMatrix4x4()
        matrix.translate(pose.translation.x, pose.translation.y, pose.translation.z)
        rx, ry, rz = pose.rotation.x, pose.rotation.y, pose.rotation.z
        cx, sx, cy, sy, cz, sz = math.cos(rx / 2), math.sin(rx / 2), math.cos(ry / 2), math.sin(ry / 2), math.cos(rz / 2), math.sin(rz / 2)
        quaternion = QQuaternion(cx * cy * cz - sx * sy * sz,
                                 sx * cy * cz + cx * sy * sz,
                                 -sx * cy * sz + cx * sy * cz,
                                 cx * cy * sz + sx * sy * cz)
        matrix.rotate(quaternion)
        matrix.scale(pose.scale.x, pose.scale.y, pose.scale.z)
        return matrix

    def set_model(self, model: StaticModel, palettes: tuple[QImage, ...], clut_y_base: int):
        self.model, self.palettes, self.clut_y_base = model, palettes, clut_y_base
        world = []
        for index, node in enumerate(model.skeleton):
            local = self._local_matrix(model.pose[index])
            world.append(local if node.parent_index is None else world[node.parent_index] * local)
        object_world = {}
        for index, node in enumerate(model.skeleton):
            if node.object_index is not None:
                object_world[node.object_index] = world[index]
        triangles, points = [], []
        for object_index, obj in enumerate(model.objects):
            matrix = object_world.get(object_index, QMatrix4x4())
            # PS1 model space has screen-down Y. Flip only in the renderer so decoded
            # coordinates and exported provenance remain untouched.
            mapped = [matrix.map(QVector3D(vertex.x, vertex.y, vertex.z)) for vertex in obj.vertices]
            vertices = [QVector3D(point.x(), -point.y(), point.z()) for point in mapped]
            points.extend(vertices)
            for face in obj.triangles:
                triangles.append((tuple(vertices[i] for i in face.vertex_indices), face, object_index))
        if not points:
            return
        self._center = sum(points[1:], points[0]) / len(points)
        self._radius = max((point - self._center).length() for point in points) or 1.0
        self._triangles = triangles
        self.reset_view()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last = event.position()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._last is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.position() - self._last
            self.yaw += delta.x() * 0.6
            self.pitch = max(-89.0, min(89.0, self.pitch + delta.y() * 0.6))
            self._last = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._last = None

    def wheelEvent(self, event: QWheelEvent):
        self.zoom = max(0.25, min(5.0, self.zoom * math.pow(1.0015, event.angleDelta().y())))
        self.update()

    @staticmethod
    def _texture_transform(source, target):
        (u0, v0), (u1, v1), (u2, v2) = source
        x0, y0 = target[0].x(), target[0].y()
        x1, y1 = target[1].x(), target[1].y()
        x2, y2 = target[2].x(), target[2].y()
        determinant = u0 * (v1 - v2) + u1 * (v2 - v0) + u2 * (v0 - v1)
        if abs(determinant) < 1e-6:
            return None
        a = (x0 * (v1 - v2) + x1 * (v2 - v0) + x2 * (v0 - v1)) / determinant
        b = (y0 * (v1 - v2) + y1 * (v2 - v0) + y2 * (v0 - v1)) / determinant
        c = (x0 * (u2 - u1) + x1 * (u0 - u2) + x2 * (u1 - u0)) / determinant
        d = (y0 * (u2 - u1) + y1 * (u0 - u2) + y2 * (u1 - u0)) / determinant
        tx = (x0 * (u1 * v2 - u2 * v1) + x1 * (u2 * v0 - u0 * v2) + x2 * (u0 * v1 - u1 * v0)) / determinant
        ty = (y0 * (u1 * v2 - u2 * v1) + y1 * (u2 * v0 - u0 * v2) + y2 * (u0 * v1 - u1 * v0)) / determinant
        return QTransform(a, b, c, d, tx, ty)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#20242b"))
        if not self._triangles:
            painter.setPen(QColor("#d0d4da")); painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load a verified static model")
            return
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
        distance = self._radius * 3.2 / self.zoom
        focal = min(self.width(), self.height()) * 0.95

        def project(point):
            p = point - self._center
            x, z = cy * p.x() + sy * p.z(), -sy * p.x() + cy * p.z()
            y, z = cp * p.y() - sp * z, sp * p.y() + cp * z
            depth = z + distance
            return QPointF(self.width() / 2 + x * focal / depth,
                           self.height() / 2 - y * focal / depth), depth

        rendered = []
        for vertices, face, object_index in self._triangles:
            projected = [project(vertex) for vertex in vertices]
            if min(value[1] for value in projected) <= 1:
                continue
            rendered.append((sum(value[1] for value in projected) / 3,
                             tuple(value[0] for value in projected), face, object_index))
        rendered.sort(reverse=True, key=lambda item: item[0])
        for _, points, face, object_index in rendered:
            path = QPainterPath(points[0]); path.lineTo(points[1]); path.lineTo(points[2]); path.closeSubpath()
            palette_index = face.material.clut_y - self.clut_y_base
            if self.textured and 0 <= palette_index < len(self.palettes):
                transform = self._texture_transform(tuple((uv.u, uv.v) for uv in face.uvs), points)
                if transform is not None:
                    painter.save(); painter.setTransform(transform)
                    source_path = QPainterPath(QPointF(face.uvs[0].u, face.uvs[0].v))
                    for uv in face.uvs[1:]: source_path.lineTo(uv.u, uv.v)
                    source_path.closeSubpath(); painter.setClipPath(source_path)
                    painter.drawImage(QPointF(0, 0), self.palettes[palette_index]); painter.restore()
            else:
                hue = (object_index * 37) % 360
                painter.fillPath(path, QBrush(QColor.fromHsv(hue, 90, 190)))
            if self.wireframe:
                painter.setPen(QPen(QColor(245, 245, 245, 190), 0.8)); painter.drawPath(path)
        painter.setPen(QColor("#c8ccd2"))
        painter.drawText(10, 20, "drag: orbit  •  wheel: zoom")
