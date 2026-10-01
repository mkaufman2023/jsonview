# jsonview

A fast, searchable JSON viewer for Windows 11, built on tkinter with the
Windows 11 (Sun Valley) theme. A rewrite of the idea behind `PyJSONViewer`.

## Setup

1. Check that your Python has tkinter. A small test window should open:

   ```bash
   python -c "import tkinter; tkinter._test()"
   ```

   If you get `ModuleNotFoundError: No module named 'tkinter'`, rerun the
   Python installer, choose **Modify**, and check **tcl/tk and IDLE**.

2. Install it in editable mode from this folder, so your code changes apply
   immediately without reinstalling:

   ```bash
   python -m pip install -e .
   ```

   This installs `sv-ttk` (theme) and `tkinterdnd2` (drag and drop), 
   and creates `jsonview.exe` in your Python `Scripts` folder.

3. Run it:

   ```bash
   jsonview                       # empty window
   jsonview points.json           # open a file
   curl.exe -s URL | python -m jsonview -    # read from standard input
   ```

   `jsonview.exe` starts without a console window. Use `python -m jsonview`
   when you want to see error output in the terminal.

To open `.json` files by double-clicking: right-click a `.json` file, 
choose **Open with > Choose another app**, and browse to `jsonview.exe`
(`where jsonview` in a terminal prints its location).

## From Python

```python
import jsonview

jsonview.view(data)                 # any dict or list
jsonview.view("export.json")        # a file path
jsonview.view(requests.get(url))    # anything with a .json() method
jsonview.view('{"a": [1, 2]}')      # JSON text
```

`view()` blocks until the window closes. It works well 
at the end of a script or from the REPL while exploring an API.

## What it does

- Opens files, clipboard text, standard input, and dropped files (drop
  several to get one window each). Handles UTF-8, UTF-8 with BOM, UTF-16
  (what Windows PowerShell 5.1 writes) and Windows-1252, plus JSON Lines.
- Shows `true`, `false` and `null` as JSON, colors values by type, and puts
  each value beside its key.
- Loads lazily: rows exist only once their parent is expanded, and
  containers with more than 1,000 children are split into ranges, so a
  200,000-item array opens instantly. Nothing is ever cut off.
- Searches keys and values across the whole document, including parts you
  haven't expanded. Supports match case and regular expressions, highlights
  matches, shows "3 of 17", and steps through them with F3 / Shift+F3.
- The details pane shows the selected value in full with its JSONPath:
  long strings wrapped, containers pretty-printed and syntax colored. Links
  get an "Open link" button; strings that contain JSON (common in API
  payloads) get "Open as JSON", which opens them in a new window.
- Copy the value, key, or path as JSONPath, Python (`data["a"][0]`) or JSON
  Pointer. Right-click any row for everything available.
- F5 reloads and keeps what you had expanded and selected. **File > Reload
  when the file changes** watches the file, which is handy while you edit it
  or while a script rewrites it.
- Parse errors show the exact line and column with the offending text.
- Follows Windows' light or dark mode (or pick one in **View > Theme**),
  including the title bar. It's DPI-aware, so text stays sharp at 125% or
  150% display scaling instead of being blurrily stretched.
- Remembers window size and position, the details pane width, recent files,
  and preferences in `%APPDATA%\JSONViewer\settings.json`.

Press F1 in the app for all keyboard shortcuts.

## Project layout

```bash
src/jsonview/
  core/            No tkinter here; everything is unit-tested
    loader.py      Reading files and text, encodings, JSON Lines, error context
    model.py       Value kinds, rows, and splitting big containers into ranges
    formatting.py  Previews, descriptions, pretty-printing, clipboard text
    paths.py       JSONPath, Python and JSON Pointer paths
    search.py      Search over the data in document order
  ui/
    app.py         The window: menus, toolbar, search, open/reload, copy
    tree.py        The lazy tree widget
    detail.py      The details pane
    theme.py       sv-ttk setup, colors, fonts, styles
    winapi.py      DPI awareness, dark title bar, taskbar icon
    widgets.py     Tooltip and placeholder entry
    dialogs.py     Shortcuts and About
  settings.py      Saved preferences
  api.py, cli.py   jsonview.view() and the command line
```

New features usually start in `core/` (with a test) 
and then get a menu item or button in `ui/app.py`.

## Tests

```bash
py -3.13 -m pip install -e ".[dev]"
py -3.13 -m pytest                 # core logic, about 0.1 s
py -3.13 tests\gui_smoke.py        # drives the real window for ~10 s
```

The smoke test opens windows on screen, exercises every feature, 
and uses a temporary settings folder so it never touches yours.

## Known limitations

- The menu bar stays light in dark mode. It's a native Windows menu, and
  Windows doesn't offer dark menus to classic apps.
- sv-ttk draws checkboxes and button corners from fixed-size images, so at
  high display scaling those details are a little small. Text scales correctly.
- Parsing happens on the UI thread. An 11 MB file opened in about 0.2 s in
  testing, but a several-hundred-MB file will freeze the window while it parses.
- Like Python's `json` module, duplicate keys keep only the last value.
- `JSONVIEW_NO_DND=1` turns drag and drop off, in case tkinterdnd2 ever
  misbehaves.
