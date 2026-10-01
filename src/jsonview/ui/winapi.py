"""Small Windows integrations. Every function is a harmless no-op on other platforms."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 before 20H1
_DWMWA_CAPTION_COLOR = 35  # Windows 11 only
_DWMWA_TEXT_COLOR = 36


def enable_dpi_awareness() -> None:
    """Render crisply on scaled displays instead of being bitmap-stretched by Windows.

    Must run before the first Tk window is created. "System aware" (1) is used
    rather than per-monitor because Tk doesn't rescale when a window moves
    between monitors with different scaling.
    """
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def set_app_id(app_id: str = "Matt.JSONViewer") -> None:
    """Give the process its own taskbar identity so it shows this app's icon instead of Python's."""
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (AttributeError, OSError):
            pass


def system_prefers_dark() -> bool:
    """Read Settings > Personalization > Colors > "Choose your app mode"."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except OSError:
        return False


def _colorref(hex_color: str) -> ctypes.c_int:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return ctypes.c_int(r | (g << 8) | (b << 16))  # COLORREF is 0x00BBGGRR


def style_title_bar(window: tk.Misc, *, dark: bool, caption: str | None = None, text: str | None = None) -> None:
    """Match the native title bar to the app: dark mode, and on Windows 11 the exact caption color."""
    if not IS_WINDOWS:
        return
    try:
        window.update_idletasks()
        get_parent = ctypes.windll.user32.GetParent
        get_parent.argtypes, get_parent.restype = [wintypes.HWND], wintypes.HWND
        hwnd = get_parent(window.winfo_id())
        if not hwnd:
            return  # not mapped yet; it's styled again once the window is shown
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attr.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        flag = ctypes.c_int(1 if dark else 0)
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            if set_attr(hwnd, attr, ctypes.byref(flag), ctypes.sizeof(flag)) == 0:
                break
        for attr, color in ((_DWMWA_CAPTION_COLOR, caption), (_DWMWA_TEXT_COLOR, text)):
            if color:
                ref = _colorref(color)
                set_attr(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
        # Nudge Windows into repainting the non-client area right away.
        alpha = window.wm_attributes("-alpha")
        window.wm_attributes("-alpha", 0.99)
        window.wm_attributes("-alpha", alpha)
    except (AttributeError, OSError, tk.TclError):
        pass


def point_on_screen(window: tk.Misc, x: int, y: int) -> bool:
    """True if (x, y) is on any connected monitor, so a saved position never strands the window."""
    if IS_WINDOWS:
        try:
            monitor_from_point = ctypes.windll.user32.MonitorFromPoint
            monitor_from_point.argtypes = [wintypes.POINT, wintypes.DWORD]
            monitor_from_point.restype = wintypes.HMONITOR
            monitor_default_to_null = 0
            return bool(monitor_from_point(wintypes.POINT(x, y), monitor_default_to_null))
        except (AttributeError, OSError):
            pass
    return 0 <= x < window.winfo_screenwidth() and 0 <= y < window.winfo_screenheight()
