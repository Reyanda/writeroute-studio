from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


DATA_PACKAGE = "stats_brain.data"
SCHEMA_PACKAGE = "stats_brain.schemas"


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict[str, Any]:
    filename = name if name.endswith(".yaml") else f"{name}.yaml"
    resource = resources.files(DATA_PACKAGE).joinpath(filename)
    with resource.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Registry {filename} must contain a mapping")
    return value


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    filename = name if name.endswith(".json") else f"{name}.json"
    resource = resources.files(SCHEMA_PACKAGE).joinpath(filename)
    with resource.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def registry_counts() -> dict[str, int]:
    return {
        "methods": len(load_yaml("method_registry").get("methods", {})),
        "method_families": len(load_yaml("method_registry").get("families", {})),
        "estimands": len(load_yaml("estimand_registry").get("estimands", {})),
        "design_profiles": len(load_yaml("design_profiles").get("profiles", {})),
        "debates": len(load_yaml("debate_registry").get("debates", {})),
        "sources": len(load_yaml("source_registry").get("sources", {})),
        "rules": len(load_yaml("rule_catalog").get("rules", [])) if _resource_exists("rule_catalog.yaml") else 0,
    }


def _resource_exists(filename: str) -> bool:
    try:
        return resources.files(DATA_PACKAGE).joinpath(filename).is_file()
    except (FileNotFoundError, ModuleNotFoundError):
        return False


def export_registry(name: str, output: str | Path) -> Path:
    value = load_yaml(name)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".json":
        output_path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        output_path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return output_path
