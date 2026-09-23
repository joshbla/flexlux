import platform
import logging

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtCore import Qt, QRect, QTimer

from flexlux.platform_ui import get_ui_config

log = logging.getLogger("FlexLux")


class _WindowsOverlayStacking:
    """Repair actual z-order, even when Windows still reports WS_EX_TOPMOST."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._types = wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._dwm = ctypes.WinDLL("dwmapi")
        self._callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._user32.EnumWindows.argtypes = [self._callback_type, wintypes.LPARAM]
        self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user32.IsIconic.argtypes = [wintypes.HWND]
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self._get_style = self._user32.GetWindowLongPtrW
        else:
            self._get_style = self._user32.GetWindowLongW
        self._get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self._get_style.restype = ctypes.c_ssize_t
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self._dwm.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        self._failure_logged = False

    def ensure_above_apps(self, overlay):
        c, w, user32 = self._ctypes, self._types, self._user32
        bounds = w.RECT()
        if not user32.GetWindowRect(overlay, c.byref(bounds)):
            return
        process = w.DWORD()
        if not user32.GetWindowThreadProcessId(overlay, c.byref(process)):
            return
        found_overlay = False
        obstructed = False
        own_ui = []

        def inspect(hwnd, _):
            nonlocal found_overlay, obstructed
            if hwnd == overlay:
                found_overlay = True
                return True
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            pid = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, c.byref(pid))
            style = self._get_style(hwnd, -20)
            if pid.value == process.value:
                # Preserve the panel, menus and tooltips above repaired overlays.
                # Click-through windows are our other dimming overlays.
                if not style & 0x20:  # WS_EX_TRANSPARENT
                    own_ui.append(hwnd)
                return True
            if found_overlay or obstructed:
                return True
            # Tool windows and non-activating system surfaces (task switcher,
            # notifications, menus) legitimately live above ordinary apps.
            if style & (0x80 | 0x08000000):  # TOOLWINDOW | NOACTIVATE
                return True
            name = c.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, len(name))
            if name.value in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "MultitaskingViewFrame"):
                return True
            cloaked = w.DWORD()
            if self._dwm.DwmGetWindowAttribute(hwnd, 14, c.byref(cloaked), c.sizeof(cloaked)) != 0:
                return True
            if cloaked.value:
                return True
            rect = w.RECT()
            if user32.GetWindowRect(hwnd, c.byref(rect)):
                obstructed = (rect.left < bounds.right and rect.right > bounds.left
                              and rect.top < bounds.bottom and rect.bottom > bounds.top)
            return True

        # Avoid walking a changing GetWindow chain. Enumerate our UI below the
        # overlay too, so repairs preserve it.
        callback = self._callback_type(inspect)
        if not user32.EnumWindows(callback, 0) or not found_overlay:
            return
        if not obstructed and self._get_style(overlay, -20) & 0x8:
            return
        if not self._raise_without_activation(overlay):
            return
        # Bottom-to-top preserves the relative order of our visible UI windows.
        for hwnd in reversed(own_ui):
            if not self._raise_without_activation(hwnd):
                return
        self._failure_logged = False
        log.debug("Restored Windows dimming overlay stacking (hwnd=%#x)", overlay)

    def _raise_without_activation(self, hwnd):
        # HWND_TOPMOST; SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE.
        # Never alter focus, geometry, alpha, or click-through styles.
        if self._user32.SetWindowPos(hwnd, self._types.HWND(-1), 0, 0, 0, 0, 0x0013):
            return True
        if not self._failure_logged:
            log.warning("Could not restore Windows overlay stacking: %s",
                        self._ctypes.WinError(self._ctypes.get_last_error()))
            self._failure_logged = True
        return False


def _apply_macos_overlay_config(widget, target_geometry):
    """Set NSWindow properties so the overlay stays above fullscreen content
    and is invisible in Mission Control.  Called after the Qt window is fully
    realised; the Qt-level flags are left untouched.

    Also forces the NSWindow frame to *target_geometry* (a QRect in Qt logical
    coordinates) via Cocoa, bypassing macOS constraints that can shrink
    frameless Qt windows to less than full-screen size."""
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

    # Force NSWindow frame to the exact target screen geometry.
    # macOS can silently constrain frameless Qt windows to ~20px less than
    # full-screen size; setting the frame at the Cocoa level bypasses this.
    class CGPoint(ctypes.Structure):
        _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]

    class CGSize(ctypes.Structure):
        _fields_ = [('width', ctypes.c_double), ('height', ctypes.c_double)]

    class CGRect(ctypes.Structure):
        _fields_ = [('origin', CGPoint), ('size', CGSize)]

    # Convert the target rect (Qt global coords, origin at the primary screen's
    # top-left, Y growing downward) into a Cocoa global frame (origin at the
    # primary screen's bottom-left, Y growing upward). The window's bottom edge
    # in Qt coords is target.y()+height(); in Cocoa that same edge sits at
    # primary_h - (target.y()+height()). This offset is anchored to the PRIMARY
    # screen's height for every overlay, regardless of which screen the target is
    # on, because both coordinate spaces share the primary screen as their origin
    # reference. Verified correct for: target == primary (y=0 -> cocoa_y=0), a
    # screen above the primary (negative Qt y -> cocoa_y near/above primary_h),
    # and a screen beside the primary with a different height. primary_h must be
    # the primary screen's FULL geometry height (geometry(), not
    # availableGeometry()) and the target must be Qt global geometry; both hold
    # here (target comes from QDesktopWidget.screenGeometry, which is global, and
    # geometry() includes the menu-bar area just as the Cocoa primary frame does).
    # On HiDPI displays Qt geometry() and NSWindow setFrame both use logical
    # points, so no devicePixelRatio scaling is needed.
    primary_h = QApplication.primaryScreen().geometry().height()
    cocoa_x = float(target_geometry.x())
    cocoa_y = float(primary_h - target_geometry.y() - target_geometry.height())
    frame = CGRect(
        CGPoint(cocoa_x, cocoa_y),
        CGSize(float(target_geometry.width()), float(target_geometry.height())))

    send_frame = ctypes.cast(
        objc.objc_msgSend,
        ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, CGRect, ctypes.c_bool))
    send_frame(ns_window, objc.sel_registerName(b'setFrame:display:'), frame, True)

    log.info("macOS overlay: NSWindow %#x  level=1000  behavior=337  frame=(%g,%g %gx%g)",
             ns_window, cocoa_x, cocoa_y, target_geometry.width(), target_geometry.height())


class OverlayWindow(QWidget):
    def __init__(self, geometry=None):
        super().__init__()
        self.alpha = 0
        self._stacking = None
        self._stack_timer = None
        if platform.system() == 'Windows':
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self._stacking = _WindowsOverlayStacking()
            self._stack_timer = QTimer(self)
            self._stack_timer.setInterval(500)
            self._stack_timer.timeout.connect(self._ensure_stacking)
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

        self._target_geometry = geometry
        self.setGeometry(geometry)
        self.show()

        if platform.system() == 'Darwin':
            geo = self._target_geometry
            QTimer.singleShot(0, lambda: _apply_macos_overlay_config(self, geo))

    def setTransparency(self, alpha):
        self.alpha = alpha
        self.repaint()
        if self._stack_timer is not None:
            if alpha > 0 and self.isVisible():
                self._ensure_stacking()
                if not self._stack_timer.isActive():
                    self._stack_timer.start()
            else:
                self._stack_timer.stop()

    def _ensure_stacking(self):
        if self._stacking is not None and self.alpha > 0 and self.isVisible():
            self._stacking.ensure_above_apps(int(self.winId()))

    def showEvent(self, event):
        super().showEvent(event)
        if self._stack_timer is not None and self.alpha > 0:
            self._ensure_stacking()
            self._stack_timer.start()

    def hideEvent(self, event):
        if self._stack_timer is not None:
            self._stack_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _):
        painter = QPainter(self)
        color = QColor(0, 0, 0, self.alpha)
        painter.fillRect(self.rect(), color)
