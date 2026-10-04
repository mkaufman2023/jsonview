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


_THEMES = ("system", "light", "dark")


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

    def __post_init__(self) -> None:
        # Not a field: recent files removed in this session, so save() doesn't merge them back.
        self._removed: set[str] = set()

    def add_recent(self, path: str | Path) -> None:
        path = str(path)
        key = os.path.normcase(path)  # Windows paths are case-insensitive
        self._removed.discard(key)
        self.recent = [path] + [p for p in self.recent if os.path.normcase(p) != key]
        del self.recent[MAX_RECENT:]

    def remove_recent(self, path: str) -> None:
        key = os.path.normcase(path)
        self._removed.add(key)
        self.recent = [p for p in self.recent if os.path.normcase(p) != key]

    def clear_recent(self) -> None:
        self._removed.update(os.path.normcase(p) for p in self.recent)
        self.recent = []

    @classmethod
    def load(cls) -> Settings:
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        settings = cls()
        # Take each saved value only if it has the right type, so a hand-edited file can't break the app.
        for f in fields(cls):
            value = raw.get(f.name)
            default = getattr(settings, f.name)
            if f.name == "recent":
                if isinstance(value, list):
                    settings.recent = [p for p in value if isinstance(p, str)][:MAX_RECENT]
            elif f.name == "theme":
                if value in _THEMES:
                    settings.theme = value
            elif isinstance(default, bool):
                if isinstance(value, bool):
                    setattr(settings, f.name, value)
            elif f.name == "details_width":
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    settings.details_width = value
            elif isinstance(value, str):  # geometry, last_dir
                setattr(settings, f.name, value)
        return settings

    def save(self) -> None:
        """Write settings, merging recent files saved meanwhile by other JSON Viewer windows."""
        try:
            on_disk = Settings.load()
            mine = {os.path.normcase(p) for p in self.recent}
            others = [
                p for p in on_disk.recent
                if os.path.normcase(p) not in mine and os.path.normcase(p) not in self._removed
            ]
            self.recent = (self.recent + others)[:MAX_RECENT]
            APP_DIR.mkdir(parents=True, exist_ok=True)
            # Write to a temp file and swap it in so a crash can't leave half a file.
            fd, tmp = tempfile.mkstemp(dir=APP_DIR, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(asdict(self), fh, indent=2)
                os.replace(tmp, SETTINGS_FILE)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            pass  # settings are a convenience; never crash over them
