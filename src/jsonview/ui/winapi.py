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


def _colorref(window: tk.Misc, color: str) -> ctypes.c_int:
    """Any Tk color ("#1c1c1c", "SystemButtonFace", ...) as a Windows COLORREF (0x00BBGGRR)."""
    r, g, b = (c >> 8 for c in window.winfo_rgb(color))
    return ctypes.c_int(r | (g << 8) | (b << 16))


def style_title_bar(window: tk.Misc, *, dark: bool, caption: str | None = None) -> None:
    """Match the native title bar to the app: dark mode, and on Windows 11 the exact caption color.

    The title text color is left to Windows so inactive windows still look dimmed.
    """
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
        if caption:
            ref = _colorref(window, caption)
            set_attr(hwnd, _DWMWA_CAPTION_COLOR, ctypes.byref(ref), ctypes.sizeof(ref))
        # Nudge Windows into repainting the non-client area right away.
        alpha = window.wm_attributes("-alpha")
        window.wm_attributes("-alpha", 0.99)
        window.wm_attributes("-alpha", alpha)
    except (AttributeError, OSError, ValueError, tk.TclError):
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
