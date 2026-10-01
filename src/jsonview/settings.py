"""User settings, stored as JSON in %APPDATA%\\JSONViewer\\settings.json."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "JSONViewer"
SETTINGS_FILE = APP_DIR / "settings.json"
MAX_RECENT = 12


@dataclass
class Settings:
    theme: str = "system"  # "system", "light" or "dark"
    geometry: str | None = None
    maximized: bool = False
    show_details: bool = True
    details_width: int | None = None
    auto_reload: bool = False
    last_dir: str | None = None
    recent: list[str] = field(default_factory=list)

    def add_recent(self, path: str | Path) -> None:
        path = str(path)
        key = os.path.normcase(path)  # Windows paths are case-insensitive
        self.recent = [path] + [p for p in self.recent if os.path.normcase(p) != key]
        del self.recent[MAX_RECENT:]

    def remove_recent(self, path: str) -> None:
        key = os.path.normcase(path)
        self.recent = [p for p in self.recent if os.path.normcase(p) != key]

    @classmethod
    def load(cls) -> Settings:
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        try:
            return cls(**{k: v for k, v in raw.items() if k in known})
        except TypeError:
            return cls()

    def save(self) -> None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            # Write to a temp file and swap it in so a crash can't leave half a file.
            fd, tmp = tempfile.mkstemp(dir=APP_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2)
            os.replace(tmp, SETTINGS_FILE)
        except OSError:
            pass  # settings are a convenience; never crash over them
