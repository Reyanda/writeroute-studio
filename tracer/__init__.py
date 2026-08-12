"""
Tracer - raster-to-vector (PNG/JPG -> SVG) translation engine.

Submodules are imported on first use. Three of them need native dependencies that ship
in the `tracer` extra, and importing them eagerly meant `import tracer` failed with a
ModuleNotFoundError naming a package the reader never asked for. Touching a feature that
needs one now raises writeroute.optional.MissingDependency, which says what to install.
"""
from __future__ import annotations

import importlib
from typing import Any

_LAZY: dict[str, str] = {'OutputMode': 'config', 'PRESETS': 'config', 'TracingConfig': 'config', 'ValidityResult': 'config', 'VerificationResult': 'config', 'analyze_image': 'analyzer', 'recommend_output_contract': 'analyzer', 'recommend_preset': 'analyzer', 'remove_background': 'bg_remover', 'calculate_similarity': 'verifier', 'render_svg_to_png': 'verifier', 'auto_tune_conversion': 'verifier', 'validate_output': 'verifier', 'optimize_svg': 'optimizer', 'postprocess_logo_svg': 'logo_postproc', 'convert': 'converter', 'TracerConverter': 'converter', 'SceneCommand': 'document', 'SceneDocument': 'document', 'SceneHistory': 'document', 'SceneNode': 'document', 'annotate_svg_scene': 'document', 'ParityResult': 'parity', 'build_parity_result': 'parity', 'convert_with_parity': 'parity'}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'tracer' has no attribute {name!r}")
    try:
        return getattr(importlib.import_module(f".{module}", __name__), name)
    except ImportError as exc:
        from writeroute.optional import MissingDependency, DISTRIBUTION
        missing = getattr(exc, "name", "") or ""
        package = DISTRIBUTION.get(missing, missing or "a native dependency")
        raise MissingDependency(
            f"tracer.{name} needs {package}, which is not installed.\n"
            f"Install it with:  pip install 'writeroute[tracer]'"
        ) from exc


def __dir__() -> list[str]:
    return sorted(_LAZY)


__version__ = "1.0.0"

__all__ = [
    "PRESETS",
    "OutputMode",
    "TracingConfig",
    "ValidityResult",
    "VerificationResult",
    "analyze_image",
    "recommend_preset",
    "recommend_output_contract",
    "remove_background",
    "calculate_similarity",
    "render_svg_to_png",
    "auto_tune_conversion",
    "validate_output",
    "optimize_svg",
    "postprocess_logo_svg",
    "convert",
    "TracerConverter",
    "SceneNode",
    "SceneDocument",
    "SceneCommand",
    "SceneHistory",
    "annotate_svg_scene",
    "ParityResult",
    "build_parity_result",
    "convert_with_parity",
]
