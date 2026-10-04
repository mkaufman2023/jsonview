"""Small reusable widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class Tooltip:
    """Shows a short hint after the pointer rests on a widget."""

    DELAY_MS = 500

    def __init__(self, widget: tk.Widget, text: str, theme) -> None:
        self.widget, self.text, self.theme = widget, text, theme
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")  # don't leave a pending timer on a dead widget

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self) -> None:
        p = self.theme.palette
        self._tip = tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        frame = tk.Frame(tip, background=p.muted, padx=1, pady=1)  # 1px border
        frame.pack()
        tk.Label(
            frame, text=self.text, background=p.field, foreground=p.text,
            font=self.theme.fonts.caption, padx=8, pady=4,
        ).pack()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip.update_idletasks()
        tip.wm_geometry(f"+{x - tip.winfo_width() // 2}+{y}")

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class PlaceholderEntry(ttk.Entry):
    """An entry that shows muted hint text while empty and unfocused.

    Use ``value`` instead of ``get()`` so the hint is never mistaken for input.
    """

    def __init__(self, master: tk.Misc, placeholder: str, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.placeholder = placeholder
        self._showing = False
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self._on_focus_out()

    @property
    def value(self) -> str:
        return "" if self._showing else self.get()

    def set_value(self, text: str) -> None:
        self._clear_placeholder()
        self.delete(0, "end")
        self.insert(0, text)
        if not text and self.focus_get() is not self:
            self._on_focus_out()

    def _clear_placeholder(self) -> None:
        if self._showing:
            self.delete(0, "end")
            self.configure(style="TEntry")
            self._showing = False

    def _on_focus_in(self, _event=None) -> None:
        self._clear_placeholder()

    def _on_focus_out(self, _event=None) -> None:
        if not self.get():
            self.insert(0, self.placeholder)
            self.configure(style="Placeholder.TEntry")
            self._showing = True
