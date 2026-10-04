"""Help windows: keyboard shortcuts and About."""

from __future__ import annotations

import platform
import tkinter as tk
from tkinter import messagebox, ttk

SHORTCUTS = [
    ("Files", [
        ("Open a file", "Ctrl+O"),
        ("Open JSON from the clipboard", "Ctrl+Shift+V, or Ctrl+V outside text boxes"),
        ("Reload", "F5"),
        ("New window / close window", "Ctrl+N / Ctrl+W"),
    ]),
    ("Search", [
        ("Find", "Ctrl+F"),
        ("Next / previous match", "F3 / Shift+F3, or Enter / Shift+Enter in the box"),
        ("Clear the search", "Esc"),
    ]),
    ("Tree", [
        ("Copy value", "Ctrl+C"),
        ("Copy JSONPath", "Ctrl+Shift+C"),
        ("Copy Python path", "Ctrl+Alt+C"),
        ("Expand all / collapse all", "Ctrl+E / Ctrl+L"),
        ("Expand the selected branch", "*"),
        ("Open a link, or expand a row", "Enter or double-click"),
        ("More actions", "Right-click or Shift+F10"),
    ]),
    ("Window", [
        ("Show or hide details", "Ctrl+D"),
        ("Switch light and dark", "Ctrl+T"),
    ]),
]


def show_shortcuts(parent: tk.Misc, theme) -> None:
    existing = getattr(parent, "_jsonview_shortcuts", None)
    if existing is not None and existing.winfo_exists():  # one at a time, however often F1 is pressed
        existing.lift()
        existing.focus_set()
        return
    top = tk.Toplevel(parent)
    parent._jsonview_shortcuts = top  # type: ignore[attr-defined]
    top.title("Keyboard shortcuts")
    top.transient(parent)
    top.resizable(False, False)
    top.configure(background=theme.palette.window)
    frame = ttk.Frame(top, padding=(24, 18, 24, 20))
    frame.pack(fill="both", expand=True)
    row = 0
    for group, items in SHORTCUTS:
        ttk.Label(frame, text=group, style="Strong.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0 if row == 0 else 14, 4))
        row += 1
        for action, keys in items:
            ttk.Label(frame, text=action).grid(row=row, column=0, sticky="w", padx=(0, 28), pady=1)
            ttk.Label(frame, text=keys, style="Muted.TLabel").grid(row=row, column=1, sticky="w", pady=1)
            row += 1
    ttk.Button(frame, text="Close", command=top.destroy).grid(row=row, column=1, sticky="e", pady=(18, 0))
    top.bind("<Escape>", lambda e: top.destroy())
    top.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - top.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - top.winfo_height()) // 3
    top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    theme.style_window(top)
    top.focus_set()


def show_about(parent: tk.Misc, session) -> None:
    from .. import __version__
    from ..settings import SETTINGS_FILE
    from .theme import sv_ttk

    lines = [
        f"jsonview {__version__}",
        "",
        f"Python {platform.python_version()}, Tcl/Tk {parent.tk.call('info', 'patchlevel')}",
        f"Windows 11 theme: {'on (sv-ttk)' if sv_ttk else 'not installed'}",
        f"Drag and drop: {'on (tkinterdnd2)' if session.dnd else 'unavailable'}",
        "",
        f"Settings: {SETTINGS_FILE}",
    ]
    messagebox.showinfo("About jsonview", "\n".join(lines), parent=parent)
