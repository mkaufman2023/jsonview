"""Colors, fonts and ttk styles, built on the sv-ttk (Windows 11 "Sun Valley") theme."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk

from . import winapi

try:
    import sv_ttk
except ImportError:  # still works, just with the stock look
    sv_ttk = None

MONO_FAMILIES = ("Cascadia Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono", "Courier New")
ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")


@dataclass(frozen=True, slots=True)
class Palette:
    dark: bool
    window: str
    field: str
    text: str
    muted: str
    accent: str
    error: str
    match: str
    # one color per JSON value type, shared by the tree and the detail pane
    string: str
    number: str
    boolean: str
    null: str


_LIGHT = dict(
    muted="#616161", accent="#005fb8", error="#c42b1c", match="#fbe7a1",
    string="#17794a", number="#1c5fb0", boolean="#8a3fb0", null="#7c7c7c",
)
_DARK = dict(
    muted="#a8a8a8", accent="#60cdff", error="#ff99a4", match="#5a4a1a",
    string="#7fd1a0", number="#8cb8ff", boolean="#d4a6f2", null="#8f8f8f",
)


class Fonts:
    def __init__(self, root: tk.Misc) -> None:
        families = set(tkfont.families(root))
        named = set(tkfont.names(root))

        def pick(name: str, fallback: str) -> tkfont.Font:
            return tkfont.nametofont(name if name in named else fallback, root=root)

        self.ui = pick("SunValleyBodyFont", "TkDefaultFont")
        self.strong = pick("SunValleyBodyStrongFont", "TkDefaultFont")
        self.caption = pick("SunValleyCaptionFont", "TkSmallCaptionFont")
        self.subtitle = pick("SunValleySubtitleFont", "TkHeadingFont")
        mono = next((f for f in MONO_FAMILIES if f in families), None)
        mono = mono or tkfont.nametofont("TkFixedFont", root=root).actual("family")
        self.mono = tkfont.Font(root=root, family=mono, size=10)
        self.display = tkfont.Font(root=root, family=mono, size=34)
        icon_family = next((f for f in ICON_FAMILIES if f in families), None)
        self.icon = tkfont.Font(root=root, family=icon_family, size=11) if icon_family else None


def _scale_pixel_fonts(root: tk.Misc) -> None:
    """sv-ttk defines its fonts in pixels, which don't grow with Windows display scaling.

    Convert them so text is the intended size at 125%, 150%, etc.
    """
    scale = root.winfo_fpixels("1i") / 96.0
    if abs(scale - 1.0) < 0.05 or getattr(root, "_jsonview_fonts_scaled", False):
        return
    for name in tkfont.names(root):
        if name.startswith("SunValley"):
            f = tkfont.nametofont(name, root=root)
            size = int(f.cget("size"))
            if size < 0:
                f.configure(size=round(size * scale))
    root._jsonview_fonts_scaled = True  # type: ignore[attr-defined]


class ThemeManager:
    """Owns the light/dark state for the whole process and tells every window when it changes."""

    def __init__(self, root: tk.Tk, mode: str = "system") -> None:
        self.root = root
        self.mode = mode if mode in ("system", "light", "dark") else "system"
        self.listeners: list[Callable[[Palette], None]] = []
        self._stopped = False
        self._dark = self._resolve_dark()
        self._set_ttk_theme()
        _scale_pixel_fonts(root)
        self.fonts = Fonts(root)
        self.palette = self._build_palette()
        self._configure_styles()
        self._poll_system()

    def _resolve_dark(self) -> bool:
        if sv_ttk is None:
            return False
        return self.mode == "dark" or (self.mode == "system" and winapi.system_prefers_dark())

    def _set_ttk_theme(self) -> None:
        if sv_ttk is not None:
            sv_ttk.set_theme("dark" if self._dark else "light", self.root)
        else:
            style = ttk.Style(self.root)
            style.theme_use("vista" if "vista" in style.theme_names() else "clam")

    def _build_palette(self) -> Palette:
        style = ttk.Style(self.root)
        window = style.lookup(".", "background") or ("#1c1c1c" if self._dark else "#fafafa")
        field = style.lookup("Treeview", "fieldbackground") or window
        text = style.lookup(".", "foreground") or ("#fafafa" if self._dark else "#1c1c1c")
        return Palette(dark=self._dark, window=window, field=field, text=text, **(_DARK if self._dark else _LIGHT))

    def _configure_styles(self) -> None:
        """Custom styles live on the active ttk theme, so they're rebuilt after every switch."""
        p, f = self.palette, self.fonts
        style = ttk.Style(self.root)
        scale = self.root.winfo_fpixels("1i") / 96.0
        style.configure("Treeview", rowheight=f.ui.metrics("linespace") + round(10 * scale), font=f.ui,
                        indent=round(20 * scale))
        style.configure("Treeview.Heading", font=f.strong)
        style.configure("Muted.TLabel", foreground=p.muted)
        style.configure("Caption.TLabel", foreground=p.muted, font=f.caption)
        style.configure("Strong.TLabel", font=f.strong)
        style.configure("Subtitle.TLabel", font=f.subtitle)
        style.configure("Display.TLabel", foreground=p.number, font=f.display)
        style.configure("Link.TLabel", foreground=p.accent, font=f.ui)
        style.configure("Error.TLabel", foreground=p.error)
        style.configure("Code.TLabel", font=f.mono, foreground=p.text)
        style.configure("CodeCaret.TLabel", font=f.mono, foreground=p.error)
        style.configure("Placeholder.TEntry", foreground=p.muted)
        style.configure("Banner.TButton", background=p.match)  # so rounded corners blend in
        if f.icon is not None:
            style.configure("Icon.Toolbutton", font=f.icon)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.refresh(force=True)

    def toggle(self) -> None:
        self.set_mode("light" if self._dark else "dark")

    def refresh(self, force: bool = False) -> None:
        dark = self._resolve_dark()
        if dark == self._dark and not force:
            return
        self._dark = dark
        self._set_ttk_theme()
        self.palette = self._build_palette()
        self._configure_styles()
        for listener in list(self.listeners):
            listener(self.palette)

    def _poll_system(self) -> None:
        """Follow Windows' app mode live while the theme is set to "system"."""
        if self._stopped:
            return
        if self.mode == "system":
            self.refresh()
        self.root.after(2000, self._poll_system)

    def stop(self) -> None:
        self._stopped = True
        self.listeners.clear()

    def style_window(self, window: tk.Misc) -> None:
        p = self.palette
        winapi.style_title_bar(window, dark=p.dark, caption=p.window)
