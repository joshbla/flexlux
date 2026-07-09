import sys
import os
import platform
import logging
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger("FlexLux")

_MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__main__.py")


def _is_frozen():
    return getattr(sys, 'frozen', False)


def _windows_command():
    if _is_frozen():
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{_MAIN_SCRIPT}"'


def _escape_desktop_arg(arg):
    escaped = (
        arg.replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('`', '\\`')
        .replace('$', '\\$')
        .replace('%', '%%')
    )
    return f'"{escaped}"'


def _desktop_exec():
    exe = _escape_desktop_arg(sys.executable)
    if _is_frozen():
        return exe
    return f"{exe} {_escape_desktop_arg(_MAIN_SCRIPT)}"


def _plist_args():
    if _is_frozen():
        return f"        <string>{_xml_escape(sys.executable)}</string>"
    return (f"        <string>{_xml_escape(sys.executable)}</string>\n"
            f"        <string>{_xml_escape(_MAIN_SCRIPT)}</string>")


def is_enabled():
    system = platform.system()
    if system == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, "FlexLux")
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False
    elif system == "Darwin":
        return os.path.exists(
            os.path.expanduser("~/Library/LaunchAgents/com.flexlux.app.plist"))
    else:
        return os.path.exists(
            os.path.expanduser("~/.config/autostart/flexlux.desktop"))


def set_enabled(enabled):
    system = platform.system()

    if system == "Windows":
        import winreg
        try:
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE)
            try:
                if enabled:
                    winreg.SetValueEx(key, "FlexLux", 0, winreg.REG_SZ,
                                      _windows_command())
                else:
                    try:
                        winreg.DeleteValue(key, "FlexLux")
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
        except Exception:
            logger.warning("Failed to set Windows autostart", exc_info=True)

    elif system == "Darwin":
        plist_path = os.path.expanduser(
            "~/Library/LaunchAgents/com.flexlux.app.plist")
        if enabled:
            args = _plist_args()
            plist = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
                ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n<dict>\n'
                '    <key>Label</key>\n'
                '    <string>com.flexlux.app</string>\n'
                '    <key>ProgramArguments</key>\n'
                '    <array>\n'
                f'{args}\n'
                '    </array>\n'
                '    <key>RunAtLoad</key>\n'
                '    <true/>\n'
                '</dict>\n</plist>\n')
            os.makedirs(os.path.dirname(plist_path), exist_ok=True)
            with open(plist_path, 'w') as f:
                f.write(plist)
        else:
            if os.path.exists(plist_path):
                os.remove(plist_path)

    else:  # Linux
        desktop_path = os.path.expanduser(
            "~/.config/autostart/flexlux.desktop")
        if enabled:
            entry = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=FlexLux\n"
                f"Exec={_desktop_exec()}\n"
                "Hidden=false\n"
                "NoDisplay=false\n"
                "X-GNOME-Autostart-enabled=true\n")
            os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
            with open(desktop_path, 'w') as f:
                f.write(entry)
        else:
            if os.path.exists(desktop_path):
                os.remove(desktop_path)
