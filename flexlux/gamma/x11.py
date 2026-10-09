import ctypes
import ctypes.util
import logging

from flexlux.gamma import GammaController

log = logging.getLogger("FlexLux")


class _XRRScreenResources(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_ulong),
        ("configTimestamp", ctypes.c_ulong),
        ("ncrtc", ctypes.c_int),
        ("crtcs", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _XRRCrtcInfo(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_uint),
        ("height", ctypes.c_uint),
        ("mode", ctypes.c_ulong),
    ]


class _XRRCrtcGamma(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_int),
        ("red", ctypes.POINTER(ctypes.c_ushort)),
        ("green", ctypes.POINTER(ctypes.c_ushort)),
        ("blue", ctypes.POINTER(ctypes.c_ushort)),
    ]


_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


@_XErrorHandler
def _ignore_x_error(_display, _event):
    # Xlib's default handler terminates the process on any protocol error,
    # e.g. a CRTC that disappears between enumeration and a gamma write.
    log.debug("Ignored X11 protocol error during gamma control")
    return 0


class X11GammaController(GammaController):
    """XRandR CRTC gamma ramps, keyed by CRTC id.

    X11 keeps a ramp after the process exits, so restore() must run on quit.
    """

    def __init__(self):
        x11_path = ctypes.util.find_library("X11")
        xrandr_path = ctypes.util.find_library("Xrandr")
        if x11_path is None or xrandr_path is None:
            raise OSError("libX11/libXrandr not found")
        x11 = ctypes.cdll.LoadLibrary(x11_path)
        xrr = ctypes.cdll.LoadLibrary(xrandr_path)
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XSetErrorHandler.argtypes = [_XErrorHandler]
        x11.XSetErrorHandler.restype = ctypes.c_void_p
        xrr.XRRGetScreenResourcesCurrent.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xrr.XRRGetScreenResourcesCurrent.restype = ctypes.POINTER(_XRRScreenResources)
        xrr.XRRFreeScreenResources.argtypes = [ctypes.POINTER(_XRRScreenResources)]
        xrr.XRRGetCrtcInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XRRScreenResources), ctypes.c_ulong]
        xrr.XRRGetCrtcInfo.restype = ctypes.POINTER(_XRRCrtcInfo)
        xrr.XRRFreeCrtcInfo.argtypes = [ctypes.POINTER(_XRRCrtcInfo)]
        xrr.XRRGetCrtcGammaSize.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xrr.XRRGetCrtcGammaSize.restype = ctypes.c_int
        xrr.XRRGetCrtcGamma.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xrr.XRRGetCrtcGamma.restype = ctypes.POINTER(_XRRCrtcGamma)
        xrr.XRRAllocGamma.argtypes = [ctypes.c_int]
        xrr.XRRAllocGamma.restype = ctypes.POINTER(_XRRCrtcGamma)
        xrr.XRRSetCrtcGamma.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XRRCrtcGamma)]
        xrr.XRRFreeGamma.argtypes = [ctypes.POINTER(_XRRCrtcGamma)]
        self._x11 = x11
        self._xrr = xrr
        # A dedicated connection: Qt's own connection is not exposed to Python.
        self._display = x11.XOpenDisplay(None)
        if not self._display:
            raise OSError("cannot open X display")
        x11.XSetErrorHandler(_ignore_x_error)
        self._root = x11.XDefaultRootWindow(self._display)
        super().__init__()

    def _list_outputs(self):
        resources = self._xrr.XRRGetScreenResourcesCurrent(self._display, self._root)
        if not resources:
            log.warning("XRRGetScreenResourcesCurrent failed")
            return []
        crtcs = []
        try:
            for i in range(resources.contents.ncrtc):
                crtc = resources.contents.crtcs[i]
                info = self._xrr.XRRGetCrtcInfo(self._display, resources, crtc)
                if not info:
                    continue
                active = info.contents.mode != 0
                self._xrr.XRRFreeCrtcInfo(info)
                if active and self._xrr.XRRGetCrtcGammaSize(self._display, crtc) > 1:
                    crtcs.append(crtc)
        finally:
            self._xrr.XRRFreeScreenResources(resources)
        return crtcs

    def _read(self, key):
        gamma = self._xrr.XRRGetCrtcGamma(self._display, key)
        if not gamma:
            return None
        try:
            g = gamma.contents
            if g.size < 2:
                return None
            return tuple([channel[i] / 65535 for i in range(g.size)]
                         for channel in (g.red, g.green, g.blue))
        finally:
            self._xrr.XRRFreeGamma(gamma)

    def _write(self, key, ramps):
        gamma = self._xrr.XRRAllocGamma(len(ramps[0]))
        if not gamma:
            return False
        try:
            g = gamma.contents
            for channel, values in zip((g.red, g.green, g.blue), ramps):
                for i, v in enumerate(values):
                    channel[i] = int(round(max(0.0, min(1.0, v)) * 65535))
            self._xrr.XRRSetCrtcGamma(self._display, key, gamma)
            self._x11.XSync(self._display, 0)
        finally:
            self._xrr.XRRFreeGamma(gamma)
        return True
