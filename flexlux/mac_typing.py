"""Typing into the tray panel on macOS.

The panel is a Qt.Popup, which Qt backs with an NSPanel that refuses to
become the key window, and FlexLux runs as a menu bar (LSUIElement) app that
a tray click does not activate. Keystrokes therefore go to whichever app is in
front, which rejects them with the system beep. While the user types into a
field, the panel is allowed to become key and FlexLux is activated; when the
panel closes, the app that was in front before gets the keyboard back.

Outside typing the panel keeps refusing key status, so using a slider never
takes the keyboard from the app in front."""
import ctypes
import logging
import os

from PyQt5.QtCore import QEvent, QObject, QTimer

log = logging.getLogger("FlexLux")

# Waits for an outside click that closed the panel to activate the clicked app
# before deciding whether to hand the keyboard back.
_RETURN_DELAY_MS = 150

_CAN_BECOME_KEY = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


class _ObjC:
    def __init__(self):
        lib = ctypes.cdll.LoadLibrary('/usr/lib/libobjc.A.dylib')
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.class_getInstanceMethod.restype = ctypes.c_void_p
        lib.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.method_getImplementation.restype = ctypes.c_void_p
        lib.method_getImplementation.argtypes = [ctypes.c_void_p]
        lib.class_replaceMethod.restype = ctypes.c_void_p
        lib.class_replaceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        self.lib = lib
        self.send = ctypes.cast(
            lib.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        self.send_int = ctypes.cast(
            lib.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int))
        self.send_ulong = ctypes.cast(
            lib.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
        self.send_bool = ctypes.cast(
            lib.objc_msgSend, ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool))
        self.ask_bool = ctypes.cast(
            lib.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p))
        self.ask_int = ctypes.cast(
            lib.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p))

    def sel(self, name):
        return self.lib.sel_registerName(name)

    def cls(self, name):
        return self.lib.objc_getClass(name)


class PanelTyping(QObject):
    """Event filter for the panel's text fields: a press in one of them lets
    the panel take the keyboard until the panel is hidden."""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        # The panel's NSWindow while it may take the keyboard.
        self._typing_window = None
        self._previous_pid = None
        self._objc = _ObjC()
        self._original_can_become_key = None
        # Kept on the instance so the callback outlives its use by AppKit.
        self._can_become_key = _CAN_BECOME_KEY(self._panel_can_become_key)
        ns_app_class = self._objc.cls(b'NSApplication')
        self._ns_app = self._objc.send(ns_app_class, self._objc.sel(b'sharedApplication'))

    def watch(self, widget):
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            self._take_keyboard()
        return False

    def _ns_window(self):
        o = self._objc
        return o.send(int(self._panel.winId()), o.sel(b'window'))

    def _panel_can_become_key(self, ns_window, cmd):
        if ns_window == self._typing_window:
            return True
        return self._original_can_become_key(ns_window, cmd)

    def _allow_key_window(self, ns_window):
        """Route canBecomeKeyWindow for the panel's window class through
        _panel_can_become_key. The class is Qt's, so its other windows (menus,
        tooltips) keep Qt's answer."""
        if self._original_can_become_key is not None:
            return
        o = self._objc
        window_class = o.send(ns_window, o.sel(b'class'))
        selector = o.sel(b'canBecomeKeyWindow')
        original = o.lib.method_getImplementation(o.lib.class_getInstanceMethod(window_class, selector))
        self._original_can_become_key = _CAN_BECOME_KEY(original)
        o.lib.class_replaceMethod(window_class, selector, ctypes.cast(self._can_become_key, ctypes.c_void_p), b'c@:')

    def _take_keyboard(self):
        o = self._objc
        ns_window = self._ns_window()
        if not ns_window:
            log.warning("Could not obtain the panel's NSWindow, typing stays with the front app")
            return
        self._allow_key_window(ns_window)
        self._typing_window = ns_window
        if not o.ask_bool(self._ns_app, o.sel(b'isActive')):
            workspace = o.send(o.cls(b'NSWorkspace'), o.sel(b'sharedWorkspace'))
            front = o.send(workspace, o.sel(b'frontmostApplication'))
            pid = o.ask_int(front, o.sel(b'processIdentifier')) if front else None
            self._previous_pid = pid if pid != os.getpid() else None
            o.send_bool(self._ns_app, o.sel(b'activateIgnoringOtherApps:'), True)
        # Makes the panel's view first responder as well as the window key.
        self._panel.activateWindow()

    def release(self):
        """Call when the panel hides."""
        if self._typing_window is None:
            return
        self._typing_window = None
        pid = self._previous_pid
        self._previous_pid = None
        if pid is not None:
            QTimer.singleShot(_RETURN_DELAY_MS, lambda: self._return_keyboard(pid))

    def _return_keyboard(self, pid):
        o = self._objc
        # An outside click that closed the panel has already activated the
        # app it landed on; only a panel closed from FlexLux itself (the tray
        # icon, Escape) leaves FlexLux in front with nothing to type into.
        if self._panel.isVisible() or not o.ask_bool(self._ns_app, o.sel(b'isActive')):
            return
        previous = o.send_int(o.cls(b'NSRunningApplication'),
                              o.sel(b'runningApplicationWithProcessIdentifier:'), pid)
        if previous:
            o.send_ulong(previous, o.sel(b'activateWithOptions:'), 0)
