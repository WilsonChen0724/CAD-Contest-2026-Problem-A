from __future__ import annotations

from pathlib import Path


def load_config(path: str | None) -> dict:
    """
    Day1 config loader.

    To avoid external dependencies, this returns raw text for now.
    Day2 can replace this with yaml.safe_load if PyYAML is available.
    """
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return {"path": str(p), "raw": p.read_text()}
