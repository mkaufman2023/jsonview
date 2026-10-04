import codecs
import json

import pytest

from jsonview.core import formatting as fmt
from jsonview.core.loader import LoadError, decode_bytes, load_file, load_text, parse_text
from jsonview.core.model import (
    Bucket, Entry, Kind, bucket_key_ranges, count_nodes, iter_node_count, kind_of,
    path_exists, plan_children, resolve,
)
from jsonview.core.paths import to_jsonpath, to_pointer, to_python
from jsonview.core.search import Query, find_all, iter_matches

DOC = {
    "name": "AHU-1",
    "enabled": True,
    "points": [
        {"id": "SAT", "value": 55.2, "units": "°F"},
        {"id": "RAT", "value": None},
    ],
    "supply temp": {"sp": 55},
    "": "empty key",
}


# --- model -------------------------------------------------------------------

@pytest.mark.parametrize("value, kind", [
    (None, Kind.NULL), (True, Kind.BOOLEAN), (0, Kind.NUMBER), (1.5, Kind.NUMBER),
    ("x", Kind.STRING), ({}, Kind.OBJECT), ([], Kind.ARRAY), ((1,), Kind.ARRAY), ({1}, Kind.OTHER),
])
def test_kind_of(value, kind):
    assert kind_of(value) is kind


def test_resolve_and_path_exists():
    assert resolve(DOC, ("points", 0, "id")) == "SAT"
    assert path_exists(DOC, ("points", 1, "value"))
    assert not path_exists(DOC, ("points", 5))
    assert not path_exists(DOC, ("name", 0))  # strings are not containers
    assert not path_exists(DOC, ("points", "0"))


def test_plan_children_small_container_lists_entries():
    rows = plan_children(DOC["points"], ("points",))
    assert [r.path for r in rows] == [("points", 0), ("points", 1)]
    assert all(isinstance(r, Entry) for r in rows)


def test_plan_children_buckets_cover_everything_without_gaps():
    big = list(range(150_000))
    top = plan_children(big, ())
    assert all(isinstance(r, Bucket) for r in top)
    assert len(top) <= 100
    assert top[0].start == 0 and top[-1].stop == 150_000
    assert all(a.stop == b.start for a, b in zip(top, top[1:]))
    # drill down until we reach entries
    level = plan_children(big, (), top[3].start, top[3].stop)
    assert all(isinstance(r, Bucket) for r in level)
    leaf = plan_children(big, (), level[0].start, level[0].stop)
    assert [e.value for e in leaf] == list(range(level[0].start, level[0].stop))


def test_plan_children_exactly_at_limit_is_direct():
    rows = plan_children(list(range(1000)), ())
    assert len(rows) == 1000 and isinstance(rows[0], Entry)


def test_bucket_labels():
    obj = {f"k{i:05}": i for i in range(2000)}
    rows = plan_children(obj, ())
    assert rows[0].label == "[0 … 99]"
    assert bucket_key_ranges(obj, rows[:2]) == ["k00000 … k00099", "k00100 … k00199"]
    assert bucket_key_ranges(obj, rows[5:7]) == ["k00500 … k00599", "k00600 … k00699"]
    assert bucket_key_ranges(obj, rows[5:7], keys=list(obj)) == ["k00500 … k00599", "k00600 … k00699"]
    assert bucket_key_ranges(list(range(2000)), rows[:1]) == ["100 items"]


def test_count_nodes():
    assert count_nodes(DOC) == 14
    assert count_nodes(DOC, limit=3) == 4
    assert list(iter_node_count(DOC, step=5))[-1] == 14


# --- formatting --------------------------------------------------------------

def test_previews():
    assert fmt.preview(True) == "true"
    assert fmt.preview(None) == "null"
    assert fmt.preview({"a": 1}) == "{ 1 key }"
    assert fmt.preview([1, 2]) == "[ 2 items ]"
    assert fmt.preview({}) == "{ }"
    assert fmt.preview("a\nb") == '"a\\nb"'
    long = fmt.preview("x" * 10_000, max_chars=50)
    assert long.endswith('…"') and len(long) == 53  # quote + 50 chars + ellipsis + quote


def test_key_label():
    assert fmt.key_label("") == '""'
    assert fmt.key_label("a\nb") == '"a\\nb"'
    assert fmt.key_label(3) == "3"
    assert fmt.key_label(None) == "(root)"


def test_copy_text():
    assert fmt.copy_text("raw text") == "raw text"
    assert fmt.copy_text(False) == "false"
    assert json.loads(fmt.copy_text(DOC)) == DOC


def test_to_json_limited():
    text, truncated = fmt.to_json_limited(list(range(100_000)), limit=1000)
    assert truncated and len(text) == 1000
    text, truncated = fmt.to_json_limited(DOC, limit=10_000)
    assert not truncated and json.loads(text) == DOC


def test_is_url_and_embedded_json():
    assert fmt.is_url("https://example.com/a?b=1")
    assert not fmt.is_url("example.com")
    assert not fmt.is_url("https://exa mple.com")
    assert fmt.parse_embedded_json('{"a": [1]}') == {"a": [1]}
    assert fmt.parse_embedded_json('"just a string"') is None
    assert fmt.parse_embedded_json("{nope") is None


# --- paths -------------------------------------------------------------------

def test_paths():
    path = ("points", 0, "supply temp", "it's")
    assert to_jsonpath(path) == "$.points[0]['supply temp']['it\\'s']"
    assert to_python(path) == 'data["points"][0]["supply temp"]["it\'s"]'
    assert to_pointer(("a/b", "c~d", 0)) == "/a~1b/c~0d/0"
    assert to_jsonpath(()) == "$" and to_pointer(()) == "" and to_python(()) == "data"


def test_python_path_round_trips():
    data = DOC
    path = ("points", 0, "units")
    assert eval(to_python(path), {"data": data}) == "°F"


# --- search ------------------------------------------------------------------

def test_search_keys_and_values_in_document_order():
    found, truncated = find_all(DOC, Query("a"))
    assert not truncated
    # keys "name", "enabled", "value" x2; values "SAT", "RAT" ("name" matches once even though its value does too)
    assert found == [
        ("name",), ("enabled",), ("points", 0, "id"), ("points", 0, "value"),
        ("points", 1, "id"), ("points", 1, "value"),
    ]


def test_search_literals_case_and_regex():
    assert find_all(DOC, Query("null"))[0] == [("points", 1, "value")]
    assert find_all(DOC, Query("true", in_keys=False))[0] == [("enabled",)]
    assert find_all(DOC, Query("ahu", case_sensitive=True))[0] == []
    assert find_all(DOC, Query(r"^[SR]AT$", regex=True))[0] == [("points", 0, "id"), ("points", 1, "id")]
    with pytest.raises(Exception):
        Query("(", regex=True).compile()


def test_search_limit_and_deep_nesting():
    found, truncated = find_all(list(range(100)), Query("1"), limit=5)
    assert truncated and len(found) == 5
    deep = current = {}
    for _ in range(5000):  # deeper than the default recursion limit
        current["n"] = {}
        current = current["n"]
    current["target"] = "here"
    assert len(next(iter_matches(deep, Query("target")))) == 5001


# --- loader ------------------------------------------------------------------

def test_decode_bytes_handles_windows_encodings():
    text = '{"a": "°F"}'
    assert decode_bytes(text.encode("utf-8"))[1] == "UTF-8"
    assert decode_bytes(codecs.BOM_UTF8 + text.encode())[0] == text
    utf16 = codecs.BOM_UTF16_LE + text.encode("utf-16-le")  # what PowerShell 5.1 writes
    assert decode_bytes(utf16) == (text, "UTF-16")
    assert decode_bytes(text.encode("cp1252"))[1] == "Windows-1252"


def test_parse_error_has_location_and_snippet():
    with pytest.raises(LoadError) as info:
        parse_text('{\n  "a": 1\n  "b": 2\n}', source="x")
    err = info.value
    assert err.line == 3 and err.column == 3
    snippet, caret = err.snippet()
    assert snippet[caret] == '"'


def test_snippet_clips_long_lines():
    text = '[' + '1,' * 5000 + ']'
    with pytest.raises(LoadError) as info:
        parse_text(text, source="x")
    err = info.value
    snippet, caret = err.snippet(width=40)
    assert len(snippet) == 40 and snippet[caret] == text[err.column - 1]


def test_json_lines_detected_and_errors_report_real_line():
    data, kind = parse_text('{"a":1}\n{"a":2}\n\n{"a":3}\n', source="x")
    assert kind == "JSON Lines" and [r["a"] for r in data] == [1, 2, 3]
    with pytest.raises(LoadError) as info:
        parse_text('{"a":1}\n{"a":2}\n{"a":}\n', source="x", json_lines=True)
    assert info.value.line == 3


def test_empty_input():
    with pytest.raises(LoadError, match="empty"):
        parse_text("   \n", source="x")


def test_load_file(tmp_path):
    f = tmp_path / "doc.json"
    f.write_bytes(codecs.BOM_UTF16_LE + json.dumps(DOC, ensure_ascii=False).encode("utf-16-le"))
    doc = load_file(f)
    assert doc.data == DOC and doc.encoding == "UTF-16" and doc.title == "doc.json"
    jl = tmp_path / "log.jsonl"
    jl.write_text('{"x": 1}\n')
    assert load_file(jl).format == "JSON Lines"
    with pytest.raises(LoadError, match="doesn't exist"):
        load_file(tmp_path / "missing.json")
    assert load_text("[1,2]").data == [1, 2]


# --- review fixes ---------------------------------------------------------------------

import collections
import enum
import os
import random

from jsonview.core.model import plan_children as _plan
from jsonview.core.search import _leaf_text


def test_kind_of_subclasses_still_classified():
    class Level(enum.IntEnum):
        LOW = 1
    Point = collections.namedtuple("Point", "x y")
    assert kind_of(Level.LOW) is Kind.NUMBER
    assert kind_of(collections.OrderedDict(a=1)) is Kind.OBJECT
    assert kind_of(Point(1, 2)) is Kind.ARRAY
    assert kind_of(True) is Kind.BOOLEAN and kind_of(1) is Kind.NUMBER


def test_entries_from_cached_key_list_match_plain_iteration():
    obj = {f"k{i}": i for i in range(3000)}
    keys = list(obj)
    plain = _plan(obj, (), 1200, 1300)
    cached = _plan(obj, (), 1200, 1300, keys=keys)
    assert [(e.key, e.value) for e in plain] == [(e.key, e.value) for e in cached]


def _reference_search(data, query):
    """Deliberately simple recursive version of iter_matches, to check the optimized one."""
    match = query.compile()
    out = []
    def walk(path, value, key_text):
        kind = kind_of(value)
        hit = query.in_keys and key_text is not None and match(key_text)
        if not hit and query.in_values and kind not in (Kind.OBJECT, Kind.ARRAY):
            hit = match(fmt.scalar_text(value, kind))
        if hit:
            out.append(path)
        if kind is Kind.OBJECT:
            for k, v in value.items():
                walk(path + (k,), v, str(k))
        elif kind is Kind.ARRAY:
            for i, v in enumerate(value):
                walk(path + (i,), v, None)
    walk((), data, None)
    return out


def test_optimized_search_matches_reference():
    rng = random.Random(42)
    words = ["sat", "SAT", "rat", "fan", "1.5", "true", "null", "Ñandú", "°F", "-2", "1e+100"]
    def gen(depth):
        r = rng.random()
        if depth > 3 or r < 0.4:
            return rng.choice([rng.choice(words), rng.randint(-5, 15), rng.choice([0.5, 1.5, -2.0, 1e100]),
                               True, False, None, float("nan")])
        if r < 0.7:
            return {rng.choice(words) + str(i): gen(depth + 1) for i in range(rng.randint(0, 5))}
        return [gen(depth + 1) for _ in range(rng.randint(0, 5))]
    for _ in range(200):
        data = gen(0)
        for q in (Query("a"), Query("sat", case_sensitive=True), Query("1"), Query("nan"), Query("NaN", case_sensitive=True),
                  Query("true", in_keys=False), Query("°", in_values=False), Query(r"^-?\d$", regex=True), Query("e+1")):
            assert list(iter_matches(data, q)) == _reference_search(data, q)
    assert list(iter_matches("just a string", Query("string"))) == [()]


def test_leaf_text_matches_preview():
    for value in (0, -3, 1.5, 1e100, 2.0, float("inf"), float("-inf"), float("nan"), True, False, None):
        assert _leaf_text(value, type(value)) == fmt.preview(value)


def test_jsonpath_escapes_control_characters_and_dollar():
    assert to_jsonpath(("multi\nline",)) == "$['multi\\nline']"
    assert to_jsonpath(("tab\there", "a\x01")) == "$['tab\\there']['a\\u0001']"
    assert to_jsonpath(("price$",)) == "$['price$']"
    assert to_jsonpath(("snake_case", "x1")) == "$.snake_case.x1"


def test_json_lines_allow_unicode_line_separators_and_crlf():
    rec = json.dumps({"note": "a\u2028b\u2029c\x85d"}, ensure_ascii=False)
    data, kind = parse_text(rec + "\r\n" + rec + "\r\n", source="x")
    assert kind == "JSON Lines" and data[1]["note"] == "a\u2028b\u2029c\x85d"


def test_error_line_text_counts_lines_like_the_parser():
    with pytest.raises(LoadError) as info:
        parse_text('{"a": "x\u2028y",\r\n "b": 1\r\n "c": 2}', source="x")
    assert info.value.line == 3 and info.value.line_text == ' "c": 2}'


def test_number_too_long_is_a_friendly_error_in_both_formats():
    huge = "9" * 5000
    with pytest.raises(LoadError, match="digits"):
        parse_text("[" + huge + "]", source="x")
    with pytest.raises(LoadError, match="digits") as info:
        parse_text('{"a": 1}\n{"b": ' + huge + "}", source="x", json_lines=True)
    assert info.value.line == 2


def test_leading_bom_in_text_is_ignored():
    assert parse_text("\ufeff[1]", source="Clipboard")[0] == [1]


def test_decode_bytes_never_raises():
    assert decode_bytes(b"\xff\xfe{\x00}")[1] == "UTF-16"  # odd length
    assert decode_bytes(codecs.BOM_UTF8 + b'"\xff"')[0] == '"\ufffd"'
    assert decode_bytes(codecs.BOM_UTF32_LE + "[32]".encode("utf-32-le")) == ("[32]", "UTF-32")


def test_minified_error_keeps_only_an_excerpt():
    text = "[" + "1," * 1_000_000 + "]"
    with pytest.raises(LoadError) as info:
        parse_text(text, source="x")
    err = info.value
    assert err.column >= len(text) - 1 and len(err.line_text) <= 4001  # 3.13 points at the comma, 3.12 at the bracket
    snippet, caret = err.snippet(width=50)
    assert snippet[caret] == text[err.column - 1]


def test_folder_and_absolute_paths(tmp_path):
    with pytest.raises(LoadError, match="folder"):
        load_file(tmp_path)
    real = tmp_path / "real.json"
    real.write_text("[1]")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    os.chdir(tmp_path)
    doc = load_file("link.json")
    assert doc.file == link and doc.file.is_absolute()  # absolute, but not resolved through links


def test_embedded_json_too_deep_is_ignored():
    assert fmt.parse_embedded_json("[" * 100_000 + "]" * 100_000) is None


def test_to_json_limited_raises_recursion_for_absurd_depth():
    deep = current = []
    for _ in range(200_000):
        current.append([])
        current = current[0]
    with pytest.raises(RecursionError):
        fmt.to_json_limited(deep, 10**9)  # a small limit returns before reaching the depth
