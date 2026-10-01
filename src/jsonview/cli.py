"""Command line: ``jsonview [FILE]`` or ``python -m jsonview [FILE]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TK_MISSING = (
    "This Python doesn't include tkinter, which JSON Viewer needs.\n\n"
    "Run the Python installer again, choose Modify, and make sure "
    '"tcl/tk and IDLE" is checked. Then try again.\n\n'
    "To check: python -c \"import tkinter; tkinter._test()\""
)


def _fatal(message: str) -> None:
    """Report an error with a native dialog; jsonview.exe has no console to print to."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "JSON Viewer", 0x10)
            return
        except (AttributeError, OSError):
            pass
    print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jsonview", description="View JSON in a fast, searchable tree.")
    parser.add_argument("file", nargs="?", help="a JSON or JSON Lines file, or - to read standard input")
    parser.add_argument("--theme", choices=["system", "light", "dark"], help="override the saved theme")
    args = parser.parse_args(argv)

    try:
        import tkinter  # noqa: F401
    except ImportError:
        _fatal(TK_MISSING)
        return 1

    from .core.loader import LoadError, load_text
    from .ui.app import run

    source = None
    if args.file == "-":
        text = sys.stdin.read() if sys.stdin else ""
        try:
            source = load_text(text, source="Standard input")
        except LoadError as err:
            source = err
    elif args.file:
        source = Path(args.file)
    run(source, theme=args.theme)
    return 0


if __name__ == "__main__":
    sys.exit(main())
