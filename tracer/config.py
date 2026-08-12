"""
Configuration presets and data structures for Tracer.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


class OutputMode(str, Enum):
    """Representation contract exposed by every Tracer interface."""

    PURE_VECTOR = "pure_vector"
    HYBRID_PARITY = "hybrid_parity"
    ABSOLUTE_PARITY = "absolute_parity"
    EXACT_WRAPPER = "exact_wrapper"


@dataclass
class ValidityResult:
    """Hard render-contract result, evaluated independently of quality ranking."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    dimension_match: bool = True
    original_alpha_coverage: float = 1.0
    rendered_alpha_coverage: float = 1.0
    original_colour_count: int = 0
    rendered_colour_count: int = 0
    path_count: int = 0
    path_command_count: int = 0
    image_count: int = 0
    svg_bytes: int = 0
    validity_profile: str = "general"
    bit_exact: bool = False
    bit_exact_rgba: bool = False
    mismatched_pixels: int = 0
    max_premultiplied_delta: int = 0
    parity_digest: str = ""


@dataclass
class TracingConfig:
    colormode: str = "color"          # 'color' or 'binary'
    hierarchical: str = "stacked"    # 'stacked' or 'cutout'
    mode: str = "spline"             # 'spline', 'polygon', or 'none'
    filter_speckle: int = 4          # 0-100: size of speckle to discard
    color_precision: int = 6         # 1-8: color quantization precision (2^n colors)
    layer_difference: int = 16       # 0-255: color distance between layers
    corner_threshold: int = 60       # 0-180: threshold angle for sharp corners
    length_threshold: float = 4.0    # 3.5-10: length threshold for spline segments
    max_iterations: int = 10         # max iterations for curve fitting
    splice_threshold: int = 45       # threshold angle for splicing curves
    path_precision: int = 3          # decimal places in SVG path coordinates

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TracingConfig:
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys and v is not None}
        return cls(**filtered)


@dataclass
class VerificationResult:
    ssim: float
    psnr: float
    mse: float
    pixel_diff_ratio: float
    passed: bool
    iterations_used: int
    best_config: TracingConfig
    quality_score: float = 0.0
    edge_similarity: float = 0.0
    color_similarity: float = 0.0
    alpha_similarity: float = 0.0
    path_count: int = 0
    svg_bytes: int = 0
    quality_profile: str = "balanced"
    candidate_history: list[Dict[str, Any]] = field(default_factory=list)
    validity: Optional[ValidityResult] = None


PRESETS: Dict[str, Dict[str, Any]] = {
    "precision_ultra": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 1,
        "color_precision": 8,
        "layer_difference": 4,
        "corner_threshold": 30,
        "length_threshold": 3.0,
        "max_iterations": 30,
        "splice_threshold": 25,
        "path_precision": 4,
        "description": "Precision Ultra: Maximum colour precision for fine text, UI icons, maps, and detailed artwork.",
    },
    "complex_map_ui": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "polygon",
        "filter_speckle": 1,
        "color_precision": 8,
        "layer_difference": 4,
        "corner_threshold": 40,
        "length_threshold": 3.5,
        "max_iterations": 25,
        "splice_threshold": 30,
        "path_precision": 4,
        "description": "Maps & UI Screenshots: Optimized for screenshots with maps, UI cards, markers, and fine text labels.",
    },
    "logo": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 4,
        "color_precision": 6,
        "layer_difference": 16,
        "corner_threshold": 60,
        "length_threshold": 4.0,
        "max_iterations": 10,
        "splice_threshold": 45,
        "path_precision": 3,
        "description": "Recommended default for logos & multi-color icons. Crisp curves and clean layering.",
    },
    "high_fidelity": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 2,
        "color_precision": 8,
        "layer_difference": 8,
        "corner_threshold": 45,
        "length_threshold": 3.5,
        "max_iterations": 15,
        "splice_threshold": 30,
        "path_precision": 4,
        "description": "High detail & shape density. Closest visual fidelity to original complex artwork.",
    },
    "poster": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 8,
        "color_precision": 4,
        "layer_difference": 32,
        "corner_threshold": 80,
        "length_threshold": 5.0,
        "max_iterations": 8,
        "splice_threshold": 60,
        "path_precision": 2,
        "description": "Limited palette, posterized style. Creates artistic simplified vector shapes.",
    },
    "lineart": {
        "colormode": "binary",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 2,
        "color_precision": 1,
        "layer_difference": 16,
        "corner_threshold": 50,
        "length_threshold": 3.5,
        "max_iterations": 12,
        "splice_threshold": 40,
        "path_precision": 3,
        "description": "Black & white line art, silhouettes, stamps, and single-color logos.",
    },
    "pixel": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "polygon",
        "filter_speckle": 0,
        "color_precision": 6,
        "layer_difference": 1,
        "corner_threshold": 0,
        "length_threshold": 3.5,
        "max_iterations": 5,
        "splice_threshold": 45,
        "path_precision": 1,
        "description": "Pixel art & retro graphics. Preserves exact square block boundaries.",
    },
    "default": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 4,
        "color_precision": 6,
        "layer_difference": 16,
        "corner_threshold": 60,
        "length_threshold": 4.0,
        "max_iterations": 10,
        "splice_threshold": 45,
        "path_precision": 3,
        "description": "Balanced general-purpose vectorization settings.",
    },
}
