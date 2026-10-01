"""Find keys and values anywhere in the document, including branches never expanded in the UI."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .formatting import scalar_text
from .model import CONTAINER_KINDS, Path, kind_of


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


def iter_matches(data: Any, query: Query) -> Iterator[Path]:
    """Yield the path of every matching node in document (pre-)order.

    Object keys match against the key text; leaves match against their value
    text (strings raw, others as JSON literals). Array indices never match.
    Iterative rather than recursive, so deep documents can't hit the recursion limit.
    """
    matches = query.compile()
    # Each stack item: (path, value, key text if the parent is an object, else None)
    stack: list[tuple[Path, Any, str | None]] = [((), data, None)]
    while stack:
        path, value, key_text = stack.pop()
        kind = kind_of(value)
        hit = False
        if query.in_keys and key_text is not None and matches(key_text):
            hit = True
        elif query.in_values and kind not in CONTAINER_KINDS and matches(scalar_text(value, kind)):
            hit = True
        if hit:
            yield path
        if isinstance(value, Mapping):
            items = value.items() if isinstance(value, dict) else list(value.items())
            stack.extend((path + (k,), v, str(k)) for k, v in reversed(items))
        elif kind in CONTAINER_KINDS:
            stack.extend((path + (i,), value[i], None) for i in range(len(value) - 1, -1, -1))


def find_all(data: Any, query: Query, limit: int | None = None) -> tuple[list[Path], bool]:
    """Convenience wrapper: (matches, truncated)."""
    found: list[Path] = []
    for path in iter_matches(data, query):
        if limit is not None and len(found) >= limit:
            return found, True
        found.append(path)
    return found, False
