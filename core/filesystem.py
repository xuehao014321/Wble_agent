import ctypes
import os
import sys


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_bool),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def move_to_recycle_bin(path):
    """Move one exact Windows path to the recycle bin."""
    if sys.platform != "win32":
        raise RuntimeError("回收站删除目前仅支持 Windows。")

    absolute_path = os.path.abspath(path)
    if not os.path.exists(absolute_path):
        return

    operation = SHFILEOPSTRUCTW()
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = absolute_path + "\0"
    operation.fFlags = (
        0x0004  # FOF_SILENT
        | 0x0010  # FOF_NOCONFIRMATION
        | 0x0040  # FOF_ALLOWUNDO
        | 0x0400  # FOF_NOERRORUI
    )
    result = ctypes.windll.shell32.SHFileOperationW(
        ctypes.byref(operation)
    )
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(
            result,
            "Windows 未能将课程文件夹移入回收站。",
            absolute_path,
        )
