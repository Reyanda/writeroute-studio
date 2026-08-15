"""Controlled rewriting loop: diagnose → contract → candidate → gate → re-score.

The model does not need to understand the ontology. We diagnose the passage,
compile only the relevant repairs into a bounded revision contract, send it to
any LLM via a callback, and reject the candidate if protected content changed
or the diagnostic burden got worse. Fluency is not evidence of improvement —
the gate is.

Semantic invariants (rejection, not warning): numbers and thresholds, normative
modals (must/shall/should/may/required/prohibited), citations, URLs. A rewrite
of "the contractor must submit 17 records" into "should submit 7 records" is a
substantive change, not a stylistic improvement.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from .rewrite import suggest
from .scoring import scan_text
from .skillengine import SkillRegistry
from .textmodel import parse

# callback contract: (revision_contract, passage) -> revised passage
RevisionCallback = Callable[[str, str], str]

_MODALS = ("must", "shall", "should", "may", "required", "prohibited", "mandatory")
_NUM = re.compile(r"\d+(?:[.,:]\d+)*\s?%?")
_CITATION = re.compile(
    r"\[[0-9,\s–-]+\]"                                  # [1], [2-4]
    r"|\([A-Z][A-Za-z'’-]+(?:\s+et al\.?)?,?\s+\d{4}[a-z]?\)"  # (Smith et al., 2020)
    r"|doi:\s?\S+|https?://\S+",
)
_LENGTH_RATIO_MIN, _LENGTH_RATIO_MAX = 0.5, 1.6
_SCORE_TOLERANCE = 0.02   # candidate may not score worse than original + this
_IMPROVED_MARGIN = 0.01   # below original - this counts as an improvement


def extract_invariants(text: str) -> dict:
    lower = text.lower()
    return {
        "numbers": Counter(n.replace(" ", "") for n in _NUM.findall(text)),
        "modals": {m: len(re.findall(rf"\b{m}\b", lower)) for m in _MODALS},
        "citations": Counter(c.strip() for c in _CITATION.findall(text)),
    }


def preservation_gate(original: str, candidate: str) -> tuple[bool, list[str]]:
    """Return (passes, violations). A violation is a substantive change."""
    violations: list[str] = []
    if not candidate or not candidate.strip():
        return False, ["candidate is empty"]
    ratio = len(candidate.split()) / max(1, len(original.split()))
    if not (_LENGTH_RATIO_MIN <= ratio <= _LENGTH_RATIO_MAX):
        violations.append(
            f"length ratio {ratio:.2f} outside [{_LENGTH_RATIO_MIN}, {_LENGTH_RATIO_MAX}] — "
            "the candidate is a summary or an expansion, not a revision")
    before, after = extract_invariants(original), extract_invariants(candidate)
    if before["numbers"] != after["numbers"]:
        lost = before["numbers"] - after["numbers"]
        added = after["numbers"] - before["numbers"]
        detail = []
        if lost:
            detail.append("removed " + ", ".join(sorted(lost)))
        if added:
            detail.append("introduced " + ", ".join(sorted(added)))
        violations.append("numbers changed: " + "; ".join(detail))
    for modal, count in before["modals"].items():
        if after["modals"][modal] != count:
            violations.append(
                f"normative force changed: '{modal}' appears {count}× in the original "
                f"and {after['modals'][modal]}× in the candidate")
    if before["citations"] != after["citations"]:
        lost = before["citations"] - after["citations"]
        if lost:
            violations.append("citations/URLs removed: " + ", ".join(sorted(lost)))
    return not violations, violations


def compile_contract(text: str, registry: SkillRegistry, genre: str = "") -> tuple[str, dict]:
    """Diagnose the passage and compile only the relevant repairs into a contract."""
    report = scan_text(text, registry, genre=genre)
    sample = parse(text)
    suggestions = suggest(sample, registry)

    defects = []
    for e in report["detectionResult"]["explainedBy"]:
        if e["aiLikenessContribution"] <= 0.55:
            continue
        feature = next(f for f in report["features"] if f["featureType"] == e["featureType"])
        quotes = "; ".join(f'"{h["text"].strip()}"' for h in feature["evidence"][:4])
        line = f"- {e['featureType']}: {e['note']}"
        if quotes:
            line += f" Evidence in this passage: {quotes}."
        defects.append(line)

    replacements = []
    for s in suggestions[:20]:
        if s.options:
            opts = " / ".join(f'"{o}"' if o else "delete" for o in s.options)
            replacements.append(f'- "{s.original}" → {opts}')

    contract = "REVISION CONTRACT\n\nDefects to repair (only these; do not rewrite beyond them):\n"
    contract += "\n".join(defects) if defects else "- none flagged; make no changes"
    if replacements:
        contract += "\n\nSpecific replacements known to be acceptable:\n" + "\n".join(replacements)
    contract += (
        "\n\nInvariants — reject-on-change, do not touch:\n"
        "- every number, threshold, date, percentage, and quantity, verbatim\n"
        "- normative modals: must, shall, should, may, required, prohibited, mandatory — "
        "never substitute one for another\n"
        "- citations, references, URLs, and direct quotations\n"
        "- the author's claims, meaning, and voice; this is a minimum effective edit, "
        "not a rewrite\n"
        "\nReturn ONLY the revised passage. No preamble, no commentary, no markdown fences."
    )
    return contract, report


def diagnostic_score(text: str, registry: SkillRegistry, genre: str = "") -> float:
    return scan_text(text, registry, genre=genre)["detectionResult"]["globalAiProbability"]


@dataclass
class Iteration:
    accepted: bool
    score_before: float
    score_after: float | None
    violations: list[str] = field(default_factory=list)
    reason: str = ""


def improve_text(text: str, callback: RevisionCallback,
                 registry: SkillRegistry | None = None, genre: str = "",
                 max_iterations: int = 3) -> dict:
    """Run the controlled loop. Returns the best accepted text, never a worse one."""
    registry = registry or SkillRegistry.load()
    current = text
    current_score = diagnostic_score(current, registry, genre)
    iterations: list[Iteration] = []

    for _ in range(max_iterations):
        contract, _report = compile_contract(current, registry, genre)
        candidate = callback(contract, current)
        passes, violations = preservation_gate(current, candidate)
        if not passes:
            iterations.append(Iteration(False, current_score, None, violations,
                                        "preservation gate rejected the candidate"))
            continue
        candidate_score = diagnostic_score(candidate, registry, genre)
        if candidate_score > current_score + _SCORE_TOLERANCE:
            iterations.append(Iteration(False, current_score, candidate_score, [],
                                        "diagnostic burden worsened"))
            continue
        improved = candidate_score < current_score - _IMPROVED_MARGIN
        iterations.append(Iteration(True, current_score, candidate_score, [],
                                    "accepted" + ("" if improved else " (no further improvement)")))
        current, current_score = candidate, candidate_score
        if not improved:
            break

    return {
        "finalText": current,
        "changed": current != text,
        "scoreBefore": round(diagnostic_score(text, registry, genre), 3),
        "scoreAfter": round(current_score, 3),
        "iterations": [
            {"accepted": it.accepted, "scoreBefore": round(it.score_before, 3),
             "scoreAfter": None if it.score_after is None else round(it.score_after, 3),
             "violations": it.violations, "reason": it.reason}
            for it in iterations
        ],
    }
