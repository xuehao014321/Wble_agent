import os
import subprocess
import sys


APP_NAME = "UTAR_WBLE_Agent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_FLAG = "--autostart"


def build_autostart_command():
    """Build a quoted command for both source runs and frozen executables."""
    if getattr(sys, "frozen", False):
        args = [os.path.abspath(sys.executable)]
    else:
        main_script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), os.pardir, "main.py")
        )
        args = [os.path.abspath(sys.executable), main_script]
    args.append(AUTOSTART_FLAG)
    return subprocess.list2cmdline(args)


def get_registered_autostart_command():
    if sys.platform != "win32":
        return ""

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return str(value)
    except OSError:
        return ""


def is_autostart_enabled():
    registered = get_registered_autostart_command()
    if not registered:
        return False
    return registered.strip().casefold() == build_autostart_command().strip().casefold()


def set_autostart_enabled(enabled):
    """Create or remove the per-user Windows startup entry."""
    if sys.platform != "win32":
        raise RuntimeError("开机自启动目前仅支持 Windows。")

    import winreg

    if enabled:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(
                key,
                APP_NAME,
                0,
                winreg.REG_SZ,
                build_autostart_command()
            )
        return

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass
