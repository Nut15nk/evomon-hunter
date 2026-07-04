# ===================================================================
#  roblox.py — ตรวจว่า Roblox เปิดอยู่ไหม (และเป็นหน้าต่างที่โฟกัสอยู่ไหม)
#  ใช้ Windows API ผ่าน ctypes (ไม่ต้องลง library เพิ่ม)
# ===================================================================

import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


# ---- ตั้ง argtypes/restypes ให้ถูกบน 64-bit ----
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.Process32First.restype = wintypes.BOOL
kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.Process32Next.restype = wintypes.BOOL
kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
SW_RESTORE = 9
SW_MAXIMIZE = 3


def is_running(proc_name="RobloxPlayerBeta.exe"):
    """มี process ชื่อนี้รันอยู่ไหม"""
    target = proc_name.lower().encode()
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return False
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(entry)):
            return False
        while True:
            if entry.szExeFile.lower() == target:
                return True
            if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return False


def _pid_exe(pid):
    """ชื่อไฟล์ .exe จาก PID"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.split("\\")[-1]
    finally:
        kernel32.CloseHandle(h)
    return ""


def foreground_exe():
    """ชื่อไฟล์ .exe ของหน้าต่างที่กำลังโฟกัสอยู่"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    return _pid_exe(pid.value)


def is_foreground(proc_name="RobloxPlayerBeta.exe"):
    """หน้าต่างที่โฟกัสอยู่เป็น Roblox ไหม"""
    return foreground_exe().lower() == proc_name.lower()


def get_hwnd(proc_name="RobloxPlayerBeta.exe"):
    """หา handle หน้าต่างหลักของ Roblox (0 ถ้าไม่เจอ)"""
    found = []

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value and _pid_exe(pid.value).lower() == proc_name.lower():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else 0


def focus(proc_name="RobloxPlayerBeta.exe"):
    """ดึงหน้าต่าง Roblox ขึ้นมาโฟกัส (คืน True ถ้าทำได้)"""
    hwnd = get_hwnd(proc_name)
    if not hwnd:
        return False
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        # ส่ง Alt เปล่าๆ เพื่อปลดล็อก SetForegroundWindow ของ Windows
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return False
    return is_foreground(proc_name)


def maximize(proc_name="RobloxPlayerBeta.exe"):
    """บังคับหน้าต่าง Roblox เป็น Full Window (maximize) — ไม่ใช่ fullscreen exclusive"""
    hwnd = get_hwnd(proc_name)
    if not hwnd:
        return False
    try:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return False
    return True


if __name__ == "__main__":
    print("RobloxPlayerBeta.exe running :", is_running())
    print("foreground exe              :", foreground_exe())
    print("Roblox is foreground        :", is_foreground())
    print("Roblox hwnd                 :", get_hwnd())
