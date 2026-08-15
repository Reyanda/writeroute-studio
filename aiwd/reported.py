"""Reported voice: text the author is quoting, not text the author is asserting.

A detector measures how a writer writes. Quoted and attributed language is
someone else's writing carried into the document, so scoring it as the author's
machine-likeness measures the wrong person. A systematic review quoting an
included study, a response to reviewers quoting a reviewer, a discussion quoting
a guideline, and a report quoting a supervisor's suggested wording all inflate a
score for doing the honest thing.

Discounted hits are returned and reported, never dropped silently, for the same
reason the allow-list reports its exemptions: the reader has to be able to see
what was excused and disagree with it.

This is deliberately conservative. Scare quotes around a single word are not
reported speech, so quoted runs must clear a minimum length. Single quotes are
only trusted when they cannot be apostrophes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Verbs that introduce someone else's words. "suggests" and "indicates" are
# omitted: in scientific prose "the evidence suggests" is the author asserting,
# not the author quoting.
_ATTRIBUTION = (
    r"(?:said|says|saying|wrote|writes|written|argued|argues|noted|notes|observed|"
    r"observes|commented|comments|remarked|remarks|stated|states|asked|asks|"
    r"replied|replies|responded|responds|put it|described it as|"
    r"in (?:his|her|their|its) words)"
)

_PATTERNS: tuple[tuple[str, str, int], ...] = (
    # Straight double quotes. Minimum run length keeps scare quotes out.
    ("quotation", r'"[^"\n]{12,800}"', 0),
    # Guillemets and any curly quotes the caller did not flatten.
    ("quotation", r"[“][^”\n]{12,800}[”]", 0),
    ("quotation", r"[«][^»\n]{12,800}[»]", 0),
    # Single quotes only when both delimiters sit at a word edge, so possessives
    # and contractions cannot open or close a span.
    ("quotation", r"(?<![A-Za-z])'[^'\n]{15,800}'(?![A-Za-z])", 0),
    # Markdown blockquote, whole line.
    ("blockquote", r"^[ \t]*>[ \t]?.*$", re.MULTILINE),
    # Attribution verb followed by a that-clause or colon, to the sentence end.
    ("attribution", rf"\b{_ATTRIBUTION}\s*(?:that\b|:)[^.!?\n]{{10,400}}", re.IGNORECASE),
    # Leading "According to X," through the end of that sentence.
    ("attribution", r"\bAccording to [^,.\n]{2,80},[^.!?\n]{10,400}", re.IGNORECASE),
)


@dataclass
class ReportedSpan:
    start: int
    end: int
    kind: str


@dataclass
class Discount:
    """A hit excused because it sits in someone else's words."""
    feature_id: str
    kind: str
    text: str


def reported_spans(text: str) -> list[ReportedSpan]:
    """Character spans of quoted, block-quoted or attributed material.

    Overlapping spans are merged so a quote inside an attribution counts once.
    """
    raw: list[ReportedSpan] = []
    for kind, pattern, flags in _PATTERNS:
        for m in re.finditer(pattern, text, flags):
            if m.end() > m.start():
                raw.append(ReportedSpan(m.start(), m.end(), kind))
    if not raw:
        return []
    raw.sort(key=lambda s: (s.start, -s.end))
    merged = [raw[0]]
    for span in raw[1:]:
        last = merged[-1]
        if span.start <= last.end:
            if span.end > last.end:
                kind = last.kind if last.kind == span.kind else "mixed"
                merged[-1] = ReportedSpan(last.start, span.end, kind)
        else:
            merged.append(span)
    return merged


def reported_fraction(text: str, spans: list[ReportedSpan] | None = None) -> float:
    """Share of the document that is someone else's words, 0..1."""
    if not text:
        return 0.0
    spans = reported_spans(text) if spans is None else spans
    return min(1.0, sum(s.end - s.start for s in spans) / len(text))


def filter_matches(feature_id: str, matches: list, spans: list[ReportedSpan]):
    """Split matches into (kept, discounted).

    A hit counts as reported only when it sits wholly inside a reported span. A
    hit straddling the boundary is partly the author's own sentence, and excusing
    it would let one quotation launder the prose around it.
    """
    if not spans or not matches:
        return matches, []
    kept, discounted = [], []
    for match in matches:
        span = next((s for s in spans
                     if s.start <= match.start and match.end <= s.end), None)
        if span is None:
            kept.append(match)
        else:
            discounted.append(Discount(feature_id, span.kind, match.text))
    return kept, discounted
