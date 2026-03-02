from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtCore import Qt, QRect

from flexlux.platform_ui import get_ui_config


class OverlayWindow(QWidget):
    def __init__(self, geometry=None):
        super().__init__()
        self.alpha = 0
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.setWindowFlags(get_ui_config().overlay_flags)

        if geometry is None:
            desktop = QApplication.desktop()
            total_rect = QRect()
            for i in range(desktop.screenCount()):
                total_rect = total_rect.united(desktop.screenGeometry(i))
            geometry = total_rect
        
        self.setGeometry(geometry)
        self.show()

    def setTransparency(self, alpha):
        self.alpha = alpha
        self.repaint()

    def paintEvent(self, _):
        painter = QPainter(self)
        color = QColor(0, 0, 0, self.alpha)
        painter.fillRect(self.rect(), color)
