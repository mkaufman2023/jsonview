"""Find keys and values anywhere in the document, including branches never expanded in the UI."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .formatting import scalar_text
from .model import Path, kind_of


@dataclass(frozen=True, slots=True)
class Query:
    text: str
    case_sensitive: bool = False
    regex: bool = False
    in_keys: bool = True
    in_values: bool = True

    def compile(self) -> Callable[[str], bool]:
        """Return a predicate. Raises re.error for an invalid regular expression."""
        if self.regex:
            pattern = re.compile(self.text, 0 if self.case_sensitive else re.IGNORECASE)
            return lambda s: pattern.search(s) is not None
        if self.case_sensitive:
            needle = self.text
            return lambda s: needle in s
        needle = self.text.casefold()
        return lambda s: needle in s.casefold()


_ROOT = object()  # marks the root, which has no key
_LEAF_TYPES = frozenset({str, int, float, bool, type(None)})


def _leaf_text(value: Any, t: type) -> str:
    if value is None:
        return "null"
    if t is bool:
        return "true" if value else "false"
    if t is int:
        return repr(value)
    if t is float:
        return repr(value) if math.isfinite(value) else json.dumps(value)  # NaN, Infinity
    return scalar_text(value, kind_of(value))


def iter_matches(data: Any, query: Query) -> Iterator[Path]:
    """Yield the path of every matching node in document (pre-)order.

    Object keys match against the key text; leaves match against their value
    text (strings raw, others as JSON literals). Array indices never match.
    Iterative rather than recursive, so deep documents can't hit the recursion limit.
    Paths are only built for containers and hits, which keeps big searches fast.
    """
    matches = query.compile()
    in_keys, in_values = query.in_keys, query.in_values
    # Each stack item: (parent path, key or _ROOT, value, key text if the parent is an object)
    stack: list[tuple[Path, Any, Any, str | None]] = [((), _ROOT, data, None)]
    pop, extend = stack.pop, stack.extend
    while stack:
        parent, key, value, key_text = pop()
        hit = in_keys and key_text is not None and matches(key_text)
        t = type(value)
        if t is str:
            if hit or (in_values and matches(value)):
                yield parent if key is _ROOT else parent + (key,)
            continue
        if t is dict or t is list:
            is_object, is_container = t is dict, True
        elif t in _LEAF_TYPES:
            is_object = is_container = False
        else:
            is_object = isinstance(value, Mapping)
            is_container = is_object or isinstance(value, (list, tuple))
        if is_container:
            path = parent if key is _ROOT else parent + (key,)
            if hit:
                yield path
            if is_object:
                items = value.items() if t is dict else list(value.items())
                extend((path, k, v, str(k) if in_keys else None) for k, v in reversed(items))
            else:
                extend((path, i, value[i], None) for i in range(len(value) - 1, -1, -1))
            continue
        if hit or (in_values and matches(_leaf_text(value, t))):
            yield parent if key is _ROOT else parent + (key,)


def find_all(data: Any, query: Query, limit: int | None = None) -> tuple[list[Path], bool]:
    """Convenience wrapper: (matches, truncated)."""
    found: list[Path] = []
    for path in iter_matches(data, query):
        if limit is not None and len(found) >= limit:
            return found, True
        found.append(path)
    return found, False
