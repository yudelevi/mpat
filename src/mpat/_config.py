from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path

PYPROJECT = "pyproject.toml"
TOOL_SECTION = "mpat"


@dataclass(frozen=True)
class Config:
    root: Path
    modules: tuple[str, ...]
    allow: tuple[str, ...]


def find_project_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / PYPROJECT).is_file():
            return candidate
    return None


def load_config(start: Path | None = None) -> Config | None:
    root = find_project_root(start or Path.cwd())
    if root is None:
        return None
    data = tomllib.loads((root / PYPROJECT).read_text(encoding="utf-8"))
    section = data.get("tool", {}).get(TOOL_SECTION, {})
    return Config(
        root=root,
        modules=tuple(section.get("modules", ())),
        allow=tuple(section.get("allow", ())),
    )


@functools.cache
def runtime_config() -> Config | None:
    return load_config()


def reset_caches() -> None:
    runtime_config.cache_clear()
