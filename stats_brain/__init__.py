"""STATS-BRAIN: estimand-first statistical and epidemiological review."""

from .auctor_bridge import AuctorBridge
from .calculators import StatisticalCalculators
from .models import ReviewContext, ReviewFinding, ReviewReport
from .reviewer import StatsBrainReviewer

__all__ = [
    "AuctorBridge",
    "StatsBrainReviewer",
    "ReviewContext",
    "ReviewFinding",
    "ReviewReport",
    "StatisticalCalculators",
]
__version__ = "1.0.0"
