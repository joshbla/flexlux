import platform

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtCore import Qt, QRect


class OverlayWindow(QWidget):
    def __init__(self, geometry=None):
        super().__init__()
        self.alpha = 0
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        
        # Platform-specific window flags
        if platform.system() == "Windows":
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        elif platform.system() == "Darwin":  # macOS — Qt.Tool omitted so overlays persist when the app deactivates
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        else:  # Linux
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.X11BypassWindowManagerHint)
        
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
