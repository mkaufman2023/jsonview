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
check(w.doc is not None and w.doc.data["pasted"][2]["ok"] is True and w.banner.winfo_ismapped(),
      "invalid clipboard JSON keeps the open document and shows a banner")

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


# ---------------------------------------------------------------------------
# Review fixes, within the main session
# ---------------------------------------------------------------------------
errors_reported = []
session._report_exception = lambda *exc: errors_reported.append(exc[1])
root.report_callback_exception = session._report_exception

# Real keyboard and mouse expansion (not just reveal()), which relies on Tk focusing the item first
w.open_path("samples/ahu_points.json"); pump()
tv = w.tree.tv
eq = w.tree.reveal(("equipment",))
w.tree.collapse(""); tv.selection_set(eq); tv.focus(eq); tv.focus_force(); pump(0.2)
tv.event_generate("<Right>"); pump(0.2)
check(w.tree.is_open(eq) and len(tv.get_children(eq)) == 2, "Right arrow expands and populates a row")
first = tv.get_children(eq)[0]
tv.see(first); pump(0.1)
_, y, _, h = tv.bbox(first)
arrow_x = next(x for x in range(0, 200, 2) if "indicator" in tv.identify_element(x, y + h // 2))
tv.event_generate("<ButtonPress-1>", x=arrow_x + 2, y=y + h // 2)
tv.event_generate("<ButtonRelease-1>", x=arrow_x + 2, y=y + h // 2); pump(0.2)
check(w.tree.is_open(first) and w.tree.row(tv.get_children(first)[0]) is not None, "clicking the expand arrow populates a row")

# Details pane: updates once the selection settles, not for every row passed
renders = []
real_show = w.detail.show
w.detail.show = lambda *a, **k: (renders.append(1), real_show(*a, **k))
children = tv.get_children(w.tree.reveal(("equipment", 0, "trend_sat", 0)))
for iid in list(children)[:30]:
    tv.selection_set(iid); root.update()
pump(0.3)
w.detail.show = real_show
check(len(renders) <= 3, f"30 quick selection changes rendered the details pane {len(renders)} time(s)")

# Context menus are replaced, not piled up
w.tree.select(w.tree.reveal(("site",))); pump(0.1)
for _ in range(3):
    w._popup(10, 10); w._menu.unpost()
menus = [c for c in w.win.winfo_children() if c.winfo_class() == "Menu" and c is not w.win.nametowidget(w.win["menu"])]
check(len(menus) == 1, f"repeated right-clicks keep one popup menu ({len(menus)})")

# Emoji before a value: highlighting must land on the value (Tk 8.6 counts emoji as two positions)
w.detail._set_text('{\n  "icon": "\U0001F600 fan", "n": 5\n}', mode="json")
t = w.detail.text
check(t.get(*t.tag_ranges("number")[:2]) == "5", "syntax colors line up after an emoji")

# Very long lines stay responsive in the details pane
import base64
blob = base64.b64encode(os.urandom(400_000)).decode()
long_file = TMP / "long.json"; long_file.write_text(json.dumps({"image": blob, "wrapper": {"image": blob}}))
w.open_path(long_file); pump()
for path, label in ((("image",), "long string"), (("wrapper",), "object containing it")):
    w.tree.select(w.tree.reveal(path)); pump(0.3)
    t0 = time.perf_counter()
    for _ in range(5):
        t.yview_scroll(3, "units"); root.update()
    check(time.perf_counter() - t0 < 0.5, f"scrolling the details of the {label} took {time.perf_counter() - t0:.2f}s")
check("Very long lines are shortened" in t.get("1.0", "end"), "shortened lines are explained")

# A failed reload keeps the document and its expanded rows; fixing the file brings it back
live = TMP / "edit.json"; live.write_text(json.dumps({"a": {"b": {"c": 1}}, "d": [1, 2]}))
w.settings.auto_reload = False
w.open_path(live); pump()
w.tree.select(w.tree.reveal(("a", "b", "c"))); pump(0.2)
time.sleep(0.02); live.write_text('{"a": {"b": {"c": 1}}, "d": [1, 2,]}')
w.reload(); pump(0.3)
check(w.doc is not None and w.banner.winfo_ismapped(), "a broken save shows a banner and keeps the document")
check("Couldn't reload edit.json" in w.banner_label.cget("text"), f"banner text: {w.banner_label.cget('text')[:60]!r}")
check(w.tree.selected_row().path == ("a", "b", "c"), "the selection survives the failed reload")
time.sleep(0.02); live.write_text(json.dumps({"a": {"b": {"c": 2}}, "d": [1, 2]}))
w.reload(); pump(0.3)
check(not w.banner.winfo_ismapped() and w.doc.data["a"]["b"]["c"] == 2, "fixing the file reloads and hides the banner")
check(w.tree.selected_row().path == ("a", "b", "c"), "selection restored after the fix")

# Stray Ctrl+V with non-JSON on the clipboard doesn't replace the document
root.clipboard_clear(); root.clipboard_append("not json at all")
w.open_clipboard(); pump(0.2)
check(w.doc is not None and w.doc.file == live and w.banner.winfo_ismapped(), "invalid paste keeps the open document")
w._banner_details(); pump(0.2)
check(w.doc is None and w._error is not None and not w.banner.winfo_ismapped(), "Show details opens the full error view")

# Closing a second window while its timers are pending must not raise errors
w.open_path("samples/ahu_points.json"); pump()
w2 = session.new_window(); w2.open_path("samples/ahu_points.json"); pump()
w2.tree.select(w2.tree.reveal(("site",))); w2.copy_value()          # starts the status flash timer
w2.search_entry.focus_set(); w2.search_entry.set_value("supply")
w2.search_entry.event_generate("<KeyRelease>", keysym="y")           # starts the search debounce timer
w2.close(); pump(2.6)
w3 = session.new_window(); pump(0.2)
w4 = session.new_window(); pump(0.2)
check(w3.win.winfo_rootx() != w4.win.winfo_rootx(), "new windows cascade instead of stacking exactly")
w4.close(); w3.close(); pump(0.2)
check(not errors_reported, f"no errors from timers of closed windows ({errors_reported[:1]})")

session.quit()
saved = json.loads((TMP / "appdata" / "JSONViewer" / "settings.json").read_text())
check(saved["recent"][0].endswith("ahu_points.json") and any(r.endswith("big.json") for r in saved["recent"])
      and saved["auto_reload"] is False, "settings saved on exit")
# ---------------------------------------------------------------------------
# Review fixes that need fresh sessions
# ---------------------------------------------------------------------------
settings_file = TMP / "appdata" / "JSONViewer" / "settings.json"

def new_session(theme=None):
    r, d = A.create_root()
    sess = A.Session(r, Settings.load(), d, theme=theme)
    win = A.ViewerWindow(r, sess, is_main=True)
    win.restore_layout(); r.deiconify(); r.update()
    return r, sess, win

def spin(r, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            r.update()
        except A.tk.TclError:
            return
        time.sleep(0.005)

# Tcl prints leaked-timer errors straight to file descriptor 2, so capture that
stderr_copy = os.dup(2)
captured = TMP / "stderr.txt"
with open(captured, "w") as fh:
    os.dup2(fh.fileno(), 2)
    try:
        # A one-off theme isn't saved; a saved left-monitor position doesn't crash startup
        saved = json.loads(settings_file.read_text())
        saved["theme"], saved["geometry"] = "system", "1180x760+-1500+100"
        settings_file.write_text(json.dumps(saved))
        r, sess, win = new_session(theme="dark")
        check(sess.theme.mode == "dark", "theme override applies to the session")
        spin(r, 0.3); sess.quit()
        check(json.loads(settings_file.read_text())["theme"] == "system", "theme override was not saved")
        # Three more sessions in a row, like repeated view() calls, each idling past the 2 s theme poll
        for _ in range(3):
            r, sess, win = new_session(); spin(r, 2.3); sess.quit()
    finally:
        os.dup2(stderr_copy, 2)
tcl_errors = captured.read_text()
check("bgerror" not in tcl_errors and "invalid command" not in tcl_errors, f"no leaked-timer errors across sessions {tcl_errors[:120]!r}")

# Two instances: the one that closes last keeps the other's recent files
first, second = Settings.load(), Settings.load()
first.add_recent(r"C:\data\one.json"); second.add_recent(r"C:\data\two.json")
first.save(); second.save()
recent = Settings.load().recent
check(r"C:\data\one.json" in recent and r"C:\data\two.json" in recent, "recent files from both instances kept")
second.remove_recent(r"C:\data\one.json"); second.save()
check(r"C:\data\one.json" not in Settings.load().recent, "a removal isn't undone by the merge")
settings_file.write_text('{"recent": "oops", "maximized": "yes", "theme": 3, "details_width": -5}')
bad = Settings.load()
check(bad.recent == [] and bad.maximized is False and bad.theme == "system" and bad.details_width is None,
      "hand-edited settings with wrong types fall back to defaults")

# Closing the main window keeps other windows open; the app ends with the last window
r, sess, win = new_session()
other = sess.new_window(); other.open_path("samples/ahu_points.json"); spin(r, 0.3)
win.close(); spin(r, 0.3)
check(r.state() == "withdrawn" and other.win.winfo_exists() and other.alive, "closing the main window keeps the other open")
other.close(); spin(r, 0.3)
try:
    r.winfo_exists(); ended = False
except A.tk.TclError:
    ended = True
check(ended, "closing the last window exits the app")

print("\nFAILURES:", fails if fails else "none")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fails else 0)
