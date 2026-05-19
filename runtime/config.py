from __future__ import annotations

from pathlib import Path


def load_config(path: str | None) -> dict:
    """Load the optional contest config file as raw text."""
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return {"path": str(p), "raw": p.read_text()}
