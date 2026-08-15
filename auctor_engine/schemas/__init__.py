"""Packaged JSON contracts for provider and engine integrations."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def available_schemas() -> list[str]:
    root = files(__name__)
    return sorted(item.name for item in root.iterdir() if item.name.endswith(".json"))


def load_schema(name: str) -> dict[str, Any]:
    available = available_schemas()
    candidates = [name]
    if not name.endswith(".json"):
        candidates.extend((f"{name}.json", f"{name}.schema.json"))
    normalized = next((candidate for candidate in candidates if candidate in available), "")
    if not normalized:
        raise KeyError(f"Unknown Auctor schema: {name}")
    with files(__name__).joinpath(normalized).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Schema {normalized} does not contain a JSON object.")
    return value


__all__ = ["available_schemas", "load_schema"]
