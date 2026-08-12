"""WriteRoute: a preservation-first writing and editing route."""
from .audit import audit_text
from .contracts import WritingBrief, compile_draft_contract, compile_revision_contract
from .evidence import verify_draft_evidence
from .formatting import formatting_advice
from .integrity import verify_integrity
from .route import (
    draft_with_callback,
    repair_text,
    rewrite_with_candidates,
    rewrite_with_callback,
    suggest_text,
    verify_text,
)
from .voice import build_voice_profile, voice_distance

__version__ = "2.0.0"
__all__ = [
    "WritingBrief",
    "audit_text",
    "build_voice_profile",
    "compile_draft_contract",
    "compile_revision_contract",
    "draft_with_callback",
    "repair_text",
    "rewrite_with_callback",
    "rewrite_with_candidates",
    "suggest_text",
    "formatting_advice",
    "verify_draft_evidence",
    "verify_integrity",
    "verify_text",
    "voice_distance",
    "__version__",
]
