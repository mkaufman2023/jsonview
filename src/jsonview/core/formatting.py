"""Turn values into the text shown in rows, the detail pane, and the clipboard."""

from __future__ import annotations

import json
import pprint
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from .model import Kind, Key, kind_of

ELLIPSIS = "…"


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (plural_word or word + 's')}"


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + ELLIPSIS


def key_label(key: Key | None) -> str:
    """Text for the Key column. Empty or multi-line keys are shown quoted so they stay visible."""
    if key is None:
        return "(root)"
    text = str(key)
    if text == "" or any(ch in text for ch in "\r\n\t"):
        return json.dumps(text, ensure_ascii=False)
    return text


def preview(value: Any, kind: Kind | None = None, max_chars: int = 300) -> str:
    """One-line text for the Value column."""
    kind = kind or kind_of(value)
    match kind:
        case Kind.OBJECT:
            return "{ }" if not value else "{ " + plural(len(value), "key") + " }"
        case Kind.ARRAY:
            return "[ ]" if not value else "[ " + plural(len(value), "item") + " ]"
        case Kind.STRING:
            # Escape only a bounded slice so a 50 MB string doesn't get fully re-encoded.
            clipped = value[:max_chars]
            inner = json.dumps(clipped, ensure_ascii=False)[1:-1]
            truncated = len(clipped) < len(value)
            if len(inner) > max_chars:  # escapes like \n made it longer
                inner, truncated = inner[:max_chars], True
            return '"' + inner + (ELLIPSIS if truncated else "") + '"'
        case Kind.BOOLEAN:
            return "true" if value else "false"
        case Kind.NULL:
            return "null"
        case Kind.NUMBER:
            return json.dumps(value)
        case _:
            return _truncate(repr(value), max_chars)


def type_label(value: Any, kind: Kind | None = None) -> str:
    kind = kind or kind_of(value)
    return type(value).__name__ if kind is Kind.OTHER else kind.value


def describe(value: Any, kind: Kind | None = None) -> str:
    """A short sentence for the detail pane header."""
    kind = kind or kind_of(value)
    match kind:
        case Kind.OBJECT:
            return "Object with " + plural(len(value), "key")
        case Kind.ARRAY:
            return "Array with " + plural(len(value), "item")
        case Kind.STRING:
            return "String, " + plural(len(value), "character")
        case Kind.NUMBER:
            return "Number (integer)" if isinstance(value, int) else "Number (decimal)"
        case Kind.BOOLEAN:
            return "Boolean"
        case Kind.NULL:
            return "Null"
        case _:
            return f"Python {type(value).__name__}"


def to_json(value: Any, indent: int | None = 2) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=indent, default=repr)
    except (TypeError, ValueError):  # e.g. dict keys that JSON can't represent
        return pprint.pformat(value, sort_dicts=False)


def to_json_limited(value: Any, limit: int, indent: int = 2) -> tuple[str, bool]:
    """Pretty-print at most ``limit`` characters. Returns (text, was_truncated).

    Uses the incremental encoder so a huge subtree costs only as much as the
    part that is actually shown.
    """
    encoder = json.JSONEncoder(ensure_ascii=False, indent=indent, default=repr)
    parts: list[str] = []
    size = 0
    try:
        for chunk in encoder.iterencode(value):
            parts.append(chunk)
            size += len(chunk)
            if size > limit:
                return "".join(parts)[:limit], True
    except (TypeError, ValueError):
        text = pprint.pformat(value, sort_dicts=False)
        return text[:limit], len(text) > limit
    return "".join(parts), False


def copy_text(value: Any, kind: Kind | None = None) -> str:
    """What "Copy value" puts on the clipboard: raw text for strings, JSON for everything else."""
    kind = kind or kind_of(value)
    match kind:
        case Kind.STRING:
            return value
        case Kind.OBJECT | Kind.ARRAY:
            return to_json(value)
        case Kind.OTHER:
            return repr(value)
        case _:
            return preview(value, kind)


def scalar_text(value: Any, kind: Kind) -> str:
    """The text a search matches against for a leaf value."""
    if kind is Kind.STRING:
        return value
    if kind is Kind.OTHER:
        return repr(value)
    return preview(value, kind)


def is_url(text: Any) -> bool:
    if not isinstance(text, str) or len(text) > 4096 or any(ch.isspace() for ch in text):
        return False
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def parse_embedded_json(text: Any, max_chars: int = 50_000_000) -> Any | None:
    """If a string holds a JSON object or array (common in API payloads), return it parsed."""
    if not isinstance(text, str) or len(text) > max_chars:
        return None
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, (Mapping, list)) else None
