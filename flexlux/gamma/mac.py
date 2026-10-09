import ctypes
import logging

from flexlux.gamma import GammaController

log = logging.getLogger("FlexLux")

_MAX_DISPLAYS = 16
_TABLE_CAPACITY = 1024


class MacGammaController(GammaController):
    """CoreGraphics display transfer tables, keyed by CGDirectDisplayID.

    macOS discards a process's tables when it exits, so the displays return to
    their ColorSync settings even if FlexLux crashes.
    """

    def __init__(self):
        cg = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        table = ctypes.POINTER(ctypes.c_float)
        cg.CGGetOnlineDisplayList.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        cg.CGGetOnlineDisplayList.restype = ctypes.c_int32
        cg.CGGetDisplayTransferByTable.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, table, table, table, ctypes.POINTER(ctypes.c_uint32)]
        cg.CGGetDisplayTransferByTable.restype = ctypes.c_int32
        cg.CGSetDisplayTransferByTable.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, table, table, table]
        cg.CGSetDisplayTransferByTable.restype = ctypes.c_int32
        self._cg = cg
        super().__init__()

    def _list_outputs(self):
        ids = (ctypes.c_uint32 * _MAX_DISPLAYS)()
        count = ctypes.c_uint32()
        err = self._cg.CGGetOnlineDisplayList(_MAX_DISPLAYS, ids, ctypes.byref(count))
        if err != 0:
            log.warning("CGGetOnlineDisplayList failed: %d", err)
            return []
        return [ids[i] for i in range(count.value)]

    def _read(self, key):
        r, g, b = ((ctypes.c_float * _TABLE_CAPACITY)() for _ in range(3))
        count = ctypes.c_uint32()
        err = self._cg.CGGetDisplayTransferByTable(key, _TABLE_CAPACITY, r, g, b, ctypes.byref(count))
        if err != 0 or count.value < 2:
            return None
        n = count.value
        return list(r[:n]), list(g[:n]), list(b[:n])

    def _write(self, key, ramps):
        tables = [(ctypes.c_float * len(channel))(*channel) for channel in ramps]
        err = self._cg.CGSetDisplayTransferByTable(key, len(ramps[0]), *tables)
        if err != 0:
            log.warning("CGSetDisplayTransferByTable(%d) failed: %d", key, err)
            return False
        return True
