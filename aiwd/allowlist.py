"""Domain allow-lists.

A generic slop lexicon cannot tell "doubly robust", the name of an estimator,
from "robust", the padding adjective. Scoring the first as a buzzword inflates a
document's score for using its field's vocabulary correctly, and an inflated
score on a technical manuscript is exactly the input that leads a reader to the
wrong conclusion about who wrote it.

An allow-list entry suppresses a feature hit that falls inside a named span. It
is scoped to the features it applies to, so exempting "doubly robust" from
BuzzwordDensity leaves a bare "robust" elsewhere in the same sentence still
counted. Every suppressed hit is returned and reported as an exemption; nothing
is dropped silently, because a reader has to be able to see what was excused and
disagree with it.

Built-in lists live in ``aiwd/data/allowlists``. User lists live in
``~/.aiwd/allowlists`` and are loaded after, so a project can add its own terms
without editing the package.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_ALLOWLIST_DIR = Path(__file__).parent / "data" / "allowlists"
USER_ALLOWLIST_DIR = Path.home() / ".aiwd" / "allowlists"


@dataclass
class AllowEntry:
    id: str
    pattern: str
    applies_to: list[str]
    reason: str
    source: str
    flags: str = ""
    guards: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()

    def regex(self) -> re.Pattern:
        return re.compile(self.pattern, re.IGNORECASE if "i" in self.flags else 0)

    def covers(self, feature_id: str) -> bool:
        return "*" in self.applies_to or feature_id in self.applies_to

    def applies_in_genre(self, genre: str) -> bool:
        return not self.genres or not genre or genre in self.genres

    def guard_hit(self, context: str) -> bool:
        """True when the surrounding sentence carries the exempting evidence.

        Span nesting cannot express "this sentence is about finance, so
        'leverage' is not a buzzword". A guard is a second regex tested against
        the context, so the flagged word and the evidence exempting it need not
        be the same text.
        """
        flags = re.IGNORECASE if "i" in self.flags else 0
        return any(re.search(g, context, flags) for g in self.guards)


@dataclass
class Exemption:
    """A hit that a named entry excused, kept for the report."""
    feature_id: str
    entry_id: str
    text: str
    reason: str


@dataclass
class AllowList:
    entries: list[AllowEntry] = field(default_factory=list)

    def spans_for(self, feature_id: str, text: str,
                  genre: str = "") -> list[tuple[int, int, AllowEntry]]:
        spans: list[tuple[int, int, AllowEntry]] = []
        for entry in self.entries:
            if not entry.covers(feature_id) or not entry.applies_in_genre(genre):
                continue
            for m in entry.regex().finditer(text):
                spans.append((m.start(), m.end(), entry))
        return spans

    def filter_matches(self, feature_id: str, text: str, matches: list,
                       genre: str = "", context_window: int = 220):
        """Split matches into (kept, exemptions).

        Containment is bidirectional. A single-word hit sits inside its phrase
        ("robust" inside "doubly robust"), but a feature regex can also capture
        more than the allow-listed phrase: the tricolon pattern takes the verb
        before the list, so "Kenya, Nigeria, and Zambia" sits inside the match
        rather than the other way round. Either nesting is the same finding, so
        both exempt. Partial overlaps do not, because a phrase clipping the edge
        of an unrelated match is not evidence about that match.
        """
        spans = self.spans_for(feature_id, text, genre)
        if not spans:
            return matches, []
        kept, exempt = [], []
        for match in matches:
            hit = None
            for start, end, entry in spans:
                inside = start <= match.start and match.end <= end
                around = match.start <= start and end <= match.end
                if not (inside or around):
                    continue
                # A guarded entry exempts only when the surrounding sentence also
                # carries the exempting evidence. Without this the entry would
                # excuse every occurrence of its own pattern, which is the whole
                # word rather than the technical sense of it.
                if entry.guards:
                    if not entry.guard_hit(_sentence_around(text, match.start,
                                                            match.end, context_window)):
                        continue
                hit = entry
                break
            if hit is None:
                kept.append(match)
            else:
                exempt.append(Exemption(feature_id, hit.id, match.text, hit.reason))
        return kept, exempt

    @property
    def sources(self) -> list[str]:
        return sorted({e.source for e in self.entries})


# A bare "." is not a sentence boundary. Splitting on one truncates the context at
# the decimal point of "8.7%", at "et al." and at "e.g.", so a guard looking for the
# evidence that excuses a hit goes blind exactly where the evidence is most likely to
# be: in prose carrying numbers and citations. Require terminal punctuation followed
# by whitespace and an opening character.
_BOUNDARY = re.compile(r"[.!?][\"\'\u201d\u2019)\]]*\s+(?=[A-Z\"\'\u201c(\[])")


def _sentence_around(text: str, start: int, end: int, window: int) -> str:
    """The sentence the hit sits in, clipped to a window.

    A guard says "this sentence is about regression diagnostics". Letting it read
    two sentences either side would exempt a bare buzzword because something
    technical happened nearby, which is the over-exemption this mechanism exists
    to avoid.
    """
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    left = max((m.end() for m in _BOUNDARY.finditer(text, lo, start)), default=-1)
    right_m = _BOUNDARY.search(text, end, hi)
    right = right_m.start() + 1 if right_m else -1
    return text[left if left != -1 else lo: right if right != -1 else hi]


def _load_dir(directory: Path) -> list[AllowEntry]:
    out: list[AllowEntry] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        listed = doc.get("id", path.stem)
        for raw in doc.get("entries", []):
            pattern = raw.get("pattern")
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error:
                continue
            out.append(
                AllowEntry(
                    id=raw.get("id", "unnamed"),
                    pattern=pattern,
                    applies_to=raw.get("applies_to", []),
                    reason=raw.get("reason", ""),
                    source=listed,
                    flags=raw.get("flags", ""),
                    guards=tuple(raw.get("guards", [])),
                    genres=tuple(raw.get("genres", [])),
                )
            )
    return out


def load_allowlist(enabled: bool = True) -> AllowList:
    if not enabled:
        return AllowList([])
    return AllowList(_load_dir(BUILTIN_ALLOWLIST_DIR) + _load_dir(USER_ALLOWLIST_DIR))
