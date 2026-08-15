"""Anti-AI rewriter: turns detector evidence into edit suggestions.

Conservative by design (no-ai-slop principle: minimum effective edit, preserve
voice). Every suggestion carries the evidence span and rationale. Only
substitutions whose trigger is listed in the feature's safe_rewrites are
applied automatically with apply=True; everything else is suggestion-only.
"""
from __future__ import annotations

from dataclasses import dataclass

from .scoring import measure_all
from .skillengine import SkillRegistry
from .textmodel import WritingSample, parse


@dataclass
class Suggestion:
    start: int
    end: int
    original: str
    options: list[str]  # empty string option = delete
    feature_id: str
    family: str
    rationale: str
    safe: bool


def suggest(sample: WritingSample, registry: SkillRegistry) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    for m in measure_all(sample, registry):
        spec = registry.features[m.feature_id]
        if not m.matches:
            continue
        for hit in m.matches:
            key = hit.text.lower().strip().rstrip(",")
            options = (
                spec.rewrites.get(key)
                or spec.rewrites.get(hit.text.lower().strip())
                or []
            )
            if not options and m.contribution <= 0.5:
                continue  # feature not AI-leaning here and nothing to offer
            suggestions.append(Suggestion(
                start=hit.start, end=hit.end, original=hit.text,
                options=options, feature_id=m.feature_id, family=m.family,
                rationale=spec.explanation, safe=key in spec.safe_rewrites,
            ))
    suggestions.sort(key=lambda s: s.start)
    # drop overlapping spans, keep the earliest/longest
    pruned: list[Suggestion] = []
    for s in suggestions:
        if pruned and s.start < pruned[-1].end:
            continue
        pruned.append(s)
    return pruned


def apply_safe(text: str, suggestions: list[Suggestion]) -> tuple[str, int]:
    """Apply only safe, single-option replacements, right to left. Returns (new_text, count)."""
    applied = 0
    out = text
    for s in sorted(suggestions, key=lambda s: s.start, reverse=True):
        if not (s.safe and len(s.options) == 1):
            continue
        replacement = s.options[0]
        if s.original[0].isupper() and replacement:
            replacement = replacement[0].upper() + replacement[1:]
        out = out[: s.start] + replacement + out[s.end :]
        applied += 1
    return out, applied


def clean_text(text: str, registry: SkillRegistry | None = None, apply: bool = False) -> dict:
    registry = registry or SkillRegistry.load()
    sample = parse(text)
    suggestions = suggest(sample, registry)
    result = {
        "suggestions": [
            {
                "span": [s.start, s.end],
                "original": s.original,
                "options": s.options,
                "featureType": s.feature_id,
                "family": s.family,
                "rationale": s.rationale,
                "autoApplicable": s.safe and len(s.options) == 1,
            }
            for s in suggestions
        ],
        "appliedCount": 0,
        "cleanedText": None,
    }
    if apply:
        cleaned, applied = apply_safe(text, suggestions)
        result["cleanedText"] = cleaned
        result["appliedCount"] = applied
    return result
