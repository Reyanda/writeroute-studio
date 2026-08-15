from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


MISSING = object()
EM_DASH_RE = re.compile("[\u2014\u2015]")


def get_path(value: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def first_present(value: Mapping[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        candidate = get_path(value, path, MISSING)
        if candidate is not MISSING and present(candidate):
            return candidate
    return default


def present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    return [value]


def normalize_key(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def flatten_strings(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, str):
        output.append(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            output.extend(flatten_strings(item))
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            output.extend(flatten_strings(item))
    return output


def contains_any(text: str, phrases: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(phrase.casefold() in lowered for phrase in phrases)


def finite_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def sanitize_text(value: str) -> str:
    return EM_DASH_RE.sub(" - ", value)


def deep_sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        return {str(key): deep_sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [deep_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(deep_sanitize(item) for item in value)
    return value


def unique_preserve(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    output: list[Any] = []
    for item in values:
        try:
            marker = item
            if marker in seen:
                continue
            seen.add(marker)
        except TypeError:
            marker = repr(item)
            if marker in seen:
                continue
            seen.add(marker)
        output.append(item)
    return output
