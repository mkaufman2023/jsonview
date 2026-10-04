"""The right-hand pane: the selected value in full, with its path."""

from __future__ import annotations

import re
import tkinter as tk
from bisect import bisect_left
from collections.abc import Callable, Mapping
from itertools import islice
from tkinter import ttk
from typing import Any

from ..core import formatting as fmt
from ..core.model import Bucket, Entry, Kind, Row, resolve
from ..core.paths import to_jsonpath
from .theme import Palette, ThemeManager
from .tree import autohide

TEXT_LIMIT = 200_000  # characters rendered; Copy value always gets the full text
# Tk's text widget slows to a crawl on extremely long lines, so lines are shortened for display.
LINE_LIMITS = {"json": 2_000, "string": 10_000, "note": 10_000}
# Tk 8.6 stores characters outside the Basic Multilingual Plane (emoji) as two positions.
_ASTRAL = re.compile("[\U00010000-\U0010FFFF]") if tk.TkVersion < 9 else None

_TOKEN = re.compile(
    r'(?P<str>"(?:[^"\\]|\\.)*")(?P<colon>\s*:)?'
    r"|(?P<number>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|NaN|-?Infinity)"
    r"|(?P<boolean>\btrue\b|\bfalse\b)"
    r"|(?P<null>\bnull\b)"
    r"|(?P<punct>[{}\[\],:])"
)


class DetailPane(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        theme: ThemeManager,
        *,
        on_open_url: Callable[[str], None],
        on_open_embedded: Callable[[Any, Entry], None],
    ) -> None:
        super().__init__(master)
        self.theme = theme
        self.on_open_url = on_open_url
        self.on_open_embedded = on_open_embedded
        self._row: Row | None = None
        self._embedded: Any = None

        header = ttk.Frame(self, padding=(12, 10, 12, 6))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(header, textvariable=self.path_var, state="readonly", font=theme.fonts.mono)
        self.path_entry.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.meta = ttk.Label(header, style="Muted.TLabel")
        self.meta.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.link_button = ttk.Button(header, text="Open link", command=self._open_link)
        self.embedded_button = ttk.Button(header, text="Open as JSON", command=self._open_embedded)

        self.text = tk.Text(
            self, wrap="none", state="disabled", relief="flat", borderwidth=0,
            highlightthickness=0, padx=14, pady=10, undo=False, font=theme.fonts.mono,
            cursor="xterm", spacing1=1, spacing3=1,
        )
        ysb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        xsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=autohide(ysb), xscrollcommand=autohide(xsb))
        self.text.grid(row=1, column=0, sticky="nsew")
        ysb.grid(row=1, column=1, sticky="ns")
        xsb.grid(row=2, column=0, sticky="ew")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Control-A>", self._select_all)
        self.text.bind("<Button-3>", self._context_menu)
        self._menu: tk.Menu | None = None
        self.show(None)

    # --- content ---------------------------------------------------------------

    def show(self, row: Row | None, data: Any = None) -> None:
        self._row = row
        self._embedded = None
        self.link_button.grid_remove()
        self.embedded_button.grid_remove()
        if row is None:
            self.path_var.set("")
            self.meta.configure(text="")
            self._set_text("Select a row to see its full value and path.", mode="note")
            return
        if isinstance(row, Bucket):
            self._show_bucket(row, data)
            return
        self.path_var.set(to_jsonpath(row.path))
        self.meta.configure(text=fmt.describe(row.value, row.kind))
        if row.kind is Kind.STRING:
            self._set_text(row.value[:TEXT_LIMIT], mode="string", truncated=len(row.value) > TEXT_LIMIT)
            if fmt.is_url(row.value):
                self.link_button.grid(row=1, column=1, sticky="e", pady=(8, 0), padx=(8, 0))
            self._embedded = fmt.parse_embedded_json(row.value)
            if self._embedded is not None:
                self.embedded_button.grid(row=1, column=2, sticky="e", pady=(8, 0), padx=(8, 0))
        else:
            self._show_json(row.value)

    def _show_bucket(self, bucket: Bucket, data: Any) -> None:
        container = resolve(data, bucket.path)
        self.path_var.set(to_jsonpath(bucket.path))
        noun = "keys" if isinstance(container, Mapping) else "items"
        self.meta.configure(
            text=f"{noun.capitalize()} {bucket.start:,} to {bucket.stop - 1:,} of {len(container):,}"
        )
        if isinstance(container, Mapping):
            subset: Any = dict(islice(container.items(), bucket.start, bucket.stop))
        else:
            subset = container[bucket.start : bucket.stop]
        self._show_json(subset)

    def _show_json(self, value: Any) -> None:
        try:
            text, truncated = fmt.to_json_limited(value, TEXT_LIMIT)
        except RecursionError:
            self._set_text("This value is nested too deeply to show here. Expand it in the tree instead.", mode="note")
            return
        self._set_text(text, mode="json", truncated=truncated)

    @staticmethod
    def _shorten_lines(content: str, limit: int) -> tuple[str, bool]:
        if len(content) <= limit:
            return content, False
        lines = content.split("\n")
        shortened = False
        for i, line in enumerate(lines):
            if len(line) > limit:
                lines[i] = line[:limit] + " …"
                shortened = True
        return ("\n".join(lines), True) if shortened else (content, False)

    def _set_text(self, content: str, *, mode: str, truncated: bool = False) -> None:
        t = self.text
        content, shortened = self._shorten_lines(content, LINE_LIMITS[mode])
        t.configure(state="normal", wrap="none" if mode == "json" else "word")
        t.delete("1.0", "end")
        t.insert("1.0", content, ("note",) if mode == "note" else ())
        if mode == "json":
            self._highlight(content)
        notes = []
        if shortened:
            notes.append("Very long lines are shortened here.")
        if truncated:
            notes.append(f"Showing the first {TEXT_LIMIT:,} characters.")
        if notes:
            t.insert("end", "\n\n" + " ".join(notes) + " Copy value copies all of it.", ("note",))
        t.configure(state="disabled")
        t.yview_moveto(0)
        t.xview_moveto(0)

    def _highlight(self, content: str) -> None:
        ranges: dict[str, list[str]] = {k: [] for k in ("key", "string", "number", "boolean", "null", "punct")}
        for lineno, line in enumerate(content.split("\n"), start=1):
            astral = [m.start() for m in _ASTRAL.finditer(line)] if _ASTRAL else []

            def col(i: int) -> str:  # a Python string index as a Tk text index
                return f"{lineno}.{i + bisect_left(astral, i) if astral else i}"

            for m in _TOKEN.finditer(line):
                if m.group("str") is not None:
                    tag = "key" if m.group("colon") else "string"
                    ranges[tag] += (col(m.start("str")), col(m.end("str")))
                    if m.group("colon"):
                        ranges["punct"] += (col(m.end("str")), col(m.end()))
                else:
                    tag = m.lastgroup or "punct"
                    ranges[tag] += (col(m.start()), col(m.end()))
        for tag, idx in ranges.items():
            for i in range(0, len(idx), 2000):  # Tcl copes with long arg lists, but keep calls modest
                self.text.tag_add(tag, *idx[i : i + 2000])

    # --- actions ---------------------------------------------------------------

    def _open_link(self) -> None:
        if isinstance(self._row, Entry):
            self.on_open_url(self._row.value)

    def _open_embedded(self) -> None:
        if self._embedded is not None and isinstance(self._row, Entry):
            self.on_open_embedded(self._embedded, self._row)

    def _select_all(self, _event=None) -> str:
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _context_menu(self, event) -> None:
        if self._menu is not None:
            self._menu.destroy()
        self._menu = menu = tk.Menu(self, tearoff=False)
        has_sel = bool(self.text.tag_ranges("sel"))
        menu.add_command(label="Copy", accelerator="Ctrl+C", state="normal" if has_sel else "disabled",
                         command=lambda: self.text.event_generate("<<Copy>>"))
        menu.add_command(label="Select all", accelerator="Ctrl+A", command=self._select_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # --- theme -------------------------------------------------------------------

    def apply_palette(self, p: Palette) -> None:
        f = self.theme.fonts
        self.text.configure(
            background=p.field, foreground=p.text, insertbackground=p.text,
            selectbackground=p.accent, selectforeground=p.field, inactiveselectbackground=p.muted,
        )
        self.text.tag_configure("key", foreground=p.text)
        self.text.tag_configure("string", foreground=p.string)
        self.text.tag_configure("number", foreground=p.number)
        self.text.tag_configure("boolean", foreground=p.boolean)
        self.text.tag_configure("null", foreground=p.null)
        self.text.tag_configure("punct", foreground=p.muted)
        self.text.tag_configure("note", foreground=p.muted, font=f.ui)
        self.text.tag_raise("sel")
