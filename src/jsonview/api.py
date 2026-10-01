"""``jsonview.view()``: open the viewer on Python data, a file, or JSON text."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .core.loader import Document, LoadError, decode_bytes, load_object, load_text


def _coerce(obj: Any) -> Document | LoadError | Path | None:
    if obj is None:
        return None
    if isinstance(obj, os.PathLike):
        return Path(obj)
    # requests.Response, httpx.Response and similar
    if not isinstance(obj, (dict, list, tuple, str, bytes)) and callable(getattr(obj, "json", None)):
        return load_object(obj.json(), source=f"{type(obj).__name__}.json()")
    if isinstance(obj, (bytes, bytearray)):
        obj = decode_bytes(bytes(obj))[0]
    if isinstance(obj, str):
        if len(obj) < 1024 and "\n" not in obj and obj.strip() and Path(obj.strip()).is_file():
            return Path(obj.strip())
        try:
            return load_text(obj, source="Python string")
        except LoadError as err:
            return err
    return load_object(obj)


def view(obj: Any = None, *, theme: str | None = None) -> None:
    """Open a viewer window and wait until it's closed.

    ``obj`` can be a dict/list (or any Python object), a path, JSON text or
    bytes, or a response object with a ``.json()`` method. ``theme`` is
    "system", "light" or "dark" and overrides the saved setting for this window.
    """
    source = _coerce(obj)
    from .ui.app import run  # imported here so `import jsonview` doesn't need tkinter

    run(source, theme=theme)
