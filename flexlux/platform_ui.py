import math
import platform
from dataclasses import dataclass

from PyQt5.QtCore import Qt, QEvent, QPointF, QRectF, QVariantAnimation, QEasingCurve
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QToolTip, QWidget


@dataclass(frozen=True)
class PlatformUIConfig:
    overlay_flags: int
    app_window_flags: int
    slider_handle_width: int
    slider_handle_radius: int
    slider_handle_margin: int
    slider_groove_height: int
    slider_max_height: int
    zone_label_font_size: int
    mid_label_width: int
    panel_y_from_top: bool
    panel_top_offset: int
    panel_bottom_offset: int
    use_event_filter: bool
    use_manual_tray_menu: bool
    compact_panel: bool


_MACOS = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus,
    # Qt.Popup (not Qt.Tool): Tool windows map to NSPanel with
    # hidesOnDeactivate, so macOS refuses to show them while the app is
    # inactive (tray clicks don't activate the app on modern macOS), and Qt's
    # isVisible() desyncs from reality once Cocoa hides the panel — making
    # tray clicks appear to do nothing. Popup windows show without app
    # activation and auto-close on outside clicks with Qt state kept in sync.
    app_window_flags=Qt.Popup | Qt.FramelessWindowHint,
    slider_handle_width=24,
    slider_handle_radius=12,
    slider_handle_margin=-8,
    slider_groove_height=6,
    slider_max_height=40,
    zone_label_font_size=9,
    mid_label_width=6,
    panel_y_from_top=True,
    panel_top_offset=30,
    panel_bottom_offset=50,
    use_event_filter=False,
    use_manual_tray_menu=True,
    compact_panel=False,
)

_WINDOWS = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool,
    app_window_flags=Qt.Tool | Qt.FramelessWindowHint,
    slider_handle_width=50,
    slider_handle_radius=25,
    slider_handle_margin=-20,
    slider_groove_height=10,
    slider_max_height=70,
    zone_label_font_size=14,
    mid_label_width=10,
    panel_y_from_top=False,
    panel_top_offset=30,
    panel_bottom_offset=50,
    use_event_filter=True,
    use_manual_tray_menu=False,
    compact_panel=True,
)

_LINUX = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.X11BypassWindowManagerHint,
    app_window_flags=Qt.Tool | Qt.FramelessWindowHint,
    slider_handle_width=50,
    slider_handle_radius=25,
    slider_handle_margin=-20,
    slider_groove_height=10,
    slider_max_height=70,
    zone_label_font_size=14,
    mid_label_width=10,
    panel_y_from_top=False,
    panel_top_offset=30,
    panel_bottom_offset=50,
    use_event_filter=True,
    use_manual_tray_menu=False,
    compact_panel=False,
)


class WindowsBrightnessIcon(QWidget):
    """Resolution-independent endpoint icons for the Windows panel."""

    def __init__(self, moon, tooltip, parent):
        super().__init__(parent)
        self._moon = moon
        self.setFixedSize(20, 44)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        color = QColor("#a6bdca" if self._moon else "#c6cdd5")
        painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if self._moon:
            disk = QPainterPath()
            disk.addEllipse(QRectF(-7, -7, 14, 14))
            cutout = QPainterPath()
            cutout.addEllipse(QRectF(-2, -10, 13, 13))
            painter.drawPath(disk.subtracted(cutout))
        else:
            painter.drawEllipse(QPointF(0, 0), 3.5, 3.5)
            for ray in range(8):
                angle = ray * math.pi / 4
                painter.drawLine(QPointF(6 * math.cos(angle), 6 * math.sin(angle)),
                                 QPointF(8.5 * math.cos(angle), 8.5 * math.sin(angle)))


class WindowsBrightnessSlider(QSlider):
    """Native slider input, with a quiet hover animation and on-demand hints."""

    def __init__(self, hw_capable, min_brightness, parent):
        super().__init__(Qt.Horizontal, parent)
        self._hw_capable = hw_capable
        self._min_brightness = min_brightness
        self._emphasis = 0.0
        self._hovered = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._animate)
        self.setRange(0, 200 if hw_capable else 100)
        self.setValue(100)
        self.setFixedHeight(44)
        self.setMinimumWidth(100)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Adjust brightness")
        description = "Move left to dim the screen beyond its hardware minimum."
        if hw_capable:
            description += " The center removes extra dimming; move right to increase monitor brightness."
        else:
            description += " Move fully right to remove extra dimming. Monitor brightness control is unavailable."
        self.setAccessibleDescription(description)
        self.setStyleSheet("""
            QSlider { background: transparent; padding: 0 5px; }
            QSlider::groove:horizontal {
                height: 4px; background: #3b424c; border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #a6bdca; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 18px; margin: -7px 0;
                background: #f1f5f9; border-radius: 9px;
            }
            QSlider::handle:horizontal:pressed { background: #c5e0ec; }
        """)
        self.sliderPressed.connect(self._show_hint)
        self.sliderReleased.connect(QToolTip.hideText)
        self.valueChanged.connect(self._value_changed)

    def _hint(self):
        value = self.value()
        if value < 100:
            darkness = round((100 - value) * 0.9)
            return f"Screen dimming  ·  {darkness}%"
        if not self._hw_capable:
            return "No extra dimming"
        percent = round(self._min_brightness + (100 - self._min_brightness) * (value - 100) / 100)
        return f"Monitor brightness  ·  {percent}%"

    def _show_hint(self):
        point = self.mapToGlobal(self.rect().topLeft())
        point.setY(point.y() - 36)
        QToolTip.showText(point, self._hint(), self, self.rect(), 2500)

    def _value_changed(self, value):
        if self.isSliderDown():
            self._show_hint()

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            self._show_hint()
            return True
        return super().event(event)

    def _update_emphasis(self):
        self._animation.stop()
        self._animation.setStartValue(self._emphasis)
        self._animation.setEndValue(1.0 if self._hovered or self.hasFocus() else 0.0)
        self._animation.start()

    def _animate(self, value):
        self._emphasis = value
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self._update_emphasis()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._update_emphasis()
        if not self.isSliderDown():
            QToolTip.hideText()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._update_emphasis()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._update_emphasis()
        QToolTip.hideText()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._hovered = False
        self._animation.stop()
        self._emphasis = 0.0
        QToolTip.hideText()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp, Qt.Key_PageDown):
            self._show_hint()

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        center = QRectF(handle).center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._hw_capable:
            # A small neutral-position marker replaces the persistent zone labels.
            painter.setPen(QPen(QColor("#7b8795"), 1, Qt.SolidLine, Qt.RoundCap))
            x = self.width() / 2
            painter.drawLine(QPointF(x, center.y() + 12), QPointF(x, center.y() + 15))
        if self._emphasis > 0:
            color = QColor("#a6bdca")
            color.setAlpha(round(110 * self._emphasis))
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(Qt.NoBrush)
            radius = 11 + 2 * self._emphasis
            painter.drawEllipse(center, radius, radius)


def get_ui_config() -> PlatformUIConfig:
    system = platform.system()
    if system == "Darwin":
        return _MACOS
    elif system == "Windows":
        return _WINDOWS
    return _LINUX
