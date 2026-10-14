from PyQt5.QtCore import Qt, QPointF, QRectF, QSize
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PyQt5.QtWidgets import QCheckBox


class PanelCheckBox(QCheckBox):
    """Checkbox drawn by FlexLux so it looks the same on every platform.

    The stylesheet still sets the label's font and color.
    """

    _BOX = 14
    _GAP = 6

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        # Layouts otherwise use the platform style's checkbox margins, which
        # can overlap the label with neighboring widgets.
        self.setAttribute(Qt.WA_LayoutUsesWidgetRect)

    def sizeHint(self):
        metrics = self.fontMetrics()
        width = self._BOX + self._GAP + metrics.horizontalAdvance(self.text())
        return QSize(width, max(self._BOX, metrics.height()) + 4)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(0.5, (self.height() - self._BOX) / 2 + 0.5, self._BOX - 1, self._BOX - 1)
        dim = 1.0 if self.isEnabled() else 0.4

        border = QColor("#8a8a8a" if self.isChecked() else "#4a4a4a")
        fill = QColor("#3a3a3a" if self.isChecked() else "#1c1c1c")
        if self.underMouse() and self.isEnabled():
            border = QColor("#aaaaaa")
        border.setAlphaF(dim)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3, 3)

        if self.isChecked():
            check = QPainterPath(QPointF(box.left() + 3.2, box.center().y() + 0.2))
            check.lineTo(QPointF(box.left() + 5.8, box.bottom() - 3.4))
            check.lineTo(QPointF(box.right() - 3, box.top() + 3.6))
            color = QColor("#ffffff")
            color.setAlphaF(dim)
            painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(check)

        text_color = self.palette().color(QPalette.WindowText)
        text_color.setAlphaF(dim)
        painter.setPen(text_color)
        painter.setFont(self.font())
        text_rect = QRectF(self._BOX + self._GAP, 0, self.width() - self._BOX - self._GAP, self.height())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self.text())

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)
