"""The menu bar, dropdowns and right-click menus, drawn by Tk in the app's colors.

Native menus can't follow the dark theme on Windows: Windows paints the menu bar
itself, and it paints each submenu arrow after Tk draws the item, always in black,
so on a dark menu the arrow all but disappears. Drawing the menus here makes them
match the rest of the window in both themes, on every platform.

While a menu is open, a 1x1 widget tucked under the menu bar holds the pointer grab
and the keyboard focus, and every click and key is routed by screen position. The
focus stays in the window that owns the menu, so that window stays active the way
it does with native menus.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk

from . import winapi

SUBMENU_DELAY_MS = 250  # how long the pointer rests on an item before its submenu opens or closes
CHECK = ("\ue73e", "✓")  # Segoe Fluent Icons glyph, and the text fallback
ARROW = ("\ue76c", "›")
_SHIFT, _CONTROL = 0x1, 0x4  # event.state bits


@dataclass(slots=True, eq=False)
class _Entry:
    kind: str  # "command", "check", "radio", "cascade" or "separator"
    label: str = ""
    command: Callable[[], object] | None = None
    accelerator: str = ""
    enabled: bool = True
    variable: tk.Variable | None = None
    value: object = None
    submenu: Menu | None = None

    @property
    def selectable(self) -> bool:
        return self.kind != "separator" and self.enabled

    def selected(self) -> bool:
        try:
            if self.kind == "check":
                return bool(self.variable.get())
            if self.kind == "radio":
                return self.variable.get() == self.value
        except (tk.TclError, ValueError):
            pass
        return False


@dataclass(slots=True)
class _Metrics:
    pad: int  # between the border and the item highlights
    radius: int
    text_x: int
    mark_x: int  # center of the check mark column
    right_x: int  # right edge of accelerators and submenu arrows
    dot: int  # radius of the radio button mark


class Menu:
    """A dropdown or context menu, with the parts of tk.Menu's interface this app uses."""

    def __init__(self, master: tk.Misc | Menu, theme, postcommand: Callable[[], object] | None = None) -> None:
        self.theme = theme
        self.postcommand = postcommand
        self.entries: list[_Entry] = []
        if isinstance(master, Menu):
            parent, self.owner = master.top, master.owner  # destroyed along with the parent menu
        else:
            parent, self.owner = master, master.winfo_toplevel()
        self.top = tk.Toplevel(parent, class_="ThemedMenu")
        self.top.withdraw()  # right away, so it never flashes on screen before it's posted
        self.top.wm_overrideredirect(True)
        try:
            self.top.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.top, highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self.active = -1
        self.parent_index = -1  # while posted as a submenu: the parent entry that opened it
        self._rows: list[tuple[int, int]] = []
        self._size = (0, 0)
        self._pos = (0, 0)
        self._m = _Metrics(0, 0, 0, 0, 0, 0)

    # --- building, like tk.Menu ----------------------------------------------

    def add_command(self, label: str, command: Callable[[], object] | None = None,
                    accelerator: str = "", state: str = "normal") -> None:
        self.entries.append(_Entry("command", label, command, accelerator, enabled=state != "disabled"))

    def add_checkbutton(self, label: str, variable: tk.Variable, command: Callable[[], object] | None = None,
                        accelerator: str = "") -> None:
        self.entries.append(_Entry("check", label, command, accelerator, variable=variable))

    def add_radiobutton(self, label: str, variable: tk.Variable, value: object,
                        command: Callable[[], object] | None = None) -> None:
        self.entries.append(_Entry("radio", label, command, variable=variable, value=value))

    def add_cascade(self, label: str, menu: Menu) -> None:
        self.entries.append(_Entry("cascade", label, submenu=menu))

    def add_separator(self) -> None:
        self.entries.append(_Entry("separator"))

    def clear(self) -> None:
        self.entries.clear()

    # --- showing ---------------------------------------------------------------

    def popup(self, x: int, y: int) -> None:
        """Open as a context menu at screen position (x, y)."""
        _controller(self.owner).popup(self, x, y)

    def unpost(self) -> None:
        ctl = getattr(self.owner, "_jsonview_menus", None)
        if ctl is not None and self in ctl.chain:
            ctl.close_all()
        else:
            self._hide()

    def destroy(self) -> None:
        self.unpost()
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    def _prepare(self) -> None:
        """Refresh the entries (postcommand), then measure and draw."""
        if self.postcommand is not None:
            self.postcommand()
        self.active = -1
        self._layout()
        self._draw()

    def _layout(self) -> None:
        f = self.theme.fonts
        s = self.top.winfo_fpixels("1i") / 96.0
        pad = round(4 * s)
        row_h = f.ui.metrics("linespace") + round(10 * s)
        sep_h = round(9 * s)
        marks = any(e.kind in ("check", "radio") for e in self.entries)
        text_x = pad + round(12 * s) + (round(24 * s) if marks else 0)
        label_w = max((f.ui.measure(e.label) for e in self.entries), default=0)
        arrow_font, arrow = _glyph(f, ARROW)
        right_w = max((arrow_font.measure(arrow) if e.kind == "cascade" else f.ui.measure(e.accelerator)
                       for e in self.entries), default=0)
        right_pad = round(12 * s) + pad
        width = text_x + label_w + (round(36 * s) + right_w if right_w else 0) + right_pad
        width = max(width, round(160 * s))
        self._rows, y = [], pad
        for e in self.entries:
            h = sep_h if e.kind == "separator" else row_h
            self._rows.append((y, y + h))
            y += h
        self._size = (width, y + pad)
        self._m = _Metrics(pad=pad, radius=round(4 * s), text_x=text_x,
                           mark_x=pad + round(20 * s), right_x=width - right_pad, dot=max(2, round(3 * s)))

    def _draw(self) -> None:
        p, f, m = self.theme.palette, self.theme.fonts, self._m
        c = self.canvas
        w, h = self._size
        c.delete("all")
        c.configure(width=w, height=h, background=p.menu)
        c.create_rectangle(0, 0, w - 1, h - 1, outline=p.menu_border)
        check_font, check = _glyph(f, CHECK)
        arrow_font, arrow = _glyph(f, ARROW)
        gap = max(1, m.pad // 4)
        for i, (entry, (y0, y1)) in enumerate(zip(self.entries, self._rows)):
            mid = (y0 + y1) // 2
            if entry.kind == "separator":
                c.create_line(m.pad, mid, w - m.pad, mid, fill=p.menu_border)
                continue
            if i == self.active:
                _round_rect(c, m.pad, y0 + gap, w - m.pad, y1 - gap, m.radius, fill=p.menu_hover)
            color = p.text if entry.enabled else p.disabled
            if entry.selected():
                if entry.kind == "check":
                    c.create_text(m.mark_x, mid, text=check, font=check_font, fill=color)
                else:
                    r = m.dot
                    c.create_oval(m.mark_x - r, mid - r, m.mark_x + r, mid + r, fill=color, outline=color)
            c.create_text(m.text_x, mid, text=entry.label, anchor="w", font=f.ui, fill=color)
            if entry.kind == "cascade":
                c.create_text(m.right_x, mid, text=arrow, anchor="e", font=arrow_font, fill=color)
            elif entry.accelerator:
                c.create_text(m.right_x, mid, text=entry.accelerator, anchor="e", font=f.ui,
                              fill=p.muted if entry.enabled else p.disabled)

    def _show(self, x: int, y: int) -> None:
        w, h = self._size
        self._pos = (x, y)
        self.top.wm_geometry(f"{w}x{h}+{x}+{y}")
        self.top.deiconify()
        self.top.lift()

    def _hide(self) -> None:
        self.active = -1
        try:
            self.top.withdraw()
        except tk.TclError:
            pass

    # --- items -----------------------------------------------------------------

    def _contains(self, x: int, y: int) -> bool:
        (left, top), (w, h) = self._pos, self._size
        return left <= x < left + w and top <= y < top + h

    def _index_at(self, x: int, y: int) -> int:
        """The selectable entry at screen position (x, y), or -1."""
        x -= self._pos[0]
        y -= self._pos[1]
        if not 0 <= x < self._size[0]:
            return -1
        for i, (y0, y1) in enumerate(self._rows):
            if y0 <= y < y1:
                return i if self.entries[i].selectable else -1
        return -1

    def _set_active(self, index: int) -> None:
        if index != self.active:
            self.active = index
            self._draw()

    def _move(self, step: int) -> None:
        choices = [i for i, e in enumerate(self.entries) if e.selectable]
        if not choices:
            return
        if self.active in choices:
            self._set_active(choices[(choices.index(self.active) + step) % len(choices)])
        else:
            self._set_active(choices[0] if step > 0 else choices[-1])


def _glyph(fonts, pair: tuple[str, str]) -> tuple[tkfont.Font, str]:
    """The icon font's glyph where it's installed (Windows 10 and 11), otherwise the text fallback."""
    return (fonts.glyph, pair[0]) if fonts.glyph is not None else (fonts.ui, pair[1])


def _round_rect(c: tk.Canvas, x0: int, y0: int, x1: int, y1: int, r: int, **kw) -> int:
    points = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
              x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0)
    return c.create_polygon(points, smooth=True, **kw)


class MenuBar(ttk.Frame):
    """Menu titles styled like the toolbar's buttons. Alt, F10 and Alt+letter work as usual."""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.items: list[ttk.Label] = []
        self.menus: list[Menu] = []
        self._underlines: list[int] = []
        self._alt_alone = False
        self._ctl = _controller(self.winfo_toplevel())
        self._ctl.bar = self
        # On the toplevel, so these work wherever the focus is in the window. Returning
        # "break" also keeps Tk from passing Alt and F10 on to Windows, which would put a
        # window without a native menu bar into an invisible system-menu mode.
        top = self.winfo_toplevel()
        for side in ("L", "R"):
            top.bind(f"<KeyPress-Alt_{side}>", self._on_alt_press, add="+")
            top.bind(f"<KeyRelease-Alt_{side}>", self._on_alt_release, add="+")
        for seq in ("<KeyPress>", "<ButtonPress>", "<FocusOut>"):  # Alt was part of something else
            top.bind(seq, self._on_other_key, add="+")
        top.bind("<KeyPress-F10>", self._on_f10, add="+")

    def add_cascade(self, label: str, menu: Menu, underline: int = 0) -> None:
        index = len(self.items)
        item = ttk.Label(self, text=label, style="Toolbutton")
        item.pack(side="left")
        item.bind("<Enter>", lambda e: self._hover(index, True))
        item.bind("<Leave>", lambda e: self._hover(index, False))
        item.bind("<ButtonPress-1>", lambda e: self._ctl.open_bar(index))
        self.items.append(item)
        self.menus.append(menu)
        self._underlines.append(underline)
        key = label[underline]
        if key.isalpha():
            for k in {key.lower(), key.upper()}:
                self.winfo_toplevel().bind(f"<Alt-KeyPress-{k}>", lambda e, i=index: self._on_alt_key(e, i), add="+")

    def index_at(self, x: int, y: int) -> int | None:
        for i, item in enumerate(self.items):
            left, top = item.winfo_rootx(), item.winfo_rooty()
            if left <= x < left + item.winfo_width() and top <= y < top + item.winfo_height():
                return i
        return None

    def find(self, char: str) -> int | None:
        char = char.lower()
        for i, item in enumerate(self.items):
            if str(item.cget("text"))[self._underlines[i]].lower() == char:
                return i
        return None

    def highlight(self, index: int | None, mnemonics: bool) -> None:
        for i, item in enumerate(self.items):
            item.state(["active"] if i == index else ["!active"])
            item.configure(underline=self._underlines[i] if mnemonics else -1)

    def _hover(self, index: int, inside: bool) -> None:
        if not self._ctl.active:  # while a menu is open, the controller decides
            self.items[index].state(["active"] if inside else ["!active"])

    def _on_alt_press(self, e) -> str:
        self._alt_alone = not e.state & _CONTROL  # Ctrl+Alt is AltGr on many keyboards
        return "break"

    def _on_other_key(self, _e=None) -> None:
        self._alt_alone = False

    def _on_alt_release(self, _e) -> str:
        if self._alt_alone and self.items:
            self._ctl.keyboard_mode(0)
        self._alt_alone = False
        return "break"

    def _on_f10(self, e) -> str | None:
        if e.state & _SHIFT or not self.items:  # Shift+F10 opens the context menu
            return None
        self._ctl.keyboard_mode(0)
        return "break"

    def _on_alt_key(self, e, index: int) -> str | None:
        if e.state & _CONTROL:
            return None
        self._alt_alone = False
        self._ctl.keyboard = True
        self._ctl.open_bar(index, select_first=True)
        return "break"


def _controller(owner: tk.Misc) -> _Controller:
    ctl = getattr(owner, "_jsonview_menus", None)
    if ctl is None:
        ctl = owner._jsonview_menus = _Controller(owner)  # type: ignore[attr-defined]
    return ctl


class _Controller:
    """Grab, focus, and the chain of open menus for one window."""

    def __init__(self, owner: tk.Misc) -> None:
        self.owner = owner
        self.bar: MenuBar | None = None
        self.chain: list[Menu] = []  # open menus, outermost first
        self.bar_index: int | None = None  # highlighted menu bar title, if any
        self.keyboard = False  # opened from the keyboard: underline the menu bar's access keys
        self.active = False  # holding the grab and the focus
        self._prev_focus: tk.Misc | None = None
        self._timer: str | None = None
        self._origin = (0, 0)
        self._moved = False
        self._pressed_inside = False
        self._alt_alone = False
        self.sink = sink = tk.Frame(owner, width=1, height=1, borderwidth=0, highlightthickness=0, takefocus=0)
        sink.place(x=0, y=0)
        sink.lower()
        sink.bindtags((str(sink),))  # only the bindings below, none of the window's shortcuts
        sink.bind("<Motion>", self._on_motion)
        sink.bind("<ButtonPress>", self._on_press)
        sink.bind("<ButtonRelease>", self._on_release)
        sink.bind("<KeyPress>", self._on_key)
        # Tk resolves "Alt" per platform (on Windows, the state bit that means Alt on X11 is Num Lock)
        sink.bind("<Alt-KeyPress>", lambda e: self._on_key(e, alt=True))
        sink.bind("<KeyRelease>", self._on_key_release)
        sink.bind("<FocusOut>", self._on_focus_out)
        owner.bind("<Configure>", self._on_owner_configure, add="+")
        owner.bind("<Unmap>", self._on_owner_configure, add="+")

    # --- opening and closing ---------------------------------------------------

    def _begin(self) -> None:
        if self.active:
            return
        try:
            focus = self.owner.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        in_owner = focus is not None and focus is not self.sink and focus.winfo_toplevel() is self.owner
        self._prev_focus = focus if in_owner else None
        self.sink.focus_set()
        try:
            self.sink.grab_set()
        except tk.TclError:
            pass
        self.active = True

    def _hide_menus(self) -> None:
        self._cancel_timer()
        while self.chain:
            self.chain.pop()._hide()

    def close_all(self) -> None:
        self._hide_menus()
        self.bar_index = None
        self.keyboard = False
        if self.bar is not None:
            self.bar.highlight(None, mnemonics=False)
        if not self.active:
            return
        self.active = False
        try:
            self.sink.grab_release()
        except tk.TclError:
            pass
        try:
            focus = self.owner.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        # Hand the focus back, unless something else has taken it since. When the app
        # isn't active this only sets where the focus goes once it is.
        if focus is None or focus is self.sink:
            target = self._prev_focus
            try:
                (target if target is not None and target.winfo_exists() else self.owner).focus_set()
            except tk.TclError:
                pass
        self._prev_focus = None

    def popup(self, menu: Menu, x: int, y: int) -> None:
        self.close_all()
        self._begin()
        menu._prepare()
        self._place(menu, x, y, x, y)
        self.chain = [menu]
        self._reset_pointer()

    def open_bar(self, index: int, *, select_first: bool = False) -> None:
        assert self.bar is not None
        self._begin()
        self._hide_menus()
        self._set_bar(index)
        menu, item = self.bar.menus[index], self.bar.items[index]
        menu._prepare()
        x, y = item.winfo_rootx(), item.winfo_rooty()
        self._place(menu, x, y + item.winfo_height(), x + item.winfo_width(), y)
        self.chain = [menu]
        self._reset_pointer()
        if select_first:
            menu._move(1)

    def keyboard_mode(self, index: int) -> None:
        """Highlight a menu bar title without opening it, like tapping Alt in a native app."""
        if self.bar is None:
            return
        self._begin()
        self._hide_menus()
        self.keyboard = True
        self._set_bar(index)

    def open_submenu(self, menu: Menu, index: int, *, select_first: bool = False) -> None:
        sub = menu.entries[index].submenu
        if sub is None or menu not in self.chain:
            return
        depth = self.chain.index(menu)
        if not (depth + 1 < len(self.chain) and self.chain[depth + 1] is sub):
            self._close_below(menu)
            sub._prepare()
            sub.parent_index = index
            (x, y), w, (y0, y1) = menu._pos, menu._size[0], menu._rows[index]
            overlap, pad = menu._m.pad, sub._m.pad
            # beside the entry, its first entry level with it; else to the left, or bottom-aligned
            self._place(sub, x + w - overlap, y + y0 - pad, x + overlap, y + y1 + pad)
            self.chain.append(sub)
        if select_first and sub.active < 0:
            sub._move(1)

    def invoke(self, menu: Menu, index: int) -> None:
        entry = menu.entries[index]
        if not entry.selectable:
            return
        if entry.kind == "cascade":
            self.open_submenu(menu, index, select_first=True)
            return
        self.close_all()
        if entry.kind == "check":
            entry.variable.set(not entry.variable.get())
        elif entry.kind == "radio":
            entry.variable.set(entry.value)
        if entry.command is not None:
            entry.command()

    def _place(self, menu: Menu, x: int, y: int, alt_right: int, alt_bottom: int) -> None:
        """Show the menu at (x, y). Where that would run off the monitor, it ends at
        alt_right / alt_bottom instead (left of / above the thing it opened from)."""
        w, h = menu._size
        left, top, right, bottom = winapi.work_area(self.owner, x, y)
        if x + w > right:
            x = alt_right - w if alt_right - w >= left else right - w
        if y + h > bottom:
            y = alt_bottom - h if alt_bottom - h >= top else bottom - h
        menu._show(max(left, x), max(top, y))

    def _close_below(self, menu: Menu) -> None:
        while self.chain and self.chain[-1] is not menu:
            self.chain.pop()._hide()

    def _set_bar(self, index: int) -> None:
        self.bar_index = index
        if self.bar is not None:
            self.bar.highlight(index, mnemonics=self.keyboard)

    def _step_bar(self, step: int) -> None:
        if self.bar is not None and self.bar_index is not None:
            self.open_bar((self.bar_index + step) % len(self.bar.menus), select_first=True)

    # --- pointer -----------------------------------------------------------------

    def _reset_pointer(self) -> None:
        self._origin = self.owner.winfo_pointerxy()
        self._moved = self._pressed_inside = False

    def _menu_at(self, x: int, y: int) -> Menu | None:
        return next((m for m in reversed(self.chain) if m._contains(x, y)), None)

    def _on_motion(self, e) -> None:
        x, y = e.x_root, e.y_root
        if abs(x - self._origin[0]) + abs(y - self._origin[1]) > 3:
            self._moved = True
        menu = self._menu_at(x, y)
        if menu is not None:
            depth = self.chain.index(menu)
            if depth:  # inside a submenu: keep the entry that opened it highlighted
                self.chain[depth - 1]._set_active(menu.parent_index)
            index = menu._index_at(x, y)
            if index != menu.active:
                menu._set_active(index)
                self._schedule(menu, index)
            return
        if self.chain and self._moved:
            self.chain[-1]._set_active(-1)
        if self.bar is not None and self.chain and self.bar_index is not None:
            index = self.bar.index_at(x, y)
            if index is not None and index != self.bar_index:  # slide along the menu bar
                self.keyboard = False
                self.open_bar(index)

    def _on_press(self, e) -> None:
        x, y = e.x_root, e.y_root
        menu = self._menu_at(x, y)
        if menu is not None:
            self._pressed_inside = True
            index = menu._index_at(x, y)
            if index >= 0 and menu.entries[index].kind == "cascade":
                menu._set_active(index)
                self.open_submenu(menu, index)
            return
        index = self.bar.index_at(x, y) if self.bar is not None else None
        if index is not None and not (self.chain and index == self.bar_index):
            self.keyboard = False
            self.open_bar(index)
        else:
            self.close_all()  # like native menus, the click doesn't reach what's under it

    def _on_release(self, e) -> None:
        pressed, self._pressed_inside = self._pressed_inside, False
        menu = self._menu_at(e.x_root, e.y_root)
        # A release right where the menu opened is the end of the click that opened it.
        if menu is None or not (pressed or self._moved):
            return
        index = menu._index_at(e.x_root, e.y_root)
        if index >= 0 and menu.entries[index].kind != "cascade":
            self.invoke(menu, index)

    def _schedule(self, menu: Menu, index: int) -> None:
        self._cancel_timer()
        self._timer = self.owner.after(SUBMENU_DELAY_MS, self._settle, menu, index)

    def _settle(self, menu: Menu, index: int) -> None:
        """The pointer has rested on an entry: open its submenu, or close the one open."""
        self._timer = None
        if menu not in self.chain or menu.active != index:
            return
        if index >= 0 and menu.entries[index].kind == "cascade":
            self.open_submenu(menu, index)
        else:
            self._close_below(menu)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self.owner.after_cancel(self._timer)
            self._timer = None

    def _on_focus_out(self, _e) -> None:
        # Another app was activated. (On X11 with focus following the mouse, merely
        # pointing at a popup takes the focus from the window; that's no reason to close.)
        if self.active and self._menu_at(*self.owner.winfo_pointerxy()) is None:
            self.close_all()

    def _on_owner_configure(self, e) -> None:
        if self.active and e.widget is self.owner:  # the window moved, resized or minimized
            self.close_all()

    # --- keyboard ----------------------------------------------------------------

    def _on_key(self, e, alt: bool = False) -> str:
        key = e.keysym
        if key in ("Alt_L", "Alt_R"):
            self._alt_alone = True
            return "break"
        self._alt_alone = False
        if not self.active or key == "F10":
            self.close_all()
        elif alt:  # Alt+letter switches to that menu, even from inside another one
            index = self.bar.find(e.char) if self.bar is not None and e.char else None
            if index is not None:
                self.keyboard = True
                self.open_bar(index, select_first=True)
        elif not self.chain:
            self._on_bar_key(e)
        else:
            self._on_menu_key(e, self.chain[-1])
        return "break"

    def _on_key_release(self, e) -> str:
        if e.keysym in ("Alt_L", "Alt_R") and self._alt_alone:  # tapping Alt again closes the menus
            self._alt_alone = False
            self.close_all()
        return "break"

    def _on_bar_key(self, e) -> None:
        assert self.bar is not None and self.bar_index is not None
        key, count = e.keysym, len(self.bar.items)
        if key in ("Left", "Right"):
            self._set_bar((self.bar_index + (1 if key == "Right" else -1)) % count)
        elif key in ("Down", "Up", "Return", "KP_Enter", "space"):
            self.open_bar(self.bar_index, select_first=True)
        elif key == "Escape":
            self.close_all()
        elif e.char and e.char.isprintable():
            index = self.bar.find(e.char)
            if index is not None:
                self.open_bar(index, select_first=True)

    def _on_menu_key(self, e, menu: Menu) -> None:
        key = e.keysym
        entry = menu.entries[menu.active] if menu.active >= 0 else None
        if key in ("Down", "Up"):
            menu._move(1 if key == "Down" else -1)
        elif key in ("Home", "End"):
            menu.active = -1
            menu._move(1 if key == "Home" else -1)
        elif key == "Right":
            if entry is not None and entry.kind == "cascade":
                self.open_submenu(menu, menu.active, select_first=True)
            else:
                self._step_bar(1)
        elif key == "Left":
            if len(self.chain) > 1:
                self._close_below(self.chain[-2])
            else:
                self._step_bar(-1)
        elif key in ("Return", "KP_Enter", "space"):
            if entry is not None:
                self.invoke(menu, menu.active)
        elif key == "Escape":
            if len(self.chain) > 1:
                self._close_below(self.chain[-2])
            elif self.bar_index is not None:
                self.keyboard_mode(self.bar_index)  # back to the menu bar, like native menus
            else:
                self.close_all()
        elif e.char and e.char.isprintable() and not e.char.isspace():
            self._jump_to(menu, e.char.lower())

    def _jump_to(self, menu: Menu, char: str) -> None:
        """Typing a letter selects the entries starting with it in turn. Enter runs one;
        a stray key never does (it could be Exit)."""
        matches = [i for i, en in enumerate(menu.entries) if en.selectable and en.label[:1].lower() == char]
        if matches:
            later = [i for i in matches if i > menu.active]
            menu._set_active((later or matches)[0])
