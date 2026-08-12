"""Meaning-preservation gates for editing and rewriting.

WriteRoute does not treat lower slop burden as proof that a revision is better.
A candidate first has to preserve facts, force, polarity, scope, attribution and
technical anchors. The gate is deterministic, explainable and genre-sensitive.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Anchor:
    kind: str
    text: str
    normalized: str
    start: int
    end: int


@dataclass(frozen=True)
class IntegrityPolicy:
    name: str
    hard_categories: frozenset[str]
    soft_categories: frozenset[str] = frozenset()
    min_length_ratio: float = 0.55
    max_length_ratio: float = 1.65
    exact_modals: bool = False
    preserve_headings: bool = False


@dataclass
class IntegrityViolation:
    category: str
    severity: str
    message: str
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "removed": self.removed,
            "added": self.added,
        }


@dataclass
class IntegrityReport:
    passes: bool
    policy: str
    length_ratio: float
    violations: list[IntegrityViolation]
    anchor_counts_before: dict[str, int]
    anchor_counts_after: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "passes": self.passes,
            "policy": self.policy,
            "lengthRatio": round(self.length_ratio, 3),
            "violations": [v.to_dict() for v in self.violations],
            "anchorCountsBefore": self.anchor_counts_before,
            "anchorCountsAfter": self.anchor_counts_after,
        }


# Ordered from most specific to broadest. Overlap across categories is deliberate:
# a p-value is both a statistical anchor and a number, and losing either is unsafe.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("url", re.compile(r"https?://[^\s\])}>,'\"]+", re.IGNORECASE)),
    ("doi", re.compile(r"\b(?:doi\s*:\s*)?10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)),
    ("citation", re.compile(
        r"\[[0-9,;\s–—-]+\]"
        r"|\([A-Z][A-Za-z'’.-]+(?:\s+et\s+al\.?)?,?\s+(?:19|20)\d{2}[a-z]?\)"
        r"|\b[A-Z][A-Za-z'’.-]+\s+et\s+al\.?,?\s+(?:19|20)\d{2}[a-z]?\b"
    )),
    ("statistic", re.compile(
        r"\b(?:p\s*[<=>]\s*0?\.\d+|(?:90|95|99)\s*%\s*CI\s*[:=]?\s*"
        r"[-−]?\d+(?:\.\d+)?\s*(?:[-–—,]|to)\s*[-−]?\d+(?:\.\d+)?|"
        r"(?:OR|RR|HR|IRR|ARR|NNT|AUC|R\^?2|β|beta|SE|SD)\s*[=:]?\s*"
        r"[-−]?\d+(?:\.\d+)?)\b",
        re.IGNORECASE,
    )),
    ("cross_reference", re.compile(
        r"\b(?:Section|Article|Clause|Paragraph|Figure|Fig\.?|Table|Appendix|Annex|"
        r"Schedule|Chapter)\s+[A-Z0-9]+(?:[.:-][A-Z0-9]+|\([a-z0-9]+\))*\b",
        re.IGNORECASE,
    )),
    ("date", re.compile(
        r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b"
        r"|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(?:19|20)\d{2}\b"
        r"|\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
        re.IGNORECASE,
    )),
    ("number", re.compile(
        r"(?<![A-Za-z0-9_])(?:[$€£¥]\s*)?[-−+]?\d+(?:[.,:]\d+)*(?:\s*[-–—]\s*"
        r"[-−+]?\d+(?:[.,:]\d+)*)?\s*(?:%|pp|percentage points?|per\s+cent|"
        r"percent|million|billion|trillion|thousand|hundred|kg|g|mg|µg|mcg|km|"
        r"cm|mm|mL|ml|L|years?|months?|weeks?|days?|hours?|minutes?|seconds?|"
        r"USD|EUR|GBP|MW|kW|V|mV|Hz|kHz|MHz|GHz|°C|°F|CI)?(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )),
    ("quotation", re.compile(r"“[^”\n]+”|‘[^’\n]+’|\"[^\"\n]+\"")),
    ("inline_code", re.compile(r"`[^`\n]+`")),
    ("cli_flag", re.compile(r"(?<!\w)--?[a-zA-Z][\w-]*")),
    ("file_path", re.compile(
        r"(?<!\w)(?:[A-Za-z]:\\|/|\.\.?/)[A-Za-z0-9_./\\-]+|"
        r"\b[A-Za-z0-9_.-]+\.(?:py|r|R|js|ts|tsx|jsx|json|ya?ml|toml|csv|tsv|"
        r"md|txt|pdf|docx|xlsx|pptx|sql|sh|bash|html|css)\b"
    )),
    ("acronym", re.compile(r"\b[A-Z][A-Z0-9-]{1,}\b")),
    ("named_entity", re.compile(
        r"\b(?:[A-Z][A-Za-z'’.-]+(?:\s+|$)){2,5}", re.MULTILINE
    )),
    ("heading", re.compile(r"(?m)^\s*#{1,6}\s+.+$")),
]

_MODAL_GROUPS: dict[str, tuple[str, ...]] = {
    "obligation": ("must", "shall", "required", "mandatory", "is required to", "are required to"),
    "recommendation": ("should", "recommended", "is advised to", "are advised to"),
    "permission": ("may", "permitted", "allowed"),
    "possibility": ("might", "could", "possible", "possibly"),
    "ability": ("can", "able to", "has the ability to", "is able to"),
    "prohibition": ("must not", "shall not", "may not", "prohibited", "forbidden"),
}

_NEGATION_GROUPS: dict[str, tuple[str, ...]] = {
    "not": ("not", "cannot", "can't", "isn't", "aren't", "wasn't", "weren't", "doesn't", "don't", "didn't"),
    "never": ("never",),
    "without": ("without",),
    "unless": ("unless",),
    "except": ("except", "excepting", "with the exception of"),
    "neither_nor": ("neither", "nor"),
}

_SCOPE_GROUPS: dict[str, tuple[str, ...]] = {
    "only": ("only", "solely", "exclusively"),
    "all": ("all", "every", "each"),
    "any": ("any",),
    "none": ("none", "no one", "nothing"),
    "some": ("some", "several"),
    "most": ("most", "majority"),
    "and_or": ("and/or",),
    "at_least": ("at least", "no fewer than"),
    "at_most": ("at most", "no more than"),
}

_DIRECTION_GROUPS: dict[str, tuple[str, ...]] = {
    "increase": ("increase", "increased", "increases", "higher", "more", "rise", "rose", "growth", "improved"),
    "decrease": ("decrease", "decreased", "decreases", "lower", "less", "decline", "fell", "reduced", "worsened"),
    "before": ("before", "prior to", "earlier than"),
    "after": ("after", "following", "later than"),
}

_CAUSAL_GROUPS: dict[str, tuple[str, ...]] = {
    "causal": ("cause", "causes", "caused", "causing", "led to", "leads to", "resulted in", "results in", "drove", "drives", "produced"),
    "associational": ("associated with", "linked to", "correlated with", "related to", "predicted", "predicts"),
    "suggestive": ("suggests", "suggested", "consistent with", "may reflect", "could reflect"),
}


_DEFAULT_HARD = frozenset({
    "url", "doi", "citation", "statistic", "cross_reference", "date", "number",
    "quotation", "modal_force", "negation", "scope", "direction", "causal_strength",
})

POLICIES: dict[str, IntegrityPolicy] = {
    "general": IntegrityPolicy(
        name="general",
        hard_categories=_DEFAULT_HARD,
        soft_categories=frozenset({"acronym", "named_entity", "inline_code", "cli_flag", "file_path"}),
    ),
    "scientific": IntegrityPolicy(
        name="scientific",
        hard_categories=_DEFAULT_HARD | frozenset({"acronym", "named_entity"}),
        soft_categories=frozenset({"heading"}),
        min_length_ratio=0.65,
    ),
    "systematic-review": IntegrityPolicy(
        name="systematic-review",
        hard_categories=_DEFAULT_HARD | frozenset({"acronym", "named_entity", "heading"}),
        min_length_ratio=0.65,
        preserve_headings=True,
    ),
    "policy-brief": IntegrityPolicy(
        name="policy-brief",
        hard_categories=_DEFAULT_HARD | frozenset({"named_entity", "acronym"}),
        soft_categories=frozenset({"heading"}),
        min_length_ratio=0.60,
    ),
    "professional-report": IntegrityPolicy(
        name="professional-report",
        hard_categories=_DEFAULT_HARD | frozenset({"named_entity", "acronym"}),
        soft_categories=frozenset({"heading"}),
        min_length_ratio=0.60,
    ),
    "grant": IntegrityPolicy(
        name="grant",
        hard_categories=_DEFAULT_HARD | frozenset({"named_entity", "acronym", "heading"}),
        min_length_ratio=0.65,
        preserve_headings=True,
    ),
    "legal": IntegrityPolicy(
        name="legal",
        hard_categories=_DEFAULT_HARD | frozenset({
            "acronym", "named_entity", "inline_code", "cli_flag", "file_path", "heading"
        }),
        min_length_ratio=0.75,
        max_length_ratio=1.35,
        exact_modals=True,
        preserve_headings=True,
    ),
    "technical": IntegrityPolicy(
        name="technical",
        hard_categories=_DEFAULT_HARD | frozenset({
            "acronym", "named_entity", "inline_code", "cli_flag", "file_path", "heading"
        }),
        min_length_ratio=0.65,
        preserve_headings=True,
    ),
    "email": IntegrityPolicy(
        name="email",
        hard_categories=_DEFAULT_HARD | frozenset({"named_entity"}),
        soft_categories=frozenset({"acronym"}),
        min_length_ratio=0.45,
        max_length_ratio=1.8,
    ),
    "op-ed": IntegrityPolicy(
        name="op-ed",
        hard_categories=_DEFAULT_HARD,
        soft_categories=frozenset({"named_entity", "acronym"}),
        min_length_ratio=0.50,
    ),
}


def get_policy(name: str | None) -> IntegrityPolicy:
    key = (name or "general").lower().strip()
    return POLICIES.get(key, POLICIES["general"])


def _normalise(kind: str, value: str) -> str:
    value = value.strip()
    value = value.replace("−", "-").replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    if kind in {"url", "doi", "file_path", "inline_code", "cli_flag", "quotation", "heading"}:
        return value
    if kind == "number":
        return re.sub(r"\s+", "", value).lower()
    if kind == "named_entity":
        return value.rstrip().casefold()
    return value.casefold()


def extract_anchors(text: str) -> list[Anchor]:
    anchors: list[Anchor] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0)
            # Avoid classifying a bare sentence-initial word followed by a newline as
            # a multiword entity because the regex can include trailing whitespace.
            if kind == "named_entity":
                raw = raw.strip()
                if len(raw.split()) < 2:
                    continue
            anchors.append(Anchor(kind, raw, _normalise(kind, raw), match.start(), match.start() + len(raw)))
    return anchors


def _phrase_counter(text: str, groups: dict[str, tuple[str, ...]]) -> Counter[str]:
    lower = text.casefold().replace("’", "'")
    counts: Counter[str] = Counter()
    # Longest first prevents "may" inside "may not" from being counted twice.
    occupied: list[tuple[int, int]] = []
    entries = sorted(
        ((label, phrase) for label, phrases in groups.items() for phrase in phrases),
        key=lambda x: len(x[1]), reverse=True,
    )
    for label, phrase in entries:
        pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)")
        for match in pattern.finditer(lower):
            if any(a < match.end() and match.start() < b for a, b in occupied):
                continue
            occupied.append((match.start(), match.end()))
            counts[label] += 1
    return counts


def _anchor_counters(text: str, policy: IntegrityPolicy) -> dict[str, Counter[str]]:
    counters: dict[str, Counter[str]] = {}
    for anchor in extract_anchors(text):
        if anchor.kind == "heading" and not policy.preserve_headings:
            continue
        counters.setdefault(anchor.kind, Counter())[anchor.normalized] += 1
    counters["modal_force"] = _phrase_counter(text, _MODAL_GROUPS)
    counters["negation"] = _phrase_counter(text, _NEGATION_GROUPS)
    counters["scope"] = _phrase_counter(text, _SCOPE_GROUPS)
    counters["direction"] = _phrase_counter(text, _DIRECTION_GROUPS)
    counters["causal_strength"] = _phrase_counter(text, _CAUSAL_GROUPS)
    if policy.exact_modals:
        exact = {
            phrase: (phrase,)
            for phrases in _MODAL_GROUPS.values()
            for phrase in phrases
        }
        counters["exact_modals"] = _phrase_counter(text, exact)
    return counters


def _format_counter(counter: Counter[str], limit: int = 12) -> list[str]:
    values: list[str] = []
    for value, count in counter.most_common(limit):
        values.append(value if count == 1 else f"{value} ×{count}")
    return values


def _compare_category(
    category: str,
    before: Counter[str],
    after: Counter[str],
    policy: IntegrityPolicy,
) -> IntegrityViolation | None:
    if before == after:
        return None
    removed = before - after
    added = after - before
    severity = "hard" if category in policy.hard_categories or category == "exact_modals" else "soft"
    if category not in policy.hard_categories and category not in policy.soft_categories and category != "exact_modals":
        return None
    details: list[str] = []
    if removed:
        details.append("removed or changed " + ", ".join(_format_counter(removed)))
    if added:
        details.append("introduced " + ", ".join(_format_counter(added)))
    return IntegrityViolation(
        category=category,
        severity=severity,
        message=f"{category.replace('_', ' ')} changed: " + "; ".join(details),
        removed=_format_counter(removed),
        added=_format_counter(added),
    )


def verify_integrity(
    original: str,
    candidate: str,
    policy: str | IntegrityPolicy | None = None,
) -> IntegrityReport:
    selected = policy if isinstance(policy, IntegrityPolicy) else get_policy(policy)
    violations: list[IntegrityViolation] = []
    if not candidate or not candidate.strip():
        violations.append(IntegrityViolation("empty", "hard", "candidate is empty"))
        return IntegrityReport(False, selected.name, 0.0, violations, {}, {})

    ratio = len(candidate.split()) / max(1, len(original.split()))
    if not (selected.min_length_ratio <= ratio <= selected.max_length_ratio):
        violations.append(IntegrityViolation(
            "length",
            "hard",
            f"word-count ratio {ratio:.2f} is outside "
            f"[{selected.min_length_ratio:.2f}, {selected.max_length_ratio:.2f}]; "
            "the candidate is likely a summary or an expansion rather than an edit",
        ))

    before = _anchor_counters(original, selected)
    after = _anchor_counters(candidate, selected)
    categories = sorted(set(before) | set(after))
    for category in categories:
        violation = _compare_category(category, before.get(category, Counter()), after.get(category, Counter()), selected)
        if violation:
            violations.append(violation)

    hard = any(v.severity == "hard" for v in violations)
    return IntegrityReport(
        passes=not hard,
        policy=selected.name,
        length_ratio=ratio,
        violations=violations,
        anchor_counts_before={k: sum(v.values()) for k, v in before.items()},
        anchor_counts_after={k: sum(v.values()) for k, v in after.items()},
    )


def protected_terms(text: str, policy: str | IntegrityPolicy | None = None) -> list[str]:
    selected = policy if isinstance(policy, IntegrityPolicy) else get_policy(policy)
    counters = _anchor_counters(text, selected)
    out: list[str] = []
    for category in sorted(selected.hard_categories):
        out.extend(counters.get(category, Counter()).keys())
    return sorted(set(out), key=lambda x: (x.casefold(), x))
