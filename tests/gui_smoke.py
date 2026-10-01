"""End-to-end smoke test of the real window. Run it with:  python tests/gui_smoke.py

It opens windows on screen for a few seconds and drives every feature
programmatically. Settings go to a temporary folder, never your real ones.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

TMP = pathlib.Path(tempfile.mkdtemp(prefix="jsonview-smoke-"))
os.environ["JSONVIEW_NO_DND"] = "1"
os.environ["APPDATA"] = str(TMP / "appdata")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)

from jsonview.core.model import Entry  # noqa: E402
from jsonview.core.paths import PathStyle  # noqa: E402
from jsonview.settings import Settings  # noqa: E402
from jsonview.ui import app as A  # noqa: E402

SHOTS = os.environ.get("JSONVIEW_SHOTS")  # folder for screenshots (Linux/X11 with ImageMagick only)
root, dnd = A.create_root()
session = A.Session(root, Settings.load(), dnd)
w = A.ViewerWindow(root, session, is_main=True)
w.restore_layout(); root.deiconify()
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)
def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.005)
def shot(name):
    pump(0.4)
    if SHOTS and shutil.which("import"):
        subprocess.run(["import", "-window", "root", f"{SHOTS}/{name}.png"], check=True)

pump(0.6); shot("1_welcome_light")

w.open_path("samples/ahu_points.json"); pump()
tv = w.tree.tv
top = [tv.item(i, "text") for i in tv.get_children("")]
check(top[:5] == ["site", "building_id", "commissioned", "last_sync", "api"], f"top-level keys {top[:6]}")
vals = {tv.item(i, "text"): tv.item(i, "values") for i in tv.get_children("")}
check(vals["commissioned"][0] == "true" and vals["last_sync"][0] == "null", "true/null shown as JSON literals")
check(vals["equipment"][0] == "[ 2 items ]", f"array summary {vals['equipment']}")

# tricky strings survive the trip through Tcl
iid = w.tree.reveal(("tricky strings", "tcl")); w.tree.select(iid); pump()
tricky = {tv.item(c, "text"): tv.item(c, "values")[0] for c in tv.get_children(tv.parent(iid))}
check(tricky["braces"] == '"{ not balanced"', f"braces: {tricky['braces']!r}")
check(tricky["close"] == '"}"' and tricky["tcl"] == '"$var [cmd] {x}"', f"tcl specials {tricky['close']!r} {tricky['tcl']!r}")
check(tricky["backslash"] == '"C:\\\\BAS\\\\exports\\\\ahu1.csv"', f"backslashes {tricky['backslash']!r}")
check('""' in tricky and '"multi\\nline key"' in tricky, f"odd keys {list(tricky)}")

# reveal through range buckets
path = ("equipment", 0, "trend_sat", 1234, "sat")
iid = w.tree.reveal(path); w.tree.select(iid); pump()
row = w.tree.selected_row()
check(isinstance(row, Entry) and row.path == path, "reveal() through buckets selects the right row")
chain = []; p = tv.parent(iid)
while p: chain.append(tv.item(p, "text")); p = tv.parent(p)
check("[1,200 … 1,299]" in chain, f"bucket chain {chain}")
check(w.detail.path_var.get() == "$.equipment[0].trend_sat[1234].sat", f"detail path {w.detail.path_var.get()}")
shot("2_bucket_reveal_light")

# copy
w.copy_value(); pump(0.1)
check(root.clipboard_get() == json.dumps(row.value), f"copy value -> {root.clipboard_get()!r}")
w.copy_path(PathStyle.PYTHON); pump(0.1)
check(root.clipboard_get() == 'data["equipment"][0]["trend_sat"][1234]["sat"]', "copy Python path")
check("Copied" in w.status_left.cget("text"), "status flash after copy")

# search
w.search_entry.focus_set(); pump(0.1)
w.search_entry.set_value("supply"); w.start_search(); pump(0.5)
check(w._search_done and len(w._matches) >= 3, f"search found {len(w._matches)}: {w._matches[:4]}")
check(w.count_label.cget("text").startswith("1 of"), f"count label {w.count_label.cget('text')!r}")
first = w.tree.selected_row().path
w.find_step(1); pump(0.1)
check(w.tree.selected_row().path == w._matches[1], "F3 moves to the second match")
w.find_step(-1); w.find_step(-1); pump(0.1)
check(w.tree.selected_row().path == w._matches[-1], "Shift+F3 wraps to the last match")
check("match" in tv.item(w.tree.reveal(w._matches[0]), "tags"), "matched rows highlighted")
shot("3_search_light")
w.regex_var.set(True); w.search_entry.set_value("(unclosed"); w.start_search(); pump(0.2)
check(w.count_label.cget("text") == "Invalid pattern", "bad regex reported, no crash")
w.regex_var.set(False); w.search_entry.set_value("NO_SUCH_THING_ANYWHERE"); w.start_search(); pump(0.3)
check(w.count_label.cget("text") == "No matches", "no matches label")
w.search_entry.set_value("55.4"); w.start_search(); pump(0.3)
check(("equipment", 0, "points", 0, "value") in w._matches, "number values are searchable")
w.clear_search(); pump(0.1)

# theme
w.theme.set_mode("dark"); pump(0.3)
check(w.theme.palette.dark and w.detail.text.cget("background") == w.theme.palette.field, "dark theme applied to detail pane")
iid = w.tree.reveal(("notes",)); w.tree.select(iid); pump()
shot("4_dark_string")
iid = w.tree.reveal(("equipment", 0, "points")); w.tree.select(iid); tv.item(iid, open=True); pump()
shot("5_dark_json")

# embedded JSON opens a second window
iid = w.tree.reveal(("raw_payload",)); w.tree.select(iid); pump()
check(w.detail.embedded_button.winfo_ismapped(), "Open as JSON button shown for embedded JSON")
w.detail._open_embedded(); pump(0.4)
check(len(session.windows) == 2, "embedded JSON opened in a new window")
w2 = session.windows[1]
check([w2.tree.tv.item(i, "text") for i in w2.tree.tv.get_children("")] == ["presentValue", "statusFlags", "reliability"], "embedded window shows parsed content")
w2.close(); pump(0.2)
check(len(session.windows) == 1, "second window closed cleanly")

# reload keeps expansion and selection
w.theme.set_mode("light"); pump(0.2)
w.tree.collapse(""); 
for pth in [("equipment",), ("equipment", 1), ("equipment", 1, "points")]:
    i = w.tree.reveal(pth); tv.item(i, open=True)
target = ("equipment", 1, "points", 1, "units")
w.tree.select(w.tree.reveal(target)); pump()
w.reload(); pump(0.3)
check(w.tree.selected_row() is not None and w.tree.selected_row().path == target, "reload restores selection")
check(w.tree.is_open(w.tree.reveal(("equipment", 1))), "reload restores expanded rows")

# expand all / collapse all
t0 = time.perf_counter(); w.expand_all(); pump(0.1)
check(len(w.tree._rows) > 1700, f"expand all created {len(w.tree._rows)} rows in {time.perf_counter()-t0:.2f}s")
w.collapse_all(); pump(0.1)

# clipboard
root.clipboard_clear(); root.clipboard_append('{"pasted": [1, 2, {"ok": true}]}'); w.open_clipboard(); pump()
check(w.win.title().startswith("Clipboard") and w.doc.data["pasted"][2]["ok"] is True, "open from clipboard")
root.clipboard_clear(); root.clipboard_append('{"pasted": [1, 2,'); w.open_clipboard(); pump()
check(w._error is not None and w.doc is None, "invalid clipboard JSON shows the error view")

# broken file
w.open_path("samples/broken.json"); pump()
check(w._error is not None and w._error.line == 5, f"broken.json error at line {w._error and w._error.line}")
shot("6_error_light")
w.theme.set_mode("dark"); pump(0.2); shot("7_error_dark"); w.theme.set_mode("light")

# auto-reload: fixing the file while the error is shown
tmp = TMP / "live.json"; tmp.write_text('{"a": 1,}')
w.settings.auto_reload = True; w.open_path(tmp); pump()
check(w._error is not None, "live file starts broken")
time.sleep(0.05); tmp.write_text('{"a": 1, "b": [true]}'); pump(2.2)
check(w.doc is not None and w.doc.data == {"a": 1, "b": [True]}, "auto-reload picked up the fixed file")

# welcome screen with recent files
w.doc = None; w._error = None; w.tree.clear(); w.show_welcome(); pump()
shot("8_welcome_recent")

# big document: lazy loading + search speed
big = TMP / "big.json"
big.write_text(json.dumps([{"id": i, "name": f"point-{i}", "value": i * 0.5} for i in range(200_000)]))
t0 = time.perf_counter(); w.open_path(big); load_t = time.perf_counter() - t0; pump(0.1)
check(len(w.tree._rows) < 200, f"200k-item array: {len(w.tree._rows)} rows created, opened in {load_t:.2f}s")
t0 = time.perf_counter(); w.search_entry.set_value("point-199999"); w.start_search()
while not w._search_done: root.update()
st = time.perf_counter() - t0; pump(0.1)
check(w.tree.selected_row().path == (199999, "name"), f"found the last item in {st:.2f}s through 3 bucket levels")
w.search_entry.set_value("point-1"); w.start_search()
while not w._search_done: root.update()
check(len(w._matches) == 10_000 and w._search_truncated and w.count_label.cget("text").endswith("+"), f"match cap: {w.count_label.cget('text')!r}")
shot("9_big_light")

# status bar finished counting
pump(1.0)
parts = [c.cget("text") for c in w.status_right.winfo_children() if isinstance(c, A.ttk.Label)]
check(any("values" in p for p in parts), f"status parts {parts}")

session.quit()
saved = json.loads((TMP / "appdata" / "JSONViewer" / "settings.json").read_text())
check(saved["recent"][0].endswith("big.json") and saved["auto_reload"] is True, "settings saved on exit")
print("\nFAILURES:", fails if fails else "none")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fails else 0)
