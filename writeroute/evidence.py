"""Evidence-bound checks for drafting from a brief.

The gate is deliberately conservative. It does not claim semantic entailment. It
prevents a common and checkable failure: a draft introducing factual anchors that
were not present in the supplied brief or evidence ledger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .contracts import WritingBrief
from .integrity import Anchor, extract_anchors


_PROTECTED_DRAFT_CATEGORIES = frozenset({
    "number", "date", "statistic", "citation", "url", "doi", "quotation",
    "cross_reference", "acronym", "inline_code", "cli_flag", "file_path",
})
_AUTHOR_INPUT = re.compile(r"\[AUTHOR INPUT:[^\]]*\]", re.IGNORECASE)


@dataclass(frozen=True)
class EvidenceBoundaryViolation:
    category: str
    value: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"category": self.category, "value": self.value, "message": self.message}


@dataclass
class EvidenceBoundaryReport:
    passes: bool
    supplied_anchor_count: int
    candidate_anchor_count: int
    violations: list[EvidenceBoundaryViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passes": self.passes,
            "suppliedAnchorCount": self.supplied_anchor_count,
            "candidateAnchorCount": self.candidate_anchor_count,
            "violations": [item.to_dict() for item in self.violations],
        }


def _brief_text(brief: WritingBrief) -> str:
    parts: Iterable[str] = (
        brief.genre,
        brief.audience,
        brief.purpose,
        brief.reader_action,
        brief.length,
        *brief.evidence,
        *brief.constraints,
    )
    return "\n".join(part for part in parts if part and part.strip())


def _is_list_marker(text: str, anchor: Anchor) -> bool:
    if anchor.kind != "number":
        return False
    line_start = text.rfind("\n", 0, anchor.start) + 1
    prefix = text[line_start:anchor.start]
    suffix = text[anchor.end:anchor.end + 3]
    return not prefix.strip() and bool(re.match(r"[.)]\s", suffix))


def _anchors(text: str) -> list[Anchor]:
    masked = _AUTHOR_INPUT.sub(lambda match: " " * len(match.group(0)), text)
    return [
        anchor for anchor in extract_anchors(masked)
        if anchor.kind in _PROTECTED_DRAFT_CATEGORIES and not _is_list_marker(masked, anchor)
    ]


def verify_draft_evidence(brief: WritingBrief, candidate: str) -> EvidenceBoundaryReport:
    """Reject factual anchors that were not supplied in the writing brief.

    Repetition of a supplied anchor is permitted. The function does not certify
    that a draft represents the supplied evidence correctly; it is a lower-bound
    anti-fabrication gate that should be followed by source-aware review for
    high-stakes work.
    """
    supplied = _anchors(_brief_text(brief))
    produced = _anchors(candidate)
    allowed = {(anchor.kind, anchor.normalized) for anchor in supplied}
    violations: list[EvidenceBoundaryViolation] = []
    seen: set[tuple[str, str]] = set()
    for anchor in produced:
        key = (anchor.kind, anchor.normalized)
        if key in allowed or key in seen:
            continue
        seen.add(key)
        violations.append(EvidenceBoundaryViolation(
            category=anchor.kind,
            value=anchor.text,
            message=(
                f"The draft introduced {anchor.kind.replace('_', ' ')} {anchor.text!r}, "
                "which is absent from the supplied brief and evidence boundary."
            ),
        ))
    return EvidenceBoundaryReport(
        passes=not violations,
        supplied_anchor_count=len(supplied),
        candidate_anchor_count=len(produced),
        violations=violations,
    )
