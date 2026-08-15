"""aiwd — AI Writing Detection & Anti-AI toolkit.

Ontology-driven detector, explainer, and conservative de-slop rewriter with a
data-driven skill (feature-pack) engine. Stdlib only.
"""
from .rewrite import clean_text
from .scoring import calibrate, load_baselines, scan_text, score
from .skillengine import SkillRegistry
from .textmodel import parse

__version__ = "1.1.0"
__all__ = [
    "parse", "score", "scan_text", "clean_text", "calibrate",
    "load_baselines", "SkillRegistry", "__version__",
]
