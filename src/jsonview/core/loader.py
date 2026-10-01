"""Read JSON from files, text, or Python objects into a Document."""

from __future__ import annotations

import codecs
import json
import time
from dataclasses import dataclass
from pathlib import Path as FilePath
from typing import Any

JSONL_SUFFIXES = frozenset({".jsonl", ".ndjson", ".jsonlines"})


@dataclass(slots=True)
class Document:
    data: Any
    source: str  # what to show the user: a file path, "Clipboard", etc.
    file: FilePath | None = None
    format: str = "JSON"
    encoding: str | None = None
    size_bytes: int | None = None
    load_seconds: float = 0.0

    @property
    def title(self) -> str:
        return self.file.name if self.file else self.source


class LoadError(Exception):
    """A problem reading or parsing input, with enough context to point at the mistake."""

    def __init__(
        self,
        message: str,
        *,
        source: str,
        line: int | None = None,
        column: int | None = None,
        line_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.source = source
        self.line = line
        self.column = column
        self.line_text = line_text

    @property
    def location(self) -> str | None:
        if self.line is None:
            return None
        return f"line {self.line:,}, column {self.column:,}"

    def snippet(self, width: int = 90) -> tuple[str, int] | None:
        """The offending line clipped to ``width`` around the error, and the caret's offset in it."""
        if self.line_text is None or self.column is None:
            return None
        text = self.line_text.replace("\t", " ")  # the parser counts a tab as one column
        col = max(self.column - 1, 0)
        start = max(0, min(col - width // 2, len(text) - width))
        clipped = text[start : start + width]
        caret = col - start
        if start > 0:
            clipped = "…" + clipped[1:]
        if start + width < len(text):
            clipped = clipped[:-1] + "…"
        return clipped, caret


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Decode file bytes. Returns (text, encoding name shown to the user).

    Windows PowerShell 5.1's Out-File and > redirection write UTF-16 with a BOM,
    and some older tools write Windows-1252, so both are handled.
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8) :].decode("utf-8"), "UTF-8 with BOM"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16"), "UTF-16"
    try:
        return raw.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "Windows-1252"


def _error_from_decode(exc: json.JSONDecodeError, source: str, line_offset: int = 0) -> LoadError:
    lines = exc.doc.splitlines()
    line_text = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else ""
    return LoadError(
        exc.msg,
        source=source,
        line=exc.lineno + line_offset,
        column=exc.colno,
        line_text=line_text,
    )


def _parse_json_lines(text: str, source: str) -> list[Any]:
    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise _error_from_decode(exc, source, line_offset=number - 1) from None
    return records


def _looks_like_json_lines(text: str) -> bool:
    for line in text.splitlines():
        if line.strip():
            try:
                json.loads(line)
            except ValueError:
                return False
            return True
    return False


def parse_text(text: str, *, source: str, json_lines: bool = False) -> tuple[Any, str]:
    """Parse text as JSON (or JSON Lines). Returns (data, format name)."""
    if not text.strip():
        raise LoadError("There's nothing to read: the input is empty.", source=source)
    if json_lines:
        return _parse_json_lines(text, source), "JSON Lines"
    try:
        return json.loads(text), "JSON"
    except json.JSONDecodeError as exc:
        # Several JSON documents, one per line, is JSON Lines.
        if exc.msg == "Extra data" and _looks_like_json_lines(text):
            try:
                return _parse_json_lines(text, source), "JSON Lines"
            except LoadError:
                pass
        raise _error_from_decode(exc, source) from None
    except ValueError as exc:  # e.g. a number with more digits than Python allows by default
        raise LoadError(str(exc), source=source) from None
    except RecursionError:
        raise LoadError("The document is nested too deeply to read.", source=source) from None


def load_file(path: str | FilePath) -> Document:
    file = FilePath(path).expanduser()
    source = str(file)
    started = time.perf_counter()
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        raise LoadError("The file doesn't exist. It may have been moved or deleted.", source=source) from None
    except IsADirectoryError:
        raise LoadError("That's a folder, not a file.", source=source) from None
    except PermissionError:
        raise LoadError("Windows denied access to the file. It may be open in another program.", source=source) from None
    except OSError as exc:
        raise LoadError(f"The file couldn't be read: {exc.strerror or exc}", source=source) from None
    text, encoding = decode_bytes(raw)
    data, fmt = parse_text(text, source=source, json_lines=file.suffix.lower() in JSONL_SUFFIXES)
    return Document(
        data=data,
        source=source,
        file=file.resolve(),
        format=fmt,
        encoding=encoding,
        size_bytes=len(raw),
        load_seconds=time.perf_counter() - started,
    )


def load_text(text: str, source: str = "Clipboard") -> Document:
    started = time.perf_counter()
    data, fmt = parse_text(text, source=source)
    return Document(
        data=data,
        source=source,
        format=fmt,
        size_bytes=len(text.encode("utf-8", errors="replace")),
        load_seconds=time.perf_counter() - started,
    )


def load_object(obj: Any, source: str = "Python object") -> Document:
    return Document(data=obj, source=source, format="Python object")
