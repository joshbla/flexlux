import sys
import os
import platform

_MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__main__.py")


def _get_executable():
    if getattr(sys, 'frozen', False):
        return sys.executable
    return f'"{sys.executable}" "{_MAIN_SCRIPT}"'


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
    exe = _get_executable()

    if system == "Windows":
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, "FlexLux", 0, winreg.REG_SZ, exe)
            else:
                try:
                    winreg.DeleteValue(key, "FlexLux")
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)

    elif system == "Darwin":
        plist_path = os.path.expanduser(
            "~/Library/LaunchAgents/com.flexlux.app.plist")
        if enabled:
            if getattr(sys, 'frozen', False):
                args = f"        <string>{exe}</string>"
            else:
                args = (f"        <string>{sys.executable}</string>\n"
                        f"        <string>{_MAIN_SCRIPT}</string>")
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
                f"Exec={exe}\n"
                "Hidden=false\n"
                "NoDisplay=false\n"
                "X-GNOME-Autostart-enabled=true\n")
            os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
            with open(desktop_path, 'w') as f:
                f.write(entry)
        else:
            if os.path.exists(desktop_path):
                os.remove(desktop_path)
