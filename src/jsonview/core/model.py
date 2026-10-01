"""Classify JSON values and plan which rows a container shows.

The UI never walks the JSON itself. It asks this module for the rows of a
container, which keeps lazy loading and the splitting of huge containers into
ranges ("buckets") in one place that is easy to test.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import Any

type Key = str | int
type Path = tuple[Key, ...]

# Containers with more children than this are split into range buckets.
DIRECT_LIMIT = 1000
# Each bucket level holds at most this many sub-buckets.
BUCKET_BASE = 100


class Kind(StrEnum):
    OBJECT = "object"
    ARRAY = "array"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"
    OTHER = "other"  # non-JSON Python objects handed to view()


CONTAINER_KINDS = frozenset({Kind.OBJECT, Kind.ARRAY})


def kind_of(value: Any) -> Kind:
    if value is None:
        return Kind.NULL
    if isinstance(value, bool):  # must come before int: bool is an int subclass
        return Kind.BOOLEAN
    if isinstance(value, (int, float)):
        return Kind.NUMBER
    if isinstance(value, str):
        return Kind.STRING
    if isinstance(value, Mapping):
        return Kind.OBJECT
    if isinstance(value, (list, tuple)):
        return Kind.ARRAY
    return Kind.OTHER


def is_container(value: Any) -> bool:
    return kind_of(value) in CONTAINER_KINDS


def child_count(value: Any) -> int:
    return len(value) if is_container(value) else 0


def resolve(data: Any, path: Path) -> Any:
    """Return the value at ``path``. Raises KeyError if the path doesn't exist."""
    for key in path:
        if not is_container(data):
            raise KeyError(key)
        try:
            data = data[key]
        except (IndexError, TypeError) as exc:
            raise KeyError(key) from exc
    return data


def path_exists(data: Any, path: Path) -> bool:
    try:
        resolve(data, path)
    except KeyError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class Entry:
    """One value in the document, addressed by its path from the root."""

    key: Key | None  # None only for the root
    path: Path
    value: Any
    kind: Kind

    @property
    def is_container(self) -> bool:
        return self.kind in CONTAINER_KINDS

    @property
    def parent_is_array(self) -> bool:
        return isinstance(self.key, int) and not isinstance(self.key, bool)


@dataclass(frozen=True, slots=True)
class Bucket:
    """A range of children [start, stop) of the container at ``path``."""

    path: Path
    start: int
    stop: int

    @property
    def size(self) -> int:
        return self.stop - self.start

    @property
    def label(self) -> str:
        return f"[{self.start:,} … {self.stop - 1:,}]"


type Row = Entry | Bucket


def root_entry(data: Any) -> Entry:
    return Entry(None, (), data, kind_of(data))


def iter_entries(container: Any, path: Path, start: int = 0, stop: int | None = None) -> Iterator[Entry]:
    if isinstance(container, Mapping):
        pairs = islice(container.items(), start, stop)
    else:
        pairs = enumerate(container[start:stop], start)
    for key, value in pairs:
        yield Entry(key, path + (key,), value, kind_of(value))


def plan_children(
    container: Any,
    path: Path,
    start: int = 0,
    stop: int | None = None,
    *,
    direct_limit: int = DIRECT_LIMIT,
    base: int = BUCKET_BASE,
) -> list[Row]:
    """Rows to show for ``container[start:stop]``: entries, or buckets if there are too many."""
    total = len(container)
    stop = total if stop is None else min(stop, total)
    span = stop - start
    if span <= direct_limit:
        return list(iter_entries(container, path, start, stop))
    chunk = base
    while -(-span // chunk) > base:  # ceil(span / chunk) buckets must fit in one level
        chunk *= base
    return [Bucket(path, s, min(s + chunk, stop)) for s in range(start, stop, chunk)]


def bucket_key_range(container: Any, bucket: Bucket, max_chars: int = 28) -> str:
    """For object buckets, describe the first and last key in the range."""
    if not isinstance(container, Mapping):
        return f"{bucket.size:,} items"
    keys = list(islice(container.keys(), bucket.start, bucket.stop))
    first, last = (str(k) for k in (keys[0], keys[-1]))
    trim = lambda s: s if len(s) <= max_chars else s[: max_chars - 1] + "…"  # noqa: E731
    return f"{trim(first)} … {trim(last)}"


def count_nodes(value: Any, limit: int | None = None) -> int:
    """Count every value in the tree (the root included). Stops early past ``limit``."""
    count = 0
    stack = [value]
    while stack:
        current = stack.pop()
        count += 1
        if limit is not None and count > limit:
            return count
        if isinstance(current, Mapping):
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return count


def iter_node_count(value: Any, step: int = 50_000) -> Iterator[int]:
    """Like count_nodes, but yields the running total every ``step`` nodes so a UI can stay responsive."""
    count = 0
    stack = [value]
    while stack:
        current = stack.pop()
        count += 1
        if count % step == 0:
            yield count
        if isinstance(current, Mapping):
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    yield count
