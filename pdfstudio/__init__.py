"""PDF Studio - form-field detection, filling and annotation for PDF documents.

Submodules load on first use because they need PyMuPDF and OpenCV, which ship in the
`pdf` extra. Importing them eagerly made `import pdfstudio` fail with a
ModuleNotFoundError naming a package the reader never asked for.
"""
from __future__ import annotations

import importlib
from typing import Any

__version__ = "2.1.0"

_LAZY: dict[str, str] = {
    "TracerUnbundler": "unbundler",
    "TracerSlotDetector": "slot_detector",
    "TracerRebundler": "rebundler",
    "TracerSemanticMapper": "semantic_schema",
    "TracerAgenticFillEngine": "semantic_schema",
    "StirlingBridge": "stirling_bridge",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'pdfstudio' has no attribute {name!r}")
    try:
        return getattr(importlib.import_module(f".{module}", __name__), name)
    except ImportError as exc:
        from writeroute.optional import MissingDependency, DISTRIBUTION
        missing = getattr(exc, "name", "") or ""
        package = DISTRIBUTION.get(missing, missing or "a native dependency")
        raise MissingDependency(
            f"pdfstudio.{name} needs {package}, which is not installed.\n"
            f"Install it with:  pip install 'writeroute[pdf]'"
        ) from exc


def __dir__() -> list[str]:
    return sorted(_LAZY)
