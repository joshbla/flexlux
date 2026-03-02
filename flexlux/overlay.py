import platform
import logging

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtCore import Qt, QRect, QTimer

from flexlux.platform_ui import get_ui_config

log = logging.getLogger("FlexLux")


def _apply_macos_overlay_config(widget):
    """Set NSWindow properties so the overlay stays above fullscreen content
    and is invisible in Mission Control.  Called after the Qt window is fully
    realised; the Qt-level flags are left untouched."""
    import ctypes

    try:
        objc = ctypes.cdll.LoadLibrary('/usr/lib/libobjc.A.dylib')
    except OSError:
        log.warning("Could not load libobjc — macOS overlay config skipped")
        return

    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    send = ctypes.cast(
        objc.objc_msgSend,
        ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
    send_long = ctypes.cast(
        objc.objc_msgSend,
        ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long))
    send_ulong = ctypes.cast(
        objc.objc_msgSend,
        ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
    send_bool = ctypes.cast(
        objc.objc_msgSend,
        ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool))

    view = int(widget.winId())
    ns_window = send(view, objc.sel_registerName(b'window'))
    if not ns_window:
        log.warning("Could not obtain NSWindow from overlay — config skipped")
        return

    # Window level: kCGScreenSaverWindowLevel (1000).
    send_long(ns_window, objc.sel_registerName(b'setLevel:'), 1000)

    # Collection behaviour — one flag from each mutually-exclusive group:
    #   canJoinAllSpaces    (1 << 0 = 1)
    #   stationary           (1 << 4 = 16)   — no tile in Mission Control
    #   ignoresCycle         (1 << 6 = 64)   — skip Cmd+` cycling
    #   fullScreenAuxiliary  (1 << 8 = 256)  — allowed on fullscreen Spaces
    send_ulong(ns_window, objc.sel_registerName(b'setCollectionBehavior:'), 1 | 16 | 64 | 256)

    send_bool(ns_window, objc.sel_registerName(b'setHidesOnDeactivate:'), False)
    send_bool(ns_window, objc.sel_registerName(b'setIgnoresMouseEvents:'), True)

    log.info("macOS overlay: NSWindow %#x  level=1000  behavior=337", ns_window)


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

        if platform.system() == 'Darwin':
            QTimer.singleShot(0, lambda: _apply_macos_overlay_config(self))

    def setTransparency(self, alpha):
        self.alpha = alpha
        self.repaint()

    def paintEvent(self, _):
        painter = QPainter(self)
        color = QColor(0, 0, 0, self.alpha)
        painter.fillRect(self.rect(), color)
