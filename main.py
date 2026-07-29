import os
import sys
import ctypes
import shutil
import logging
from logging.handlers import RotatingFileHandler


def configure_working_directory():
    """Use a stable writable app-data directory and migrate portable data."""
    if getattr(sys, "frozen", False):
        legacy_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        legacy_dir = os.path.dirname(os.path.abspath(__file__))

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = legacy_dir
    data_dir = os.path.join(local_app_data, "UTAR_WBLE_Agent")
    os.makedirs(data_dir, exist_ok=True)

    for filename in (
        "wble_config.json",
        "wble_config.json.bak",
        "wble_state.json",
        "wble_state.json.bak",
        "wble_auth_state.json",
    ):
        source = os.path.join(legacy_dir, filename)
        destination = os.path.join(data_dir, filename)
        if os.path.isfile(source) and not os.path.exists(destination):
            try:
                shutil.copy2(source, destination)
            except OSError:
                pass

    legacy_profile = os.path.join(legacy_dir, "chrome_data")
    migrated_profile = os.path.join(data_dir, "chrome_data")
    if os.path.isdir(legacy_profile) and not os.path.exists(migrated_profile):
        try:
            shutil.copytree(legacy_profile, migrated_profile)
        except OSError:
            # A running legacy Chrome profile may be locked. The app remains
            # usable and will create a fresh profile instead.
            pass

    os.chdir(data_dir)


configure_working_directory()


def configure_logging():
    handler = RotatingFileHandler(
        "wble_agent.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    def log_uncaught_exception(exception_type, exception, traceback):
        logging.critical(
            "Uncaught exception",
            exc_info=(exception_type, exception, traceback),
        )
        if not getattr(sys, "frozen", False):
            sys.__excepthook__(exception_type, exception, traceback)

    sys.excepthook = log_uncaught_exception


configure_logging()

import asyncio
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QMessageBox, QSystemTrayIcon
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
import qasync

from core.autostart import AUTOSTART_FLAG
from gui.main_window import MainWindow, resource_path


INSTANCE_MUTEX_HANDLE = None


def acquire_single_instance():
    """Return False when another WBLE Agent instance already owns the mutex."""
    global INSTANCE_MUTEX_HANDLE
    if sys.platform != "win32":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    create_mutex.restype = ctypes.c_void_p
    handle = create_mutex(
        None, False, r"Local\UTAR_WBLE_Agent_SingleInstance"
    )
    if not handle:
        return True

    already_exists = ctypes.get_last_error() == 183
    if already_exists:
        kernel32.CloseHandle(handle)
        return False

    INSTANCE_MUTEX_HANDLE = handle
    return True


def configure_windows_app_identity():
    """Give Windows a stable identity for taskbar icon grouping and display."""
    if sys.platform != "win32":
        return
    try:
        set_app_id = (
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        )
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id("UTAR.WBLEAgent")
    except Exception:
        pass


def main():
    configure_windows_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("UTAR WBLE Agent")
    app.setOrganizationName("UTAR")
    app.setWindowIcon(QIcon(resource_path("utar_logo.png")))
    app.setQuitOnLastWindowClosed(False)
    is_autostart_launch = AUTOSTART_FLAG in sys.argv[1:]
    if not acquire_single_instance():
        if not is_autostart_launch:
            QMessageBox.information(
                None,
                "WBLE Agent 已在运行",
                "程序已经在系统托盘中运行，请勿重复启动。",
            )
        return
    
    # Use qasync event loop to bridge PyQt6 and asyncio (Playwright)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = MainWindow()
    if (
        is_autostart_launch
        and QSystemTrayIcon.isSystemTrayAvailable()
    ):
        # Keep startup unobtrusive, then immediately perform one headless scan.
        window.hide()
        QTimer.singleShot(3000, window.auto_scan_trigger)
    else:
        window.show()
    
    with loop:
        loop.run_forever()

if __name__ == "__main__":
    main()
