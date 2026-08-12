"""Declarative surface-pattern scanner with genre and context guards."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .genres import GenreProfile
from .model import Document

PATTERN_DIR = Path(__file__).parent / "data" / "patterns"


@dataclass(frozen=True)
class PatternSpec:
    id: str
    title: str
    category: str
    severity: str
    confidence: float
    pattern: str
    rationale: str
    action: str
    strategy: str = "review"
    replacements: dict[str, str] = field(default_factory=dict)
    literal_guards: tuple[str, ...] = ()
    genre_soft: frozenset[str] = frozenset()
    genre_exempt: frozenset[str] = frozenset()
    source: str = "writeroute"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatternSpec":
        return cls(
            id=data["id"],
            title=data["title"],
            category=data["category"],
            severity=data.get("severity", "soft"),
            confidence=float(data.get("confidence", 0.8)),
            pattern=data["pattern"],
            rationale=data.get("rationale", ""),
            action=data.get("action", "Review the passage."),
            strategy=data.get("strategy", "review"),
            replacements={k.casefold(): v for k, v in data.get("replacements", {}).items()},
            literal_guards=tuple(data.get("literalGuards", [])),
            genre_soft=frozenset(data.get("genreSoft", [])),
            genre_exempt=frozenset(data.get("genreExempt", [])),
            source=data.get("source", "writeroute"),
        )


@dataclass(frozen=True)
class PatternHit:
    spec: PatternSpec
    start: int
    end: int
    text: str
    severity: str
    confidence: float
    groups: dict[str, str]
    reported_voice: bool = False


@lru_cache(maxsize=1)
def load_patterns() -> tuple[PatternSpec, ...]:
    specs: list[PatternSpec] = []
    for path in sorted(PATTERN_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("patterns", []):
            specs.append(PatternSpec.from_dict(row))
    ids = [spec.id for spec in specs]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"Duplicate pattern ids: {', '.join(duplicates)}")
    return tuple(specs)


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _guarded(spec: PatternSpec, context: str) -> bool:
    return any(re.search(guard, context) for guard in spec.literal_guards)


def _resolve_severity(spec: PatternSpec, genre: GenreProfile, reported: bool) -> str:
    severity = spec.severity
    if spec.id in genre.hard_patterns:
        severity = "hard"
    if spec.id in genre.soft_patterns or genre.id in spec.genre_soft:
        severity = "soft"
    # Source-reported language may still be clumsy, but an automated editor must not
    # silently treat a quoted or attributed claim as the current author's assertion.
    if reported and severity == "hard":
        severity = "review"
    return severity


def scan_patterns(document: Document, genre: GenreProfile) -> list[PatternHit]:
    text = document.masked_text
    hits: list[PatternHit] = []
    for spec in load_patterns():
        if spec.id in genre.disabled_patterns or genre.id in spec.genre_exempt:
            continue
        regex = _compiled(spec.pattern)
        for match in regex.finditer(text):
            start, end = match.span()
            if start == end or document.is_protected(start, end):
                continue
            sentence = document.sentence_for_span(start, end)
            context = sentence.text if sentence else document.text[max(0, start - 100): min(len(text), end + 100)]
            if _guarded(spec, context):
                continue
            reported = document.is_reported_voice(start, end)
            groups = {k: v for k, v in match.groupdict().items() if v is not None}
            hits.append(PatternHit(
                spec=spec,
                start=start,
                end=end,
                text=document.text[start:end],
                severity=_resolve_severity(spec, genre, reported),
                confidence=max(0.35, spec.confidence - (0.20 if reported else 0.0)),
                groups=groups,
                reported_voice=reported,
            ))
    # Keep the most specific hit when two patterns cover the same span. Different
    # categories may overlap and remain useful, but exact duplicate spans should not
    # create a noisy review queue.
    hits.sort(key=lambda h: (h.start, -(h.end - h.start), -h.confidence, h.spec.id))
    output: list[PatternHit] = []
    seen: set[tuple[int, int, str]] = set()
    for hit in hits:
        key = (hit.start, hit.end, hit.spec.category)
        if key in seen:
            continue
        seen.add(key)
        output.append(hit)
    return output


def pattern_catalogue() -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "title": spec.title,
            "category": spec.category,
            "severity": spec.severity,
            "confidence": spec.confidence,
            "strategy": spec.strategy,
            "genreSoft": sorted(spec.genre_soft),
            "genreExempt": sorted(spec.genre_exempt),
        }
        for spec in load_patterns()
    ]
