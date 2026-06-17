from __future__ import annotations

from pathlib import Path
from typing import Any


def load_config(path: str | None) -> dict:
    """Load the optional contest config file.

    The project intentionally avoids a PyYAML dependency, so this parser only
    supports the small nested key/value shape used by config.example.yaml.
    The raw text is preserved for the LLM API compatibility path.
    """
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = p.read_text(encoding="utf-8")
    parsed = _parse_simple_yaml(raw)
    parsed["path"] = str(p)
    parsed["raw"] = raw
    return parsed


def _parse_simple_yaml(raw: str) -> dict[str, Any]:
    config: dict[str, Any] = {}
    current_section: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "#" in stripped:
            stripped = stripped.split("#", 1)[0].rstrip()
            if not stripped:
                continue
        indent = len(line) - len(line.lstrip(" "))
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if indent == 0:
            if value == "":
                config[key] = {}
                current_section = key
            else:
                config[key] = _parse_scalar(value)
                current_section = None
        elif current_section is not None and isinstance(config.get(current_section), dict):
            config[current_section][key] = _parse_scalar(value)
    return config


def _parse_scalar(value: str) -> Any:
    if value in {"null", "None", "~"}:
        return None
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
