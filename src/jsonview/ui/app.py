"""The viewer window and the process-wide session that owns all windows."""

from __future__ import annotations

import os
import re
import sys
import time
import traceback
import webbrowser
from collections.abc import Callable, Mapping
from itertools import islice
from pathlib import Path as FilePath
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any

from ..core import formatting as fmt
from ..core.loader import Document, LoadError, load_file, load_text
from ..core.model import Bucket, Entry, Kind, Row, count_nodes, iter_node_count, kind_of, resolve
from ..core.paths import PathStyle, format_path, to_jsonpath
from ..core.search import Query, iter_matches
from ..settings import Settings
from . import dialogs, winapi
from .detail import DetailPane
from .theme import Palette, ThemeManager
from .tree import JsonTree, TreeState
from .widgets import PlaceholderEntry, Tooltip

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = DND_FILES = None

APP_NAME = "JSON Viewer"
ASSETS = FilePath(__file__).resolve().parent.parent / "assets"
FILETYPES = [("JSON files", "*.json *.jsonl *.ndjson *.geojson"), ("All files", "*.*")]
MAX_MATCHES = 10_000
EXPAND_CONFIRM_NODES = 20_000
SEARCH_DELAY_MS = 250
DETAIL_DELAY_MS = 40  # holding an arrow key renders the details pane once, when you stop
WORK_SLICE_S = 0.015  # how long background-ish work may hold the UI per step

# Segoe Fluent Icons glyphs (Windows 11), with text fallbacks for other systems
ICONS = {
    "open": ("\ue8e5", "Open"),
    "paste": ("\ue77f", "Paste"),
    "reload": ("\ue72c", "Reload"),
    "search": ("\ue721", ""),
    "prev": ("\ue70e", "▲"),
    "next": ("\ue70d", "▼"),
    "sun": ("\ue706", "Light"),
    "moon": ("\ue708", "Dark"),
}


def _human_size(n: int | None) -> str | None:
    if n is None:
        return None
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return None


def _bind_letter(widget: tk.Misc, mods: str, letter: str, handler: Callable) -> None:
    """Bind Ctrl+<letter>, also when Caps Lock is on."""
    if "Shift" in mods:
        widget.bind(f"<{mods}-{letter.upper()}>", handler)
    else:
        widget.bind(f"<{mods}-{letter.lower()}>", handler)
        widget.bind(f"<{mods}-Lock-{letter.upper()}>", handler)


class Session:
    """State shared by every window in the process: settings, theme, drag-and-drop support."""

    def __init__(self, root: tk.Tk, settings: Settings, dnd: bool, theme: str | None = None) -> None:
        self.root = root
        self.settings = settings
        self.dnd = dnd
        self.windows: list[ViewerWindow] = []
        root.report_callback_exception = self._report_exception
        # A theme passed in (--theme, view(theme=...)) applies to this session only.
        self.theme = ThemeManager(root, theme or settings.theme)
        self._initial_theme = self.theme.mode
        self.auto_reload_var = tk.BooleanVar(root, settings.auto_reload)  # shared by every window
        self._set_icon()

    def _set_icon(self) -> None:
        try:
            if winapi.IS_WINDOWS and (ASSETS / "icon.ico").exists():
                self.root.iconbitmap(default=str(ASSETS / "icon.ico"))
            elif (ASSETS / "icon.png").exists():
                self._icon_image = tk.PhotoImage(file=str(ASSETS / "icon.png"))
                self.root.iconphoto(True, self._icon_image)
        except tk.TclError:
            pass

    def _report_exception(self, exc_type, exc, tb) -> None:
        """Show unexpected errors instead of losing them (there's no console under jsonview.exe)."""
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        print(details, file=sys.stderr)
        try:
            messagebox.showerror(APP_NAME, f"Something went wrong:\n\n{exc}\n\n{details[-1500:]}")
        except tk.TclError:  # the app is already shutting down
            pass

    def new_window(self, doc: Document | None = None) -> ViewerWindow:
        anchor = self.windows[0].win if self.windows else self.root
        offset = 40 * (1 + len(self.windows) % 6)  # cascade, so new windows don't hide each other
        top = tk.Toplevel(self.root)
        window = ViewerWindow(top, self, is_main=False)
        x, y = anchor.winfo_rootx() + offset, anchor.winfo_rooty() + offset
        top.geometry(f"{anchor.winfo_width()}x{anchor.winfo_height()}+{x}+{y}")
        if doc is not None:
            window.show_document(doc)
        self.theme.style_window(top)
        return window

    def quit(self) -> None:
        for window in list(self.windows):
            window.save_layout()
        if self.theme.mode != self._initial_theme:  # changed in the app, so remember it
            self.settings.theme = self.theme.mode
        self.settings.save()
        for window in list(self.windows):
            window.shutdown()
        self.theme.stop()
        # Cancel every pending timer, so nothing fires into a destroyed app (or into the
        # next one, when view() is called again from the same Python session).
        for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(job)
        self.root.destroy()


class ViewerWindow:
    def __init__(self, win: tk.Tk | tk.Toplevel, session: Session, *, is_main: bool) -> None:
        self.win = win
        self.session = session
        self.settings = session.settings
        self.theme = session.theme
        self.is_main = is_main
        self.doc: Document | None = None
        self.alive = True
        self._after = session.root.after
        self._cancel = session.root.after_cancel
        self._searched_text = ""

        self._error: LoadError | None = None
        self._watch_path: FilePath | None = None
        self._watch_sig: tuple[int, int] | None = None
        self._pending_sig: tuple[int, int] | None = None
        self._status_text = ""
        self._flash_job: str | None = None
        self._count_token = 0
        self._search_after: str | None = None
        self._search_token = 0
        self._search_done = True
        self._search_error = False
        self._search_truncated = False
        self._matches: list = []
        self._match_index = -1
        self._detail_job: str | None = None
        self._menu: tk.Menu | None = None
        self._banner_error: tuple[LoadError, FilePath | None] | None = None
        self._loaded_at = ""

        win.title(APP_NAME)
        scale = win.winfo_fpixels("1i") / 96.0
        win.minsize(round(640 * scale), round(400 * scale))
        win.columnconfigure(0, weight=1)
        win.rowconfigure(2, weight=1)

        self.case_var = tk.BooleanVar(win, False)
        self.regex_var = tk.BooleanVar(win, False)
        self.keys_var = tk.BooleanVar(win, True)
        self.values_var = tk.BooleanVar(win, True)
        self.details_var = tk.BooleanVar(win, self.settings.show_details)
        self.auto_reload_var = session.auto_reload_var
        self.theme_var = tk.StringVar(win, self.theme.mode)

        self._build_menu()
        self._build_toolbar()
        ttk.Separator(win).grid(row=1, column=0, sticky="ew")
        self._build_body()
        self._build_status()
        self._bind_keys()
        self._setup_drop()

        self.theme.listeners.append(self._apply_palette)
        self._apply_palette(self.theme.palette)
        self.show_welcome()
        win.protocol("WM_DELETE_WINDOW", self.close)
        session.windows.append(self)
        self._poll_file()

    # ======================================================================
    # Layout
    # ======================================================================

    def _build_menu(self) -> None:
        bar = tk.Menu(self.win)
        file = tk.Menu(bar, tearoff=False)
        file.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_dialog)
        file.add_command(label="Open from clipboard", accelerator="Ctrl+Shift+V", command=self.open_clipboard)
        self.recent_menu = tk.Menu(file, tearoff=False, postcommand=self._fill_recent_menu)
        file.add_cascade(label="Open recent", menu=self.recent_menu)
        file.add_separator()
        file.add_command(label="Reload", accelerator="F5", command=self.reload)
        file.add_checkbutton(label="Reload when the file changes", variable=self.auto_reload_var,
                             command=self._toggle_auto_reload)
        file.add_separator()
        file.add_command(label="New window", accelerator="Ctrl+N", command=self.session.new_window)
        file.add_command(label="Close window", accelerator="Ctrl+W", command=self.close)
        file.add_command(label="Exit", command=self.session.quit)
        bar.add_cascade(label="File", menu=file)

        edit = tk.Menu(bar, tearoff=False)
        edit.add_command(label="Copy value", accelerator="Ctrl+C", command=self.copy_value)
        edit.add_command(label="Copy key", command=self.copy_key)
        edit.add_cascade(label="Copy path", menu=self._path_menu(edit))
        edit.add_command(label="Copy whole document", command=self.copy_document)
        edit.add_separator()
        edit.add_command(label="Find", accelerator="Ctrl+F", command=self.focus_search)
        edit.add_command(label="Find next", accelerator="F3", command=lambda: self.find_step(1))
        edit.add_command(label="Find previous", accelerator="Shift+F3", command=lambda: self.find_step(-1))
        options = tk.Menu(edit, tearoff=False)
        for label, var in (("Match case", self.case_var), ("Regular expression", self.regex_var),
                           ("Search keys", self.keys_var), ("Search values", self.values_var)):
            options.add_checkbutton(label=label, variable=var, command=self.start_search)
        edit.add_cascade(label="Search options", menu=options)
        bar.add_cascade(label="Edit", menu=edit)

        view = tk.Menu(bar, tearoff=False)
        view.add_command(label="Expand all", accelerator="Ctrl+E", command=self.expand_all)
        view.add_command(label="Collapse all", accelerator="Ctrl+L", command=self.collapse_all)
        view.add_command(label="Expand selected branch", accelerator="*", command=self.expand_branch)
        view.add_command(label="Collapse selected branch", command=self.collapse_branch)
        view.add_separator()
        view.add_checkbutton(label="Details pane", accelerator="Ctrl+D", variable=self.details_var,
                             command=self._apply_details_visibility)
        theme_menu = tk.Menu(view, tearoff=False)
        for label, value in (("Match Windows", "system"), ("Light", "light"), ("Dark", "dark")):
            theme_menu.add_radiobutton(label=label, value=value, variable=self.theme_var,
                                       command=lambda: self.theme.set_mode(self.theme_var.get()))
        view.add_cascade(label="Theme", menu=theme_menu)
        bar.add_cascade(label="View", menu=view)

        help_ = tk.Menu(bar, tearoff=False)
        help_.add_command(label="Keyboard shortcuts", accelerator="F1",
                          command=lambda: dialogs.show_shortcuts(self.win, self.theme))
        help_.add_command(label="About", command=lambda: dialogs.show_about(self.win, self.session))
        bar.add_cascade(label="Help", menu=help_)
        self.win.configure(menu=bar)

    def _path_menu(self, parent: tk.Menu) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        accelerators = {PathStyle.JSONPATH: "Ctrl+Shift+C", PathStyle.PYTHON: "Ctrl+Alt+C"}
        for style in PathStyle:
            menu.add_command(label=style.title, accelerator=accelerators.get(style, ""),
                             command=lambda s=style: self.copy_path(s))
        return menu

    def _icon_button(self, parent: tk.Misc, icon: str, tip: str, command: Callable) -> ttk.Button:
        glyph, fallback = ICONS[icon]
        has_icons = self.theme.fonts.icon is not None
        button = ttk.Button(parent, text=glyph if has_icons else fallback,
                            style="Icon.Toolbutton" if has_icons else "Toolbutton", command=command)
        Tooltip(button, tip, self.theme)
        return button

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.win, padding=(8, 6, 8, 6))
        bar.grid(row=0, column=0, sticky="ew")
        self._icon_button(bar, "open", "Open a file (Ctrl+O)", self.open_dialog).pack(side="left")
        self._icon_button(bar, "paste", "Open JSON from the clipboard (Ctrl+Shift+V)", self.open_clipboard).pack(side="left")
        self._icon_button(bar, "reload", "Reload the file (F5)", self.reload).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=4)
        ttk.Button(bar, text="Expand all", style="Toolbutton", command=self.expand_all).pack(side="left")
        ttk.Button(bar, text="Collapse all", style="Toolbutton", command=self.collapse_all).pack(side="left")

        self.theme_button = self._icon_button(bar, "moon", "Switch between light and dark (Ctrl+T)", self._toggle_theme)
        self.theme_button.pack(side="right")
        ttk.Separator(bar, orient="vertical").pack(side="right", fill="y", padx=8, pady=4)

        search = ttk.Frame(bar)
        search.pack(side="right")
        if self.theme.fonts.icon is not None:
            ttk.Label(search, text=ICONS["search"][0], font=self.theme.fonts.icon, style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.search_entry = PlaceholderEntry(search, "Search keys and values", width=30)
        self.search_entry.pack(side="left")
        for text, var, tip in (("Aa", self.case_var, "Match case"), (".*", self.regex_var, "Regular expression")):
            toggle = ttk.Checkbutton(search, text=text, variable=var, style="Toggle.TButton",
                                     command=self.start_search, width=3)
            toggle.pack(side="left", padx=(6, 0))
            Tooltip(toggle, tip, self.theme)
        self.count_label = ttk.Label(search, style="Muted.TLabel", width=13, anchor="center")
        self.count_label.pack(side="left", padx=(6, 0))
        self._icon_button(search, "prev", "Previous match (Shift+F3)", lambda: self.find_step(-1)).pack(side="left")
        self._icon_button(search, "next", "Next match (F3)", lambda: self.find_step(1)).pack(side="left")

        entry = self.search_entry
        entry.bind("<KeyRelease>", self._on_search_key)
        entry.bind("<Return>", lambda e: self.find_step(1) or "break")
        entry.bind("<Shift-Return>", lambda e: self.find_step(-1) or "break")
        entry.bind("<Escape>", lambda e: self.clear_search() or "break")

    def _build_body(self) -> None:
        self.body = ttk.Frame(self.win)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.rowconfigure(1, weight=1)
        self.body.columnconfigure(0, weight=1)

        # Shown above the document when a reload or paste fails, so the document stays put.
        # Plain tk widgets: ttk labels repaint their background with the theme default.
        self.banner = tk.Frame(self.body, padx=12, pady=7, borderwidth=0, highlightthickness=0)
        self.banner_label = tk.Label(self.banner, anchor="w", justify="left", font=self.theme.fonts.ui, borderwidth=0)
        self.banner_label.pack(side="left", fill="x", expand=True)
        ttk.Button(self.banner, text="Show details", style="Banner.TButton",
                   command=self._banner_details).pack(side="right", padx=(12, 0))
        self.banner.bind("<Configure>", lambda e: self.banner_label.configure(wraplength=max(200, e.width - 160)))
        self.banner.grid(row=0, column=0, sticky="ew")
        self.banner.grid_remove()

        self.panes = ttk.Panedwindow(self.body, orient="horizontal")
        self.tree = JsonTree(self.panes, on_select=self._on_select)
        self.detail = DetailPane(self.panes, self.theme, on_open_url=webbrowser.open,
                                 on_open_embedded=self._open_embedded)
        self.panes.add(self.tree, weight=3)
        if self.details_var.get():
            self.panes.add(self.detail, weight=2)
        self.placeholder = ttk.Frame(self.body)
        self.panes.grid(row=1, column=0, sticky="nsew")
        self.placeholder.grid(row=1, column=0, sticky="nsew")

        tv = self.tree.tv
        tv.bind("<Button-3>", self._on_right_click)
        tv.bind("<Shift-F10>", self._on_menu_key)
        for keysym in ("App", "Menu"):  # the context-menu key: "App" on Windows, "Menu" on X11
            try:
                tv.bind(f"<{keysym}>", self._on_menu_key)
            except tk.TclError:
                pass
        tv.bind("<Double-1>", self._on_double_click, add="+")
        tv.bind("<Return>", self._on_return)
        tv.bind("<asterisk>", lambda e: self.expand_branch() or "break")
        tv.bind("<KP_Multiply>", lambda e: self.expand_branch() or "break")
        _bind_letter(tv, "Control", "c", lambda e: self.copy_value() or "break")
        _bind_letter(tv, "Control-Shift", "c", lambda e: self.copy_path(PathStyle.JSONPATH) or "break")
        _bind_letter(tv, "Control-Alt", "c", lambda e: self.copy_path(PathStyle.PYTHON) or "break")

    def _build_status(self) -> None:
        bar = ttk.Frame(self.win, padding=(12, 4, 12, 5))
        bar.grid(row=3, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.status_left = ttk.Label(bar, style="Caption.TLabel", anchor="w")
        self.status_left.grid(row=0, column=0, sticky="ew")
        self.status_right = ttk.Frame(bar)
        self.status_right.grid(row=0, column=1, sticky="e")

    def _set_status(self, source: str = "", parts: list[str | None] | None = None) -> None:
        self._status_text = source
        if not self._flash_job:
            self.status_left.configure(text=source)
        for child in self.status_right.winfo_children():
            child.destroy()
        for i, part in enumerate(p for p in (parts or []) if p):
            if i:
                ttk.Separator(self.status_right, orient="vertical").pack(side="left", fill="y", padx=10, pady=2)
            ttk.Label(self.status_right, text=part, style="Caption.TLabel").pack(side="left")

    def flash(self, message: str) -> None:
        """Briefly show feedback (like "Copied value") in the status bar."""
        if self._flash_job:
            self._cancel(self._flash_job)
        self.status_left.configure(text=message)

        def restore() -> None:
            self._flash_job = None
            if self.alive:
                self.status_left.configure(text=self._status_text)

        self._flash_job = self._after(2200, restore)

    def _bind_keys(self) -> None:
        w = self.win
        _bind_letter(w, "Control", "o", lambda e: self.open_dialog())
        _bind_letter(w, "Control-Shift", "v", lambda e: self.open_clipboard())
        _bind_letter(w, "Control", "v", self._on_plain_paste)
        _bind_letter(w, "Control", "f", lambda e: self.focus_search())
        _bind_letter(w, "Control", "e", lambda e: self.expand_all())
        _bind_letter(w, "Control", "l", lambda e: self.collapse_all())
        _bind_letter(w, "Control", "d", lambda e: self._toggle_details())
        _bind_letter(w, "Control", "t", lambda e: self._toggle_theme())
        _bind_letter(w, "Control", "n", lambda e: self.session.new_window())
        _bind_letter(w, "Control", "w", lambda e: self.close())
        w.bind("<F5>", lambda e: self.reload())
        w.bind("<F3>", lambda e: self.find_step(1))
        w.bind("<Shift-F3>", lambda e: self.find_step(-1))
        w.bind("<F1>", lambda e: dialogs.show_shortcuts(self.win, self.theme))

    def _setup_drop(self) -> None:
        if not self.session.dnd:
            return
        for widget in (self.win, self.tree.tv, self.placeholder, self.detail.text, self.body):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            except (AttributeError, tk.TclError):
                pass

    # ======================================================================
    # Theme
    # ======================================================================

    def _apply_palette(self, p: Palette) -> None:
        if not self.alive:
            return
        self.win.configure(background=p.window)
        self.banner.configure(background=p.match)
        self.banner_label.configure(background=p.match, foreground=p.text)
        self.tree.apply_palette(p)
        self.detail.apply_palette(p)
        glyph, fallback = ICONS["sun" if p.dark else "moon"]
        self.theme_button.configure(text=glyph if self.theme.fonts.icon else fallback)
        self.theme_var.set(self.theme.mode)
        self._update_count()
        self.theme.style_window(self.win)

    def _toggle_theme(self) -> None:
        self.theme.toggle()

    # ======================================================================
    # Opening documents
    # ======================================================================

    def open_dialog(self) -> None:
        initial = self.settings.last_dir
        if self.doc and self.doc.file:
            initial = str(self.doc.file.parent)
        path = filedialog.askopenfilename(parent=self.win, title="Open a JSON file",
                                          initialdir=initial or None, filetypes=FILETYPES)
        if path:
            self.open_path(path)

    def open_path(self, path: str | os.PathLike, state: TreeState | None = None, *, reloading: bool = False) -> None:
        file = FilePath(path)
        self._busy(f"Opening {file.name}…")
        try:
            doc = load_file(file)
        except LoadError as err:
            exists = file.is_file()
            if exists:
                self.settings.add_recent(file.absolute())
            else:
                self.settings.remove_recent(str(path))
            if reloading and self.doc is not None and exists:
                # Keep showing the last good version (handy while the file is mid-edit);
                # the next change to the file triggers another try.
                self._watch(file)
                where = f" at {err.location}" if err.location else ""
                self._show_banner(
                    f"Couldn't reload {file.name}: {err.message.rstrip('.')}{where}. "
                    f"Showing the version opened at {self._loaded_at}.", err, file)
                return
            self.show_error(err, file=file if exists else None)
            return
        finally:
            self._busy(None)
        self.settings.add_recent(doc.file)
        self.settings.last_dir = str(doc.file.parent)
        self.show_document(doc, state)

    def open_clipboard(self) -> None:
        try:
            text = self.win.clipboard_get()
        except tk.TclError:
            self.flash("The clipboard doesn't contain any text.")
            return
        try:
            doc = load_text(text, source="Clipboard")
        except LoadError as err:
            if self.doc is not None:  # don't throw away the open document over a stray Ctrl+V
                where = f" at {err.location}" if err.location else ""
                self._show_banner(
                    f"The clipboard text isn't valid JSON: {err.message.rstrip('.')}{where}. "
                    "The open document wasn't replaced.", err, None)
            else:
                self.show_error(err)
            return
        self.show_document(doc)

    def _on_plain_paste(self, event) -> None:
        """Ctrl+V opens the clipboard, unless you're typing in a text field."""
        if not isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
            self.open_clipboard()

    def _on_drop(self, event) -> str:
        paths = self.win.tk.splitlist(event.data)
        if paths:
            self.open_path(paths[0])
            for extra in paths[1:5]:  # open a few more in their own windows
                window = self.session.new_window()
                window.open_path(extra)
        return event.action

    def reload(self) -> None:
        if self.doc is not None and self.doc.file is not None:
            self.open_path(self.doc.file, state=self.tree.capture_state(), reloading=True)
        elif self._error is not None and self._watch_path is not None:
            self.open_path(self._watch_path)

    def _open_embedded(self, data: Any, entry: Entry) -> None:
        origin = self.doc.title if self.doc else "document"
        doc = Document(data=data, source=f"{to_jsonpath(entry.path)} in {origin}", format="Embedded JSON")
        self.session.new_window(doc)

    def show_document(self, doc: Document, state: TreeState | None = None) -> None:
        self.doc = doc
        self._error = None
        self._loaded_at = time.strftime("%H:%M:%S")
        self._hide_banner()
        self.tree.load(doc.data)
        if state is not None:
            self.tree.restore_state(state)
        self._show_body(self.panes)
        self.win.title(f"{doc.title} - {APP_NAME}")
        self.detail.show(self.tree.selected_row(), doc.data)
        self._watch(doc.file)
        self._status_parts = [
            doc.format,
            doc.encoding,
            _human_size(doc.size_bytes),
            "Counting values…",
            f"Opened in {doc.load_seconds:.2f} s" if doc.file else None,
        ]
        self._set_status(doc.source, self._status_parts)
        self._count_values()
        if self.search_entry.value:
            self.start_search(jump=False)
        else:
            self._reset_search()
        if self.win.focus_get() is not self.search_entry:
            self.tree.tv.focus_set()

    def _count_values(self) -> None:
        self._count_token += 1
        token, counter = self._count_token, iter_node_count(self.doc.data, step=20_000)

        def step() -> None:
            if token != self._count_token or not self.alive:
                return
            deadline = time.perf_counter() + WORK_SLICE_S
            total = 0
            for total in counter:
                if time.perf_counter() > deadline:
                    self._after(1, step)
                    return
            self._status_parts[3] = fmt.plural(total, "value")
            self._set_status(self._status_text, self._status_parts)

        step()

    def show_error(self, err: LoadError, file: FilePath | None = None) -> None:
        self.doc = None
        self._error = err
        self._hide_banner()
        self._count_token += 1
        self.tree.clear()
        self.detail.show(None)
        self._reset_search()
        self._watch(file)
        name = FilePath(err.source).name or err.source
        headline = "The clipboard text isn't valid JSON" if err.source == "Clipboard" else f"{name} couldn't be read"
        self.win.title(f"{name} (not opened) - {APP_NAME}")
        self._set_status(err.source, [])

        inner = self._fresh_placeholder()
        ttk.Label(inner, text=headline, style="Subtitle.TLabel").pack(anchor="w")
        message = err.message.rstrip(".") + (f" at {err.location}." if err.location else ".")
        scale = self.win.winfo_fpixels("1i") / 96.0
        ttk.Label(inner, text=message, wraplength=round(620 * scale)).pack(anchor="w", pady=(6, 0))
        snippet = err.snippet()
        if snippet:
            text, caret = snippet
            code = ttk.Frame(inner, padding=(14, 10), style="Card.TFrame")
            code.pack(anchor="w", fill="x", pady=(14, 0))
            ttk.Label(code, text=f"{err.line:>6}  {text}", style="Code.TLabel").pack(anchor="w")
            ttk.Label(code, text=" " * (8 + caret) + "^", style="CodeCaret.TLabel").pack(anchor="w")
        if file is not None:
            hint = ("Fix the file and save it. It will reload automatically."
                    if self.settings.auto_reload else "Fix the file and save it, then press F5 to reload.")
            ttk.Label(inner, text=hint, style="Muted.TLabel").pack(anchor="w", pady=(14, 0))
        buttons = ttk.Frame(inner)
        buttons.pack(anchor="w", pady=(16, 0))
        if file is not None:
            ttk.Button(buttons, text="Reload", style="Accent.TButton", command=self.reload).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Open another file…", command=self.open_dialog).pack(side="left")

    def show_welcome(self) -> None:
        self._hide_banner()
        inner = self._fresh_placeholder()
        ttk.Label(inner, text="{ }", style="Display.TLabel").pack(anchor="w")
        ttk.Label(inner, text="Open a JSON file", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 4))
        how = ("Drop a file anywhere in this window, paste JSON with Ctrl+V, or choose a file."
               if self.session.dnd else "Paste JSON with Ctrl+V, or choose a file.")
        ttk.Label(inner, text=how, style="Muted.TLabel").pack(anchor="w")
        buttons = ttk.Frame(inner)
        buttons.pack(anchor="w", pady=(16, 0))
        ttk.Button(buttons, text="Open file…", style="Accent.TButton", command=self.open_dialog).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Paste from clipboard", command=self.open_clipboard).pack(side="left")

        recent = [p for p in self.settings.recent if p][:6]
        if recent:
            ttk.Label(inner, text="Recent files", style="Strong.TLabel").pack(anchor="w", pady=(28, 6))
            for path in recent:
                row = ttk.Frame(inner)
                row.pack(anchor="w", fill="x", pady=1)
                file = FilePath(path)
                link = ttk.Label(row, text=file.name, style="Link.TLabel", cursor="hand2")
                link.pack(side="left")
                ttk.Label(row, text=str(file.parent), style="Caption.TLabel").pack(side="left", padx=(10, 0))
                link.bind("<Button-1>", lambda e, p=path: self.open_path(p))
        self.win.title(APP_NAME)

    def _fresh_placeholder(self) -> ttk.Frame:
        for child in self.placeholder.winfo_children():
            child.destroy()
        self._show_body(self.placeholder)
        inner = ttk.Frame(self.placeholder, padding=24)
        inner.place(relx=0.5, rely=0.42, anchor="center")
        return inner

    def _show_banner(self, message: str, err: LoadError, file: FilePath | None) -> None:
        self._banner_error = (err, file)
        self.banner_label.configure(text=message)
        self.banner.grid()

    def _hide_banner(self) -> None:
        self._banner_error = None
        self.banner.grid_remove()

    def _banner_details(self) -> None:
        if self._banner_error is not None:
            err, file = self._banner_error
            self.show_error(err, file=file)

    def _show_body(self, widget: tk.Widget) -> None:
        """Both views share one grid cell; the visible one is simply raised above the other."""
        widget.tkraise()

    def _busy(self, message: str | None) -> None:
        if message:
            self.status_left.configure(text=message)
            self.win.configure(cursor="watch")
            self.win.update_idletasks()
        else:
            self.win.configure(cursor="")
            self.status_left.configure(text=self._status_text)

    def _fill_recent_menu(self) -> None:
        menu = self.recent_menu
        menu.delete(0, "end")
        if not self.settings.recent:
            menu.add_command(label="No recent files", state="disabled")
            return
        for path in self.settings.recent:
            menu.add_command(label=path, command=lambda p=path: self.open_path(p))
        menu.add_separator()
        menu.add_command(label="Clear list", command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.settings.clear_recent()
        if self.doc is None and self._error is None:
            self.show_welcome()

    # ======================================================================
    # Auto-reload
    # ======================================================================

    @staticmethod
    def _signature(path: FilePath) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _watch(self, path: FilePath | None) -> None:
        self._watch_path = path
        self._watch_sig = self._signature(path) if path else None
        self._pending_sig = None

    def _poll_file(self) -> None:
        if not self.alive:
            return
        if self.settings.auto_reload and self._watch_path is not None:
            sig = self._signature(self._watch_path)
            if sig != self._watch_sig and sig is not None:
                if sig == self._pending_sig:  # unchanged for one interval: the save has finished
                    self.reload()
                else:
                    self._pending_sig = sig
        self._after(700, self._poll_file)

    def _toggle_auto_reload(self) -> None:
        self.settings.auto_reload = self.auto_reload_var.get()
        self.flash("Reloading when the file changes" if self.settings.auto_reload else "Automatic reload is off")

    # ======================================================================
    # Selection, menus, activation
    # ======================================================================

    def _on_select(self, _row: Row | None) -> None:
        # Rendering a big value takes a moment, so wait until the selection stops changing.
        if self._detail_job:
            self._cancel(self._detail_job)
        self._detail_job = self._after(DETAIL_DELAY_MS, self._render_detail)

    def _render_detail(self) -> None:
        self._detail_job = None
        if self.alive:
            self.detail.show(self.tree.selected_row(), self.doc.data if self.doc else None)

    def _selected_entry(self) -> Entry | None:
        row = self.tree.selected_row()
        return row if isinstance(row, Entry) else None

    def _on_right_click(self, event) -> None:
        iid = self.tree.tv.identify_row(event.y)
        if iid:
            self.tree.tv.selection_set(iid)
            self.tree.tv.focus(iid)
            self._popup(event.x_root, event.y_root)

    def _on_menu_key(self, _event=None) -> str:
        iid = self.tree.selected_iid()
        if iid:
            bbox = self.tree.tv.bbox(iid)
            if bbox:
                x, y, _w, h = bbox
                self._popup(self.tree.tv.winfo_rootx() + x + 24, self.tree.tv.winfo_rooty() + y + h)
        return "break"

    def _popup(self, x: int, y: int) -> None:
        iid = self.tree.selected_iid()
        row = self.tree.row(iid) if iid else None
        if row is None:
            return
        if self._menu is not None:  # replace, rather than pile up, menu widgets
            self._menu.destroy()
        self._menu = menu = tk.Menu(self.win, tearoff=False)
        if isinstance(row, Entry):
            menu.add_command(label="Copy value", accelerator="Ctrl+C", command=self.copy_value)
            if row.key is not None:
                menu.add_command(label="Copy key", command=self.copy_key)
        menu.add_cascade(label="Copy path", menu=self._path_menu(menu))
        if isinstance(row, Entry) and row.kind is Kind.STRING:
            if fmt.is_url(row.value):
                menu.add_separator()
                menu.add_command(label="Open link", command=lambda: webbrowser.open(row.value))
            embedded = fmt.parse_embedded_json(row.value)
            if embedded is not None:
                menu.add_separator()
                menu.add_command(label="Open as JSON in a new window",
                                 command=lambda: self._open_embedded(embedded, row))
        if self.tree.has_children(iid):
            menu.add_separator()
            menu.add_command(label="Expand branch", accelerator="*", command=self.expand_branch)
            menu.add_command(label="Collapse branch", command=self.collapse_branch)
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _on_double_click(self, event) -> str | None:
        row = self.tree.row(self.tree.tv.identify_row(event.y))
        if isinstance(row, Entry) and row.kind is Kind.STRING and fmt.is_url(row.value):
            webbrowser.open(row.value)
            return "break"
        return None

    def _on_return(self, _event=None) -> str:
        iid = self.tree.selected_iid()
        row = self.tree.row(iid) if iid else None
        if isinstance(row, Entry) and row.kind is Kind.STRING and fmt.is_url(row.value):
            webbrowser.open(row.value)
        elif iid and self.tree.has_children(iid):
            self.tree.toggle(iid)
        return "break"

    # ======================================================================
    # Copy
    # ======================================================================

    def _to_clipboard(self, text: str, message: str) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.flash(message)

    def _copy_formatted(self, value: Any, kind: Kind, message: str) -> None:
        try:
            if kind in (Kind.OBJECT, Kind.ARRAY):
                text = self._with_busy_cursor(lambda: fmt.copy_text(value, kind))
            else:
                text = fmt.copy_text(value, kind)
        except RecursionError:
            self.flash("That value is nested too deeply to copy as text.")
            return
        self._to_clipboard(text, message)

    def copy_value(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            self._copy_formatted(entry.value, entry.kind, "Copied value")

    def copy_key(self) -> None:
        entry = self._selected_entry()
        if entry is not None and entry.key is not None:
            self._to_clipboard(str(entry.key), "Copied key")

    def copy_path(self, style: PathStyle) -> None:
        row = self.tree.selected_row()
        if row is not None:
            self._to_clipboard(format_path(row.path, style), f"Copied {style.title} path")

    def copy_document(self) -> None:
        if self.doc is not None:
            self._copy_formatted(self.doc.data, kind_of(self.doc.data), "Copied the whole document")

    # ======================================================================
    # Expand / collapse
    # ======================================================================

    def _confirm_big_expand(self, value: Any) -> bool:
        n = count_nodes(value, limit=EXPAND_CONFIRM_NODES)
        if n <= EXPAND_CONFIRM_NODES:
            return True
        return messagebox.askyesno(
            APP_NAME,
            f"This expands more than {EXPAND_CONFIRM_NODES:,} rows and may take a while.\n\nExpand anyway?",
            parent=self.win,
        )

    def _with_busy_cursor(self, action: Callable[[], Any]) -> Any:
        self.win.configure(cursor="watch")
        self.win.update_idletasks()
        try:
            return action()
        finally:
            self.win.configure(cursor="")

    def expand_all(self) -> None:
        if self.doc is not None and self._confirm_big_expand(self.doc.data):
            self._with_busy_cursor(lambda: self.tree.expand(""))

    def collapse_all(self) -> None:
        if self.doc is not None:
            self.tree.collapse("")

    def expand_branch(self) -> None:
        iid = self.tree.selected_iid()
        row = self.tree.row(iid) if iid else None
        if row is None or self.doc is None:
            return
        if isinstance(row, Bucket):
            container = resolve(self.doc.data, row.path)
            items = islice(container.values() if isinstance(container, Mapping) else container, row.start, row.stop)
            value: Any = list(items)
        else:
            value = row.value
        if self._confirm_big_expand(value):
            self._with_busy_cursor(lambda: self.tree.expand(iid))

    def collapse_branch(self) -> None:
        iid = self.tree.selected_iid()
        if iid:
            self.tree.collapse(iid)

    # ======================================================================
    # Search
    # ======================================================================

    def focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def _on_search_key(self, event) -> None:
        if self.search_entry.value == self._searched_text:
            return  # navigation keys, F3, modifiers: the text didn't change
        if self._search_after:
            self._cancel(self._search_after)
        self._search_after = self._after(SEARCH_DELAY_MS, self.start_search)

    def _reset_search(self) -> None:
        self._search_token += 1
        self._matches = []
        self._match_index = -1
        self._search_done = True
        self._search_error = False
        self._search_truncated = False
        self.tree.set_matches(())
        self._update_count()

    def start_search(self, jump: bool = True) -> None:
        if self._search_after:
            self._cancel(self._search_after)
            self._search_after = None
        if not self.alive:
            return
        self._reset_search()
        text = self._searched_text = self.search_entry.value
        if not text or self.doc is None:
            return
        query = Query(text, self.case_var.get(), self.regex_var.get(), self.keys_var.get(), self.values_var.get())
        try:
            query.compile()
        except re.error:
            self._search_error = True
            self._update_count()
            return
        self._search_done = False
        self._search_step(self._search_token, iter_matches(self.doc.data, query), jump)

    def _search_step(self, token: int, matches, jump: bool) -> None:
        """Search in short slices so typing and scrolling stay smooth on huge documents."""
        if token != self._search_token or not self.alive:
            return
        deadline = time.perf_counter() + WORK_SLICE_S
        for path in matches:
            if len(self._matches) >= MAX_MATCHES:
                self._search_truncated = True
                break
            self._matches.append(path)
            if jump and len(self._matches) == 1:
                self._goto_match(0)
            if time.perf_counter() > deadline:
                self._update_count()
                self._after(1, self._search_step, token, matches, jump)
                return
        self._search_done = True
        self.tree.set_matches(self._matches)
        self._update_count()

    def find_step(self, step: int) -> None:
        if self._search_after:  # typed but the debounce hasn't fired yet
            self.start_search(jump=True)
            return
        if not self._matches:
            if self.search_entry.value and self._search_done and not self._search_error:
                self.start_search(jump=True)
            return
        if self._match_index < 0:
            self._goto_match(0 if step > 0 else len(self._matches) - 1)
        else:
            self._goto_match(self._match_index + step)

    def _goto_match(self, index: int) -> None:
        self._match_index = index % len(self._matches)
        iid = self.tree.reveal(self._matches[self._match_index])
        if iid:
            self.tree.select(iid)
        self._update_count()

    def clear_search(self) -> None:
        self.search_entry.set_value("")
        self._searched_text = ""
        self._reset_search()
        self.tree.tv.focus_set()

    def _update_count(self) -> None:
        label = self.count_label
        if self._search_error:
            label.configure(text="Invalid pattern", style="Error.TLabel")
            return
        label.configure(style="Muted.TLabel")
        n = len(self._matches)
        more = "+" if self._search_truncated else ""
        if not self.search_entry.value or self.doc is None:
            text = ""
        elif not self._search_done:
            text = f"{n:,}{more} found…"
        elif n == 0:
            text = "No matches"
        elif self._match_index >= 0:
            text = f"{self._match_index + 1:,} of {n:,}{more}"
        else:
            text = fmt.plural(n, "match", "matches") + more
        label.configure(text=text)

    # ======================================================================
    # Details pane and window lifecycle
    # ======================================================================

    def _details_shown(self) -> bool:
        return str(self.detail) in {str(p) for p in self.panes.panes()}

    def _toggle_details(self) -> None:
        self.details_var.set(not self.details_var.get())
        self._apply_details_visibility()

    def _apply_details_visibility(self) -> None:
        shown = self._details_shown()
        if self.details_var.get() and not shown:
            self.panes.add(self.detail, weight=2)
        elif not self.details_var.get() and shown:
            self.panes.forget(self.detail)
        self.settings.show_details = self.details_var.get()

    def restore_layout(self) -> None:
        """Main window only: size, position and the details pane width from last time."""
        s, w = self.settings, self.win
        scale = w.winfo_fpixels("1i") / 96.0
        geometry = s.geometry
        # Tk writes positions left of / above the primary monitor as "+-1500", and a
        # minimized window as "+-32000+-32000"; the on-screen check rejects the latter.
        m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geometry or "")
        if m and winapi.point_on_screen(w, int(m.group(3)) + 40, int(m.group(4)) + 10):
            w.geometry(geometry)
        else:
            width, height = round(1180 * scale), round(760 * scale)
            x = max(0, (w.winfo_screenwidth() - width) // 2)
            y = max(0, (w.winfo_screenheight() - height) // 3)
            w.geometry(f"{width}x{height}+{x}+{y}")
        if s.maximized:
            try:
                w.state("zoomed")
            except tk.TclError:
                pass

        attempts = 0

        def place_sash() -> None:
            nonlocal attempts
            if not self.alive:
                return
            total = self.panes.winfo_width()
            if total <= 1:  # not laid out yet (or started minimized): try again, but not forever
                attempts += 1
                if attempts < 50:
                    self._after(60, place_sash)
                return
            if self._details_shown():
                width = s.details_width or round(total * 0.38)
                self.panes.sashpos(0, max(round(240 * scale), total - width))

        self._after(80, place_sash)

    def save_layout(self) -> None:
        if not self.is_main or not self.alive:
            return
        s, w = self.settings, self.win
        try:
            state = w.state()
            if state in ("iconic", "withdrawn"):  # minimized: size and position aren't meaningful
                return
            s.maximized = state == "zoomed"
            if not s.maximized:
                s.geometry = w.geometry()
            if self._details_shown():
                total = self.panes.winfo_width()
                width = total - self.panes.sashpos(0)
                if 0 < width < total:
                    s.details_width = width
        except tk.TclError:
            pass

    def shutdown(self) -> None:
        """Stop this window's timers and detach it from the session."""
        if not self.alive:
            return
        self.alive = False
        self._search_token += 1
        self._count_token += 1
        for job in (self._flash_job, self._search_after, self._detail_job):
            if job:
                self._cancel(job)
        self._flash_job = self._search_after = self._detail_job = None
        if self._apply_palette in self.theme.listeners:
            self.theme.listeners.remove(self._apply_palette)
        if self in self.session.windows:
            self.session.windows.remove(self)

    def close(self) -> None:
        """Close this window. The app exits when the last window closes."""
        if not any(w is not self for w in self.session.windows):
            self.session.quit()
        elif self.is_main:
            # The main window is the Tk root and can't be destroyed without closing
            # everything, so it's hidden while other windows stay open.
            self.save_layout()
            self.shutdown()
            self.win.withdraw()
        else:
            self.shutdown()
            self.win.destroy()


# ==========================================================================
# Entry point used by the CLI and by jsonview.view()
# ==========================================================================

def create_root() -> tuple[tk.Tk, bool]:
    """Create the Tk root, with drag-and-drop support when tkinterdnd2 can load."""
    winapi.enable_dpi_awareness()
    winapi.set_app_id()
    if TkinterDnD is not None and not os.environ.get("JSONVIEW_NO_DND"):
        before = getattr(tk, "_default_root", None)
        try:
            return TkinterDnD.Tk(), True
        except Exception:  # tkdnd failed to load: carry on without drag and drop
            # TkinterDnD.Tk() creates the window before loading tkdnd; don't leave it behind.
            stray = getattr(tk, "_default_root", None)
            if stray is not None and stray is not before:
                try:
                    stray.destroy()
                except tk.TclError:
                    pass
    return tk.Tk(), False


def run(source: Document | LoadError | str | os.PathLike | None = None, *, theme: str | None = None) -> None:
    settings = Settings.load()
    root, dnd = create_root()
    root.withdraw()  # stay hidden until themed and sized, so there's no flash of an unstyled window
    session = Session(root, settings, dnd, theme=theme)
    window = ViewerWindow(root, session, is_main=True)
    window.restore_layout()
    root.deiconify()
    root.update_idletasks()
    session.theme.style_window(root)
    if isinstance(source, Document):
        window.show_document(source)
    elif isinstance(source, LoadError):
        window.show_error(source)
    elif source is not None:
        window.open_path(source)
    root.mainloop()
