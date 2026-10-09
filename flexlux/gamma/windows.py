import ctypes
import logging
import winreg
from ctypes import wintypes

from flexlux.gamma import GammaController

log = logging.getLogger("FlexLux")

_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
_RAMP_SIZE = 256
_ICM_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM"
_FULL_GAMMA_RANGE = 256


class _DisplayDevice(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


_Ramp = (wintypes.WORD * _RAMP_SIZE) * 3


class WindowsGammaController(GammaController):
    """GDI device gamma ramps, keyed by display device name (\\\\.\\DISPLAY1).

    Windows keeps a ramp after the process exits, so restore() must run on
    quit. By default it also rejects ramps far from neutral; the
    GdiIcmGammaRange registry value lifts that limit after the next sign-in.
    """

    def __init__(self):
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32")
        self._user32.EnumDisplayDevicesW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DisplayDevice), wintypes.DWORD]
        self._user32.EnumDisplayDevicesW.restype = wintypes.BOOL
        self._gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
        self._gdi32.CreateDCW.restype = wintypes.HDC
        self._gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self._gdi32.GetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
        self._gdi32.GetDeviceGammaRamp.restype = wintypes.BOOL
        self._gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
        self._gdi32.SetDeviceGammaRamp.restype = wintypes.BOOL
        self._shell32.ShellExecuteW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        self._shell32.ShellExecuteW.restype = ctypes.c_void_p
        super().__init__()

    def _list_outputs(self):
        names = []
        index = 0
        while True:
            device = _DisplayDevice()
            device.cb = ctypes.sizeof(_DisplayDevice)
            if not self._user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                return names
            if device.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                names.append(device.DeviceName)
            index += 1

    def _with_dc(self, key, action):
        hdc = self._gdi32.CreateDCW(key, None, None, None)
        if not hdc:
            log.warning("CreateDC failed for %s: %s", key, ctypes.WinError(ctypes.get_last_error()))
            return False
        try:
            return action(hdc)
        finally:
            self._gdi32.DeleteDC(hdc)

    def _read(self, key):
        ramp = _Ramp()
        if not self._with_dc(key, lambda hdc: self._gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(ramp))):
            return None
        return tuple([v / 65535 for v in channel] for channel in ramp)

    def _write(self, key, ramps):
        ramp = _Ramp()
        for channel, values in zip(ramp, ramps):
            for i, v in enumerate(values):
                channel[i] = int(round(max(0.0, min(1.0, v)) * 65535))
        # A rejected ramp is expected under the default range limit; the
        # caller searches for the strongest accepted one and logs the result.
        return bool(self._with_dc(key, lambda hdc: self._gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))))

    def can_unlock_full_range(self):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _ICM_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "GdiIcmGammaRange")
        except FileNotFoundError:
            return True
        return value != _FULL_GAMMA_RANGE

    def unlock_full_range(self):
        """Ask for administrator approval to set GdiIcmGammaRange.

        Returns True if the elevated registry command was launched. Windows
        applies the new limit after the user signs out and back in.
        """
        args = (f'add "HKLM\\{_ICM_KEY}" /v GdiIcmGammaRange /t REG_DWORD '
                f'/d {_FULL_GAMMA_RANGE} /f')
        result = self._shell32.ShellExecuteW(None, "runas", "reg.exe", args, None, 0)
        launched = result is not None and result > 32
        if not launched:
            log.warning("Could not launch elevated GdiIcmGammaRange update (code %s)", result)
        return launched
