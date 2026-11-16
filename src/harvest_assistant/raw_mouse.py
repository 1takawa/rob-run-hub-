"""Relative mouse motion straight from Windows Raw Input.

Games that lock the cursor re-centre it every frame, so screen-position deltas
(what pynput reports) cancel out. Raw Input reports the real hand movement,
which is exactly what a game uses for the camera.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

WM_INPUT = 0x00FF
WM_QUIT = 0x0012
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEMOUSE = 0
MOUSE_MOVE_ABSOLUTE = 0x01
HWND_MESSAGE = wintypes.HWND(-3)

LRESULT = ctypes.c_ssize_t


class _RawInputDevice(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class _RawInputHeader(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RawMouse(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("ulButtons", wintypes.ULONG),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class _RawInputMouse(ctypes.Structure):
    _fields_ = [("header", _RawInputHeader), ("mouse", _RawMouse)]


_WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WndClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class RawMouseReader:
    """Calls ``on_delta(dx, dy)`` from a background thread for every mouse move."""

    def __init__(self, on_delta: Callable[[int, int], None]) -> None:
        if not hasattr(ctypes, "windll"):
            raise OSError("Raw Input is only available on Windows")
        self._on_delta = on_delta
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: Exception | None = None

    def start(self, timeout: float = 2.0) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="raw-mouse", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Raw Input listener did not start")
        if self._error is not None:
            self._thread = None
            raise self._error

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = 0

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.GetRawInputData.argtypes = [
            wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT), wintypes.UINT,
        ]
        user32.GetRawInputData.restype = wintypes.UINT
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.RegisterRawInputDevices.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        header_size = ctypes.sizeof(_RawInputHeader)

        def wnd_proc(hwnd, message, wparam, lparam):
            if message == WM_INPUT:
                try:
                    size = wintypes.UINT(ctypes.sizeof(_RawInputMouse))
                    data = _RawInputMouse()
                    read = user32.GetRawInputData(
                        wintypes.HANDLE(lparam), RID_INPUT, ctypes.byref(data), ctypes.byref(size), header_size
                    )
                    if read != 0xFFFFFFFF and data.header.dwType == RIM_TYPEMOUSE:
                        mouse = data.mouse
                        if not (mouse.usFlags & MOUSE_MOVE_ABSOLUTE) and (mouse.lLastX or mouse.lLastY):
                            self._on_delta(int(mouse.lLastX), int(mouse.lLastY))
                except Exception:
                    pass
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        callback = _WNDPROC(wnd_proc)  # keep a reference for the lifetime of the thread
        instance = kernel32.GetModuleHandleW(None)
        class_name = f"HarvestAssistantRawMouse{id(self)}"
        window_class = _WndClass()
        window_class.lpfnWndProc = callback
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        hwnd = None
        try:
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError()
            hwnd = user32.CreateWindowExW(0, class_name, "", 0, 0, 0, 0, 0, HWND_MESSAGE, None, instance, None)
            if not hwnd:
                raise ctypes.WinError()
            device = _RawInputDevice(0x01, 0x02, RIDEV_INPUTSINK, hwnd)
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
                raise ctypes.WinError()
            self._thread_id = kernel32.GetCurrentThreadId()
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        device = _RawInputDevice(0x01, 0x02, 0x00000001, None)  # RIDEV_REMOVE
        user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(class_name, instance)
