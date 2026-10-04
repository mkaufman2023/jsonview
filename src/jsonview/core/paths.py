"""Format a value's location as JSONPath, a Python expression, or a JSON Pointer."""

from __future__ import annotations

import json
import re
from enum import StrEnum

from .model import Path

# RFC 9535 dot-notation names (ASCII subset); anything else uses bracket notation
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ESCAPES = {"\\": "\\\\", "'": "\\'", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _quote_name(name: str) -> str:
    """A single-quoted JSONPath name with backslashes, quotes and control characters escaped."""
    out = []
    for ch in name:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ch < " ":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "'" + "".join(out) + "'"


class PathStyle(StrEnum):
    JSONPATH = "jsonpath"
    PYTHON = "python"
    POINTER = "pointer"

    @property
    def title(self) -> str:
        return {"jsonpath": "JSONPath", "python": "Python", "pointer": "JSON Pointer"}[self.value]


def _is_index(key: object) -> bool:
    return isinstance(key, int) and not isinstance(key, bool)


def to_jsonpath(path: Path) -> str:
    """``$.points[0]['supply temp']``"""
    parts = ["$"]
    for key in path:
        if _is_index(key):
            parts.append(f"[{key}]")
        elif _IDENTIFIER.match(str(key)):
            parts.append(f".{key}")
        else:
            parts.append(f"[{_quote_name(str(key))}]")
    return "".join(parts)


def to_python(path: Path, root: str = "data") -> str:
    """``data["points"][0]["supply temp"]``, ready to paste into a script."""
    parts = [root]
    for key in path:
        if isinstance(key, str):
            # JSON string escapes are valid Python string escapes.
            parts.append(f"[{json.dumps(key, ensure_ascii=False)}]")
        else:
            parts.append(f"[{key!r}]")
    return "".join(parts)


def to_pointer(path: Path) -> str:
    """RFC 6901: ``/points/0/supply temp``. The root is the empty string."""
    return "".join("/" + str(key).replace("~", "~0").replace("/", "~1") for key in path)


def format_path(path: Path, style: PathStyle) -> str:
    match style:
        case PathStyle.JSONPATH:
            return to_jsonpath(path)
        case PathStyle.PYTHON:
            return to_python(path)
        case PathStyle.POINTER:
            return to_pointer(path)
