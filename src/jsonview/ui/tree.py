"""The tree: rows are created only when their parent is expanded."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Any

from ..core import formatting as fmt
from ..core.model import (
    DIRECT_LIMIT, Bucket, Entry, Path, Row, bucket_key_ranges, is_container, path_exists,
    plan_children, resolve, root_entry,
)
from .theme import Palette


@dataclass
class TreeState:
    """What's expanded and selected, so a reload can put things back."""

    open_paths: list[Path] = field(default_factory=list)
    selected: Path | None = None
    scroll: float = 0.0


def autohide(scrollbar: ttk.Scrollbar) -> Callable[[str, str], None]:
    """A scroll command that hides the scrollbar when everything fits."""

    def set_(lo: str, hi: str) -> None:
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            scrollbar.grid_remove()
        else:
            scrollbar.grid()
        scrollbar.set(lo, hi)

    return set_


class JsonTree(ttk.Frame):
    def __init__(self, master: tk.Misc, *, on_select: Callable[[Row | None], None]) -> None:
        super().__init__(master)
        self.on_select = on_select
        scale = self.winfo_fpixels("1i") / 96.0
        self.tv = tv = ttk.Treeview(self, columns=("value", "type"), show="tree headings", selectmode="browse")
        tv.heading("#0", text="Key", anchor="w")
        tv.heading("value", text="Value", anchor="w")
        tv.heading("type", text="Type", anchor="w")
        # Value starts narrow and stretches, so the columns never add up to more than the pane
        tv.column("#0", width=round(260 * scale), minwidth=round(100 * scale), stretch=False)
        tv.column("value", width=round(160 * scale), minwidth=round(100 * scale), stretch=True)
        tv.column("type", width=round(76 * scale), minwidth=round(60 * scale), stretch=False)
        ysb = ttk.Scrollbar(self, orient="vertical", command=tv.yview)
        xsb = ttk.Scrollbar(self, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=autohide(ysb), xscrollcommand=autohide(xsb))
        tv.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        tv.bind("<<TreeviewOpen>>", self._on_open)
        tv.bind("<<TreeviewSelect>>", lambda _e: self.on_select(self.selected_row()))
        self._reset()

    # --- state ---------------------------------------------------------------

    def _reset(self) -> None:
        self.data: Any = None
        self.loaded = False
        self._rows: dict[str, Row] = {}
        self._iid_by_path: dict[Path, str] = {}
        self._populated: set[str] = set()
        self._placeholders: set[str] = set()
        self._match_paths: frozenset[Path] = frozenset()
        self._matched_iids: set[str] = set()
        self._key_lists: dict[Path, list] = {}  # key order of big objects, so ranges slice cheaply
        self._key_positions: dict[Path, dict[Any, int]] = {}

    def clear(self) -> None:
        children = self.tv.get_children("")
        if children:
            self.tv.delete(*children)
        self._reset()

    def load(self, data: Any) -> None:
        self.clear()
        self.data = data
        self.loaded = True
        self._populated.add("")
        if is_container(data) and len(data) > 0:
            self._insert_children("", data, ())
        else:  # a scalar or empty document still gets one visible row
            self._insert("", root_entry(data))
        first = self.tv.get_children("")
        if first:
            self.tv.focus(first[0])

    # --- rows ----------------------------------------------------------------

    def _keys(self, container: Any, path: Path) -> list | None:
        if isinstance(container, Mapping) and len(container) > DIRECT_LIMIT:
            keys = self._key_lists.get(path)
            if keys is None:
                keys = self._key_lists[path] = list(container.keys())
            return keys
        return None

    def _insert_children(self, parent: str, container: Any, path: Path, start: int = 0, stop: int | None = None) -> None:
        keys = self._keys(container, path)
        rows = plan_children(container, path, start, stop, keys=keys)
        buckets = [r for r in rows if isinstance(r, Bucket)]
        labels = iter(bucket_key_ranges(container, buckets, keys))
        for row in rows:
            self._insert(parent, row, next(labels) if isinstance(row, Bucket) else "")

    def _insert(self, parent: str, row: Row, bucket_label: str = "") -> str:
        tv = self.tv
        if isinstance(row, Bucket):
            iid = tv.insert(
                parent, "end", text=row.label, values=(bucket_label, "range"), tags=("bucket",),
            )
            self._add_placeholder(iid)
        else:
            tags: tuple[str, ...] = (f"t_{row.kind}",)
            if row.path in self._match_paths:
                tags += ("match",)
            iid = tv.insert(
                parent, "end", text=fmt.key_label(row.key),
                values=(fmt.preview(row.value, row.kind), fmt.type_label(row.value, row.kind)),
                tags=tags,
            )
            self._iid_by_path[row.path] = iid
            if "match" in tags:
                self._matched_iids.add(iid)
            if row.is_container and len(row.value) > 0:
                self._add_placeholder(iid)
        self._rows[iid] = row
        return iid

    def _add_placeholder(self, iid: str) -> None:
        """An invisible child so Tk draws an expand arrow before the real children exist."""
        self._placeholders.add(self.tv.insert(iid, "end", text=""))

    def _populate(self, iid: str) -> None:
        if iid in self._populated:
            return
        self._populated.add(iid)
        for child in self.tv.get_children(iid):
            if child in self._placeholders:
                self.tv.delete(child)
                self._placeholders.discard(child)
        row = self._rows.get(iid)
        if isinstance(row, Bucket):
            self._insert_children(iid, resolve(self.data, row.path), row.path, row.start, row.stop)
        elif isinstance(row, Entry) and row.is_container:
            self._insert_children(iid, row.value, row.path)

    def _on_open(self, _event=None) -> None:
        # Tk focuses the item before firing <<TreeviewOpen>>, for both mouse and keyboard.
        iid = self.tv.focus()
        if iid:
            self._populate(iid)

    # --- queries ---------------------------------------------------------------

    def row(self, iid: str) -> Row | None:
        return self._rows.get(iid)

    def selected_iid(self) -> str | None:
        sel = self.tv.selection()
        return sel[0] if sel else None

    def selected_row(self) -> Row | None:
        iid = self.selected_iid()
        return self._rows.get(iid) if iid else None

    def is_open(self, iid: str) -> bool:
        return bool(self.tv.item(iid, "open"))

    def has_children(self, iid: str) -> bool:
        return bool(self.tv.get_children(iid))

    # --- navigation --------------------------------------------------------------

    def select(self, iid: str) -> None:
        self.tv.selection_set(iid)
        self.tv.focus(iid)
        self.tv.see(iid)  # also opens every ancestor

    def toggle(self, iid: str) -> None:
        self._populate(iid)
        self.tv.item(iid, open=not self.is_open(iid))

    def reveal(self, path: Path) -> str | None:
        """Create the rows down to ``path`` (through any buckets) and return its item id."""
        if not self.loaded:
            return None
        if path in self._iid_by_path or not path:
            return self._iid_by_path.get(path)
        parent = ""
        for depth in range(len(path)):
            target = path[: depth + 1]
            while target not in self._iid_by_path:
                self._populate(parent)
                if target in self._iid_by_path:
                    break
                bucket = self._bucket_containing(parent, path[:depth], path[depth])
                if bucket is None:
                    return None
                parent = bucket
            parent = self._iid_by_path[target]
        return parent

    def _bucket_containing(self, parent: str, container_path: Path, key: Any) -> str | None:
        try:
            container = resolve(self.data, container_path)
        except KeyError:
            return None
        if isinstance(container, Mapping):
            positions = self._key_positions.get(container_path)
            if positions is None:
                keys = self._keys(container, container_path) or list(container)
                positions = self._key_positions[container_path] = {k: i for i, k in enumerate(keys)}
            pos = positions.get(key)
        else:
            pos = key if isinstance(key, int) and 0 <= key < len(container) else None
        if pos is None:
            return None
        for child in self.tv.get_children(parent):
            row = self._rows.get(child)
            if isinstance(row, Bucket) and row.start <= pos < row.stop:
                return child
        return None

    def _open_ancestors(self, iid: str) -> None:
        parent = self.tv.parent(iid)
        while parent:
            self.tv.item(parent, open=True)
            parent = self.tv.parent(parent)

    # --- expand / collapse -----------------------------------------------------

    def expand(self, iid: str = "") -> None:
        """Expand ``iid`` and everything below it (the whole tree if empty)."""
        stack = [iid] if iid else list(self.tv.get_children(""))
        while stack:
            current = stack.pop()
            self._populate(current)
            children = self.tv.get_children(current)
            if children:
                self.tv.item(current, open=True)
                stack.extend(children)

    def collapse(self, iid: str = "") -> None:
        stack = [iid] if iid else list(self.tv.get_children(""))
        while stack:
            current = stack.pop()
            if current in self._populated:
                self.tv.item(current, open=False)
                stack.extend(self.tv.get_children(current))
        if iid:
            self.tv.item(iid, open=False)

    # --- search highlighting -----------------------------------------------------

    def set_matches(self, paths: Iterable[Path]) -> None:
        for iid in self._matched_iids:
            if self.tv.exists(iid):
                tags = self.tv.item(iid, "tags") or ()
                self.tv.item(iid, tags=tuple(t for t in tags if t != "match"))
        self._matched_iids = set()
        self._match_paths = frozenset(paths)
        for path in self._match_paths:
            iid = self._iid_by_path.get(path)
            if iid is not None:
                tags = self.tv.item(iid, "tags") or ()
                self.tv.item(iid, tags=tuple(tags) + ("match",))
                self._matched_iids.add(iid)

    # --- reload support ------------------------------------------------------------

    def capture_state(self) -> TreeState:
        open_paths = [
            row.path for iid, row in self._rows.items()
            if isinstance(row, Entry) and iid in self._populated and self.is_open(iid)
        ]
        selected = self.selected_row()
        return TreeState(
            open_paths=open_paths,
            selected=selected.path if isinstance(selected, Entry) else None,
            scroll=self.tv.yview()[0],
        )

    def restore_state(self, state: TreeState) -> None:
        for path in sorted(state.open_paths, key=len):
            if path_exists(self.data, path):
                iid = self.reveal(path)
                if iid:
                    self._populate(iid)
                    self.tv.item(iid, open=True)
                    self._open_ancestors(iid)
        if state.selected is not None and path_exists(self.data, state.selected):
            iid = self.reveal(state.selected)
            if iid:
                self._open_ancestors(iid)
                self.tv.selection_set(iid)
                self.tv.focus(iid)
        self.tv.update_idletasks()
        self.tv.yview_moveto(state.scroll)

    # --- theme -----------------------------------------------------------------------

    def apply_palette(self, p: Palette) -> None:
        tv = self.tv
        tv.tag_configure("t_string", foreground=p.string)
        tv.tag_configure("t_number", foreground=p.number)
        tv.tag_configure("t_boolean", foreground=p.boolean)
        tv.tag_configure("t_null", foreground=p.null)
        tv.tag_configure("t_other", foreground=p.muted)
        tv.tag_configure("bucket", foreground=p.muted)
        tv.tag_configure("match", background=p.match)
