"""Domain allow-list for the claim-support checks.

The substance layer reads a word and asks whether the document earned the claim it makes.
On clinical and epidemiological prose it was wrong most of the time, because the words it
watches — improved, safe, reduced, increased, results in — are also the field's category
labels, its checklist wording and its conditional treatment instructions. Measured on the
benchmark corpus, its hard findings on published journal prose were roughly one in three
correct.

An allow-list is the right instrument here rather than weaker patterns. The patterns are
not wrong about the words; they are missing the context that tells you the word is a label.
Weakening them would lose the true positives too.

Two rules govern every entry:

* it must be written against a named false positive, quoted in the entry's `evidence`;
* it must be narrow enough that the same word still fires when it really is a claim.
  "improved water source" is excused. "improved survival" is not.

Exemptions are reported, never silent. `AuditReport.metrics["allowListExemptions"]` lists
what was excused and why, so a reader can see what the tool decided not to tell them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "allowlists"


@dataclass(frozen=True)
class Exemption:
    """One excused finding, kept for the report."""
    entry_id: str
    pattern_id: str
    reason: str
    text: str


@dataclass(frozen=True)
class Entry:
    id: str
    applies_to: frozenset[str]
    reason: str
    context: tuple[re.Pattern[str], ...]
    flagged_term: re.Pattern[str] | None = None
    genres: frozenset[str] = field(default_factory=frozenset)
    # "span": the context match must cover the flagged word — the rule is that this word
    # is part of a fixed label. "sentence": the match may sit anywhere in the sentence —
    # the rule is about what the sentence as a whole contains, such as whether it reports
    # a measurement. Defaulting to "span" keeps entries narrow unless they say otherwise.
    scope: str = "span"

    def covers(self, pattern_id: str, genre_id: str) -> bool:
        if pattern_id not in self.applies_to:
            return False
        return not self.genres or genre_id in self.genres

    def matches(self, flagged: str, context: str, offset: int) -> bool:
        """`offset` is where the flagged span begins inside `context`.

        A context pattern only excuses the finding if its own match *covers* that offset.
        Searching the sentence as a whole was wrong in a way a test caught: "Improved
        water sources improved survival in this cohort" contains a JMP label and a real
        causal claim, and a sentence-wide match excused both. Requiring the label to
        overlap the flagged word keeps the first "Improved" excused and leaves the second
        one flagged.
        """
        if self.flagged_term and not self.flagged_term.search(flagged.strip()):
            return False
        if self.scope == "sentence":
            return any(p.search(context) for p in self.context)
        end = offset + max(1, len(flagged))
        for pattern in self.context:
            for match in pattern.finditer(context):
                if match.start() < end and offset < match.end():
                    return True
        return False


def _compile(entry: dict) -> Entry:
    return Entry(
        id=entry["id"],
        applies_to=frozenset(entry["appliesTo"]),
        reason=entry["reason"],
        context=tuple(re.compile(p, re.IGNORECASE) for p in entry["contextPatterns"]),
        flagged_term=(re.compile(entry["flaggedTerm"], re.IGNORECASE)
                      if entry.get("flaggedTerm") else None),
        genres=frozenset(entry.get("genres", ())),
        scope=entry.get("scope", "span"),
    )


@lru_cache(maxsize=1)
def load_entries() -> tuple[Entry, ...]:
    entries: list[Entry] = []
    for path in sorted(DATA.glob("*.json")):
        payload = json.loads(path.read_text())
        entries.extend(_compile(e) for e in payload.get("entries", ()))
    return tuple(entries)


def find_exemption(pattern_id: str, genre_id: str, flagged: str,
                   context: str, offset: int) -> Entry | None:
    """The first entry that excuses this finding, or None.

    `context` is the sentence the span sits in — a sentence rather than the whole document,
    because "improved" three paragraphs away from "water source" says nothing about this
    occurrence. `offset` locates the flagged span inside that sentence so an entry can only
    excuse the words it actually covers.
    """
    if not flagged.strip():
        return None
    for entry in load_entries():
        if entry.covers(pattern_id, genre_id) and entry.matches(flagged, context, offset):
            return entry
    return None


def summarise(exemptions: list[Exemption]) -> list[dict]:
    """Group for the report: one row per entry and pattern, with up to three examples."""
    rows: dict[tuple[str, str], dict] = {}
    for ex in exemptions:
        key = (ex.entry_id, ex.pattern_id)
        row = rows.setdefault(key, {
            "allowListEntry": ex.entry_id,
            "patternId": ex.pattern_id,
            "reason": ex.reason,
            "count": 0,
            "examples": [],
        })
        row["count"] += 1
        text = ex.text.strip()
        if len(row["examples"]) < 3 and text not in row["examples"]:
            row["examples"].append(text)
    return sorted(rows.values(), key=lambda r: -r["count"])
