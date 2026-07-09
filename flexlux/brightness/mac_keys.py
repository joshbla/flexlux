"""Intercept macOS brightness keys via CGEventTap.

Standalone module — no imports from the rest of FlexLux except PyQt5 for
signals.  If anything fails (missing permissions, unsupported macOS version,
broken event tap), callers get a clean False from start() and the app
continues without this feature.
"""

import ctypes
import logging
import threading

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

# AXIsProcessTrusted lets us distinguish "permission denied" from other
# CGEventTapCreate failures on modern macOS. It ships with pyobjc's
# ApplicationServices; treat it as an optional capability — no hard dependency.
try:
    from ApplicationServices import AXIsProcessTrusted
except ImportError:
    AXIsProcessTrusted = None

NX_SYSDEFINED = 14
NX_KEYTYPE_BRIGHTNESS_UP = 2
NX_KEYTYPE_BRIGHTNESS_DOWN = 3
NX_SUBTYPE_AUX_CONTROL_BUTTONS = 8

_kCGSessionEventTap = 1
_kCGHeadInsertEventTap = 0
_kCGEventTapOptionDefault = 0x00000000
_kCGEventTapDisabledByTimeout = 0xFFFFFFFE
_kCGEventTapDisabledByUserInput = 0xFFFFFFFF

CGEventTapCallBack = ctypes.CFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_void_p,
)

_HEALTH_CHECK_INTERVAL_MS = 30000


class MacBrightnessKeyInterceptor(QObject):
    """Listens for brightness key presses and optionally suppresses them.

    The app controls which directions are intercepted via set_state().  When a
    key is intercepted the system event is suppressed (hardware brightness
    stays unchanged) and brightness_key_pressed is emitted with
    intercepted=True.  Non-intercepted presses are passed through to macOS and
    emitted with intercepted=False so the app can trigger a fast poll.
    """

    brightness_key_pressed = pyqtSignal(str, bool)  # ("up"|"down", intercepted)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._intercept_down = False
        self._intercept_up = False
        self._tap = None
        self._run_loop = None
        self._source = None
        self._thread = None
        self._callback_ref = None
        self._cg = None
        self._cf = None
        self._objc = None
        self._health_timer = None
        self._lock = threading.Lock()
        self._cancelled = False
        # When start() returns False, this is 'permission', 'timeout', or
        # 'error'. None means start() has not been attempted or it succeeded.
        self.failure_reason = None
        self._sel_eventWithCGEvent = None
        self._sel_subtype = None
        self._sel_data1 = None
        self._NSEvent = None
        self._send_ptr_ptr = None
        self._send_long = None
        self._kCFRunLoopCommonModes = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, intercept_down, intercept_up):
        """Tell the interceptor which directions to suppress.

        Called from the main thread; read from the callback thread.  Simple
        boolean writes are atomic enough under the GIL.
        """
        self._intercept_down = intercept_down
        self._intercept_up = intercept_up

    def start(self):
        """Install the event tap and start listening.  Returns True on success.

        On failure, sets self.failure_reason to one of 'permission', 'timeout',
        or 'error' so callers can react appropriately (e.g. prompt for
        Accessibility permission).
        """
        self._cancelled = False
        try:
            self._load_frameworks()
        except Exception as e:
            log.warning("Could not load frameworks for brightness key interception: %s", e)
            self.failure_reason = "error"
            return False

        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self._thread.start()

        if not ready.wait(timeout=3):
            log.warning("Brightness key event tap failed to start within timeout")
            self.failure_reason = "timeout"
            # Signal the background thread to abort before it creates/enters a
            # live tap, then perform a full teardown so a timed-out start()
            # cannot leave an orphaned running tap.
            with self._lock:
                self._cancelled = True
            self.stop()
            return False

        if self._tap is None:
            # failure_reason was already set inside _run.
            return False

        self._health_timer = QTimer(self)
        self._health_timer.setInterval(_HEALTH_CHECK_INTERVAL_MS)
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start()

        log.info("Brightness key interception active")
        self.failure_reason = None
        return True

    def stop(self):
        """Tear down the event tap and background thread.  Safe in every state."""
        if self._health_timer is not None:
            self._health_timer.stop()
            self._health_timer = None
        if self._run_loop is not None and self._cf:
            try:
                self._cf.CFRunLoopStop(self._run_loop)
            except Exception as e:
                log.warning("Failed to stop brightness key run loop: %s", e)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        # Disable the tap and remove the run-loop source before nulling
        # references so the OS stops routing events through us.
        if self._tap is not None and self._cg:
            try:
                self._cg.CGEventTapEnable(self._tap, False)
            except Exception as e:
                log.debug("Failed to disable event tap during stop: %s", e)
        if self._source is not None and self._cf and self._run_loop is not None:
            try:
                self._cf.CFRunLoopRemoveSource(
                    self._run_loop, self._source, self._kCFRunLoopCommonModes)
            except Exception as e:
                log.debug("Failed to remove run loop source during stop: %s", e)
        self._tap = None
        self._source = None
        self._run_loop = None

    def _teardown_tap(self):
        """Disable the tap and remove its run-loop source.

        Called only on the background thread when it bails out after a
        timed-out start() (see _cancelled). The normal stop path is handled by
        stop() above.
        """
        if self._tap is not None and self._cg:
            try:
                self._cg.CGEventTapEnable(self._tap, False)
            except Exception as e:
                log.debug("Failed to disable event tap during teardown: %s", e)
        if self._source is not None and self._cf and self._run_loop is not None:
            try:
                self._cf.CFRunLoopRemoveSource(
                    self._run_loop, self._source, self._kCFRunLoopCommonModes)
            except Exception as e:
                log.debug("Failed to remove run loop source during teardown: %s", e)
        self._tap = None
        self._source = None
        self._run_loop = None

    def _diagnose_tap_failure(self):
        """Record a failure_reason and log when CGEventTapCreate returns NULL."""
        if AXIsProcessTrusted is not None:
            try:
                trusted = AXIsProcessTrusted()
            except Exception as e:
                log.warning("AXIsProcessTrusted check failed: %s", e)
                trusted = None
            if trusted is False:
                self.failure_reason = "permission"
                log.warning(
                    "Brightness key event tap creation failed: Accessibility "
                    "permission not granted. Enable FlexLux under System "
                    "Settings > Privacy & Security > Accessibility to enable "
                    "brightness-key support.")
                return
        self.failure_reason = "error"
        log.warning(
            "CGEventTapCreate returned NULL. If FlexLux lacks permission, "
            "grant Accessibility under System Settings > Privacy & Security > "
            "Accessibility.")

    def is_tap_enabled(self):
        if self._tap and self._cg:
            try:
                return bool(self._cg.CGEventTapIsEnabled(self._tap))
            except Exception as e:
                log.debug("CGEventTapIsEnabled failed: %s", e)
                return False
        return False

    def reenable_tap(self):
        if self._tap and self._cg:
            try:
                self._cg.CGEventTapEnable(self._tap, True)
            except Exception as e:
                log.warning("Failed to re-enable brightness key event tap: %s", e)

    # ------------------------------------------------------------------
    # Framework loading (all ctypes, no PyObjC dependency)
    # ------------------------------------------------------------------

    def _load_frameworks(self):
        cg = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cf = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        objc = ctypes.cdll.LoadLibrary('/usr/lib/libobjc.A.dylib')

        cg.CGEventTapCreate.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint64, CGEventTapCallBack, ctypes.c_void_p]
        cg.CGEventTapCreate.restype = ctypes.c_void_p

        cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        cg.CGEventTapEnable.restype = None

        cg.CGEventTapIsEnabled.argtypes = [ctypes.c_void_p]
        cg.CGEventTapIsEnabled.restype = ctypes.c_bool

        cf.CFMachPortCreateRunLoopSource.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p

        cf.CFRunLoopGetCurrent.argtypes = []
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p

        cf.CFRunLoopAddSource.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRunLoopAddSource.restype = None

        cf.CFRunLoopRemoveSource.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRunLoopRemoveSource.restype = None

        cf.CFRunLoopRun.argtypes = []
        cf.CFRunLoopRun.restype = None

        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        cf.CFRunLoopStop.restype = None

        self._kCFRunLoopCommonModes = ctypes.c_void_p.in_dll(cf, 'kCFRunLoopCommonModes')

        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.objc_getClass.restype = ctypes.c_void_p

        self._sel_eventWithCGEvent = objc.sel_registerName(b'eventWithCGEvent:')
        self._sel_subtype = objc.sel_registerName(b'subtype')
        self._sel_data1 = objc.sel_registerName(b'data1')
        self._NSEvent = objc.objc_getClass(b'NSEvent')

        self._send_ptr_ptr = ctypes.cast(
            objc.objc_msgSend,
            ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p))
        self._send_long = ctypes.cast(
            objc.objc_msgSend,
            ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p))

        self._cg = cg
        self._cf = cf
        self._objc = objc

    # ------------------------------------------------------------------
    # Event parsing
    # ------------------------------------------------------------------

    def _parse_brightness_event(self, cg_event):
        """Return (direction, is_key_down) or None."""
        try:
            ns_event = self._send_ptr_ptr(
                self._NSEvent, self._sel_eventWithCGEvent, cg_event)
            if not ns_event:
                log.debug("eventWithCGEvent: returned nil NSEvent")
                return None

            subtype = self._send_long(ns_event, self._sel_subtype)
            if subtype != NX_SUBTYPE_AUX_CONTROL_BUTTONS:
                return None

            data1 = self._send_long(ns_event, self._sel_data1)
            key_code = (data1 >> 16) & 0xFFFF
            key_state = (data1 >> 8) & 0xFF
            is_key_down = key_state == 0x0A

            if key_code == NX_KEYTYPE_BRIGHTNESS_UP:
                return ("up", is_key_down)
            elif key_code == NX_KEYTYPE_BRIGHTNESS_DOWN:
                return ("down", is_key_down)

            return None
        except Exception as e:
            log.warning("Failed to parse brightness event: %s", e)
            return None

    # ------------------------------------------------------------------
    # Event tap callback (runs on background thread)
    # ------------------------------------------------------------------

    def _event_tap_callback(self, proxy, event_type, cg_event, user_info):
        # CGEventTap callbacks must never raise — wrap everything so an
        # exception here cannot propagate into CoreGraphics.
        try:
            if event_type in (_kCGEventTapDisabledByTimeout, _kCGEventTapDisabledByUserInput):
                # The _check_health QTimer owns re-enabling; just log here.
                cause = ("timeout" if event_type == _kCGEventTapDisabledByTimeout
                         else "user input")
                log.info("Brightness key event tap disabled by %s; health timer will re-enable",
                         cause)
                return cg_event

            if event_type != NX_SYSDEFINED:
                return cg_event

            parsed = self._parse_brightness_event(cg_event)
            if parsed is None:
                return cg_event

            direction, is_key_down = parsed
            if not is_key_down:
                return cg_event

            should_intercept = False
            if direction == "down" and self._intercept_down:
                should_intercept = True
            elif direction == "up" and self._intercept_up:
                should_intercept = True

            try:
                self.brightness_key_pressed.emit(direction, should_intercept)
            except Exception as e:
                log.warning("Failed to emit brightness_key_pressed: %s", e)

            if should_intercept:
                return None
            return cg_event
        except Exception as e:
            log.warning("Exception in brightness key event tap callback: %s", e)
            return cg_event

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self, ready):
        try:
            event_mask = 1 << NX_SYSDEFINED
            self._callback_ref = CGEventTapCallBack(self._event_tap_callback)

            with self._lock:
                if self._cancelled:
                    ready.set()
                    return

            tap = self._cg.CGEventTapCreate(
                _kCGSessionEventTap,
                _kCGHeadInsertEventTap,
                _kCGEventTapOptionDefault,
                event_mask,
                self._callback_ref,
                None)

            if not tap:
                self._diagnose_tap_failure()
                ready.set()
                return

            self._tap = tap

            source = self._cf.CFMachPortCreateRunLoopSource(None, tap, 0)
            if not source:
                log.warning("CFMachPortCreateRunLoopSource failed")
                self.failure_reason = "error"
                self._teardown_tap()
                ready.set()
                return

            run_loop = self._cf.CFRunLoopGetCurrent()
            self._run_loop = run_loop
            self._source = source

            self._cf.CFRunLoopAddSource(
                run_loop, source, self._kCFRunLoopCommonModes)
            self._cg.CGEventTapEnable(tap, True)

            ready.set()

            # Re-check cancelled after the tap is live: a timed-out start()
            # may have set _cancelled while we were still setting up. If so,
            # tear down the now-live tap instead of entering the run loop.
            with self._lock:
                if self._cancelled:
                    self._teardown_tap()
                    return

            self._cf.CFRunLoopRun()

        except Exception as e:
            log.warning("Brightness key event tap thread failed: %s", e)
            self.failure_reason = "error"
            self._tap = None
            ready.set()

    # ------------------------------------------------------------------
    # Health monitoring (runs on main thread via QTimer)
    # ------------------------------------------------------------------

    def _check_health(self):
        if self._tap and not self.is_tap_enabled():
            log.info("Brightness key event tap found disabled, re-enabling")
            self.reenable_tap()
