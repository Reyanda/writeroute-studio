from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import FactLedger, Issue, RevisionProposal, Severity, TextAnchor

EM_DASHES = {"\u2014", "\u2015"}
EM_DASH_RE = re.compile(r"[\u2014\u2015]")
DASH_SURROUNDED_RE = re.compile(r"[ \t]*[\u2014\u2015][ \t]*")
TOKEN_RE = re.compile(r"\b[\w][\w'’\-]*\b", re.UNICODE)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?(?:\s*(?:%|percentage points?|pp|fold))?",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(?:18|19|20|21)\d{2}\b")
PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
EFFECT_RE = re.compile(
    r"\b(?:RR|OR|HR|PR|IRR|MD|SMD|NNT)\s*[=:]?\s*[-+]?\d+(?:[.,]\d+)?\b",
    re.IGNORECASE,
)
STATISTICAL_EXPRESSION_RE = re.compile(
    r"(?:\b(?:p|q)\s*(?:=|<|>|≤|≥)\s*(?:0(?:[.,]\d+)?|1(?:[.,]0+)?)\b|"
    r"\b(?:90|95|99)\s*%?\s*(?:CI|CrI)\s*[:=]?\s*[-+]?\d+(?:[.,]\d+)?\s*(?:to|[-,])\s*[-+]?\d+(?:[.,]\d+)?\b|"
    r"\b(?:IQR|SD|SE)\s*[:=]\s*[-+]?\d+(?:[.,]\d+)?(?:\s*(?:to|[-,])\s*[-+]?\d+(?:[.,]\d+)?)?)",
    re.IGNORECASE,
)
UNIT_EXPRESSION_RE = re.compile(
    r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?\s*(?P<unit>kg|g|mg|mcg|µg|μg|ng|L|mL|dL|µL|μL|"
    r"mmol|µmol|μmol|mol|mmHg|kPa|bpm|cm|mm|m|km|days?|weeks?|months?|years?|hours?|minutes?|seconds?)\b",
    re.IGNORECASE,
)
REGISTRY_ID_RE = re.compile(
    r"\b(?:NCT\d{8}|ISRCTN\d{8}|CRD420\d{6,}|ACTRN\d{14}|ChiCTR[-A-Za-z0-9]+|"
    r"PMID\s*:?\s*\d+|PROSPERO\s*:?\s*CRD420\d{6,})\b|"
    r"\bdoi\s*:?\s*10\.\d{4,9}/[-._;()/:A-Za-z0-9]+",
    re.IGNORECASE,
)
CROSS_REFERENCE_RE = re.compile(
    r"\b(?:Figure|Fig\.|Table|Appendix|Supplementary\s+(?:Table|Figure|File)|Supplement\s+(?:Table|Figure|File))"
    r"\s+[A-Za-z]?\d+(?:[.\-]\d+)?\b",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(r"\b(?:no|not|none|neither|nor|without|absence of|did not|does not|was not|were not)\b", re.IGNORECASE)
DIRECTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:increase[ds]?|increasing|higher|greater|rose|rising|positive association)\b", re.I), "increase"),
    (re.compile(r"\b(?:decrease[ds]?|decreasing|lower|less|reduced?|declined?|negative association)\b", re.I), "decrease"),
    (re.compile(r"\b(?:no (?:material )?(?:difference|change|association|effect)|unchanged|similar)\b", re.I), "no_change"),
)
CLAIM_FORCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:associated with|association between|correlated with|correlation between|related to)\b", re.I), "association"),
    (re.compile(r"\b(?:predicts?|predicted|prediction|prognostic|diagnostic performance)\b", re.I), "prediction"),
    (re.compile(r"\b(?:causes?|caused|causal effect|led to|resulted in|attributable to|because of)\b", re.I), "causation"),
    (re.compile(r"\b(?:mediat(?:e|ed|es|ion)|mechanism|mechanistic|pathway)\b", re.I), "mechanism"),
)
CITATION_RE = re.compile(
    r"(?:\[(?:\d+[a-z]?(?:\s*[-,;]\s*\d+[a-z]?)*|[A-Za-z][^\]]{0,80}\d{4}[^\]]{0,20})\]|"
    r"\((?:[A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?(?:,|\s)\s*)?\d{4}[a-z]?(?:;[^)]*)?\))"
)
ABBREVIATION_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}(?:-[A-Z0-9]{1,6})?\b")
NAMED_TERM_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,5}\b")
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])" )
CLAUSE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|shows?|showed|suggests?|suggested|indicates?|"
    r"indicated|increases?|increased|decreases?|decreased|causes?|caused|predicts?|predicted|"
    r"remains?|remained|provides?|provided|supports?|supported|requires?|required)\b",
    re.IGNORECASE,
)


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_BOUNDARY_RE.split(text.strip()) if part.strip()]


def _normalize_statistical_expression(value: str) -> str:
    value = value.replace("≤", "<=").replace("≥", ">=").replace(",", ".")
    return re.sub(r"\s+", "", value).lower()


def _canonical_matches(text: str, patterns: Sequence[tuple[re.Pattern[str], str]]) -> list[str]:
    matches: list[tuple[int, str]] = []
    for pattern, label in patterns:
        matches.extend((match.start(), label) for match in pattern.finditer(text))
    return [label for _, label in sorted(matches)]


def extract_fact_ledger(text: str, immutable_phrases: Iterable[str] = ()) -> FactLedger:
    immutable = [phrase for phrase in immutable_phrases if phrase]
    numbers = NUMBER_RE.findall(text)
    citations = CITATION_RE.findall(text)
    years = YEAR_RE.findall(text)
    percentages = PERCENT_RE.findall(text)
    effects = EFFECT_RE.findall(text)
    statistical_expressions = [
        _normalize_statistical_expression(match.group(0))
        for match in STATISTICAL_EXPRESSION_RE.finditer(text)
    ]
    units = [match.group("unit").casefold().replace("μ", "µ") for match in UNIT_EXPRESSION_RE.finditer(text)]
    registry_ids = [re.sub(r"\s+", "", match.group(0)).casefold() for match in REGISTRY_ID_RE.finditer(text)]
    cross_references = [re.sub(r"\s+", " ", match.group(0)).casefold() for match in CROSS_REFERENCE_RE.finditer(text)]
    directionality = _canonical_matches(text, DIRECTION_PATTERNS)
    negation_markers = ["negation" for _ in NEGATION_RE.finditer(text)]
    claim_force = _canonical_matches(text, CLAIM_FORCE_PATTERNS)
    abbreviations = sorted(set(ABBREVIATION_RE.findall(text)))
    named_terms = sorted(set(NAMED_TERM_RE.findall(text)))
    occurrences = {
        phrase: len(re.findall(re.escape(phrase), text, flags=re.IGNORECASE))
        for phrase in immutable
    }
    return FactLedger(
        numbers=numbers,
        citations=citations,
        years=years,
        percentages=percentages,
        effect_measures=effects,
        statistical_expressions=statistical_expressions,
        units=units,
        registry_ids=registry_ids,
        cross_references=cross_references,
        directionality=directionality,
        negation_markers=negation_markers,
        claim_force=claim_force,
        abbreviations=abbreviations,
        named_terms=named_terms,
        immutable_phrases=immutable,
        immutable_phrase_occurrences=occurrences,
    )


def _missing_items(before: Sequence[str], after: Sequence[str]) -> list[str]:
    remaining = Counter(after)
    missing: list[str] = []
    for item in before:
        if remaining[item] > 0:
            remaining[item] -= 1
        else:
            missing.append(item)
    return missing


def compare_fact_ledgers(before: FactLedger, after: FactLedger) -> list[Issue]:
    issues: list[Issue] = []
    categories: tuple[tuple[str, Sequence[str], Sequence[str], str], ...] = (
        ("numbers", before.numbers, after.numbers, Severity.CRITICAL.value),
        ("citations", before.citations, after.citations, Severity.CRITICAL.value),
        ("statistical expressions", before.statistical_expressions, after.statistical_expressions, Severity.CRITICAL.value),
        ("units", before.units, after.units, Severity.CRITICAL.value),
        ("study or source identifiers", before.registry_ids, after.registry_ids, Severity.CRITICAL.value),
        ("table, figure, or supplement references", before.cross_references, after.cross_references, Severity.CRITICAL.value),
        ("directionality", before.directionality, after.directionality, Severity.CRITICAL.value),
        ("negation", before.negation_markers, after.negation_markers, Severity.CRITICAL.value),
        ("abbreviations", before.abbreviations, after.abbreviations, Severity.HIGH.value),
    )
    for label, original, revised, severity in categories:
        missing = _missing_items(original, revised)
        added = _missing_items(revised, original)
        if missing or added:
            issues.append(
                Issue(
                    code="AWE-FACT-001",
                    title=f"Unapproved change to {label}",
                    severity=severity,
                    message=(
                        f"The revision changed protected {label}. Missing: {missing or 'none'}. "
                        f"Added: {added or 'none'}."
                    ),
                    evidence="Fact ledger comparison",
                    action="Restore the original factual tokens or explicitly authorize and document the change.",
                    auto_fixable=False,
                    metadata={"missing": missing, "added": added, "category": label},
                )
            )

    before_force = Counter(before.claim_force)
    after_force = Counter(after.claim_force)
    if after_force["causation"] > before_force["causation"]:
        issues.append(
            Issue(
                code="AWE-INFER-001",
                title="Causal force increased during revision",
                severity=Severity.CRITICAL.value,
                message=(
                    "The revised passage contains more causal claim markers than the source. "
                    "A copyedit or unverified provider rewrite may not strengthen inferential force."
                ),
                evidence="Claim-force ledger comparison",
                action="Restore the source inferential level or document the design and assumptions that authorize the causal claim.",
                metadata={"before": dict(before_force), "after": dict(after_force)},
            )
        )
    elif before_force != after_force:
        issues.append(
            Issue(
                code="AWE-FACT-003",
                title="Inferential claim class changed during revision",
                severity=Severity.CRITICAL.value,
                message=f"Claim classes changed from {dict(before_force)} to {dict(after_force)}.",
                evidence="Claim-force ledger comparison",
                action="Confirm that association, prediction, mechanism, and causation labels remain scientifically equivalent.",
                metadata={"before": dict(before_force), "after": dict(after_force)},
            )
        )

    for phrase in before.immutable_phrases:
        original_count = before.immutable_phrase_occurrences.get(phrase, 0)
        revised_count = after.immutable_phrase_occurrences.get(phrase, 0)
        if original_count != revised_count:
            issues.append(
                Issue(
                    code="AWE-FACT-002",
                    title="Immutable phrase was changed",
                    severity=Severity.CRITICAL.value,
                    message=(
                        f"The protected phrase '{phrase}' occurred {original_count} time(s) in the source and "
                        f"{revised_count} time(s) in the revision."
                    ),
                    action="Restore the exact protected phrase unless an author has approved and documented a change.",
                    metadata={"phrase": phrase, "before": original_count, "after": revised_count},
                )
            )
    return issues


def choose_em_dash_replacement(text: str, index: int) -> str:
    """Choose a conservative ASCII punctuation replacement for one em dash.

    This is a copyediting heuristic. Paired parenthetical dashes become
    parentheses. A single dash between two clause-like spans becomes a
    semicolon. Explanatory right-hand spans generally receive a colon.
    """

    positions = [m.start() for m in EM_DASH_RE.finditer(text)]
    if len(positions) >= 2:
        try:
            rank = positions.index(index)
        except ValueError:
            rank = -1
        if rank >= 0:
            return "(" if rank % 2 == 0 else ")"

    left = text[:index].rstrip()
    right = text[index + 1 :].lstrip()
    left_tail = left.rsplit(".", 1)[-1]
    right_head = re.split(r"[.!?]", right, maxsplit=1)[0]
    if CLAUSE_VERB_RE.search(left_tail) and CLAUSE_VERB_RE.search(right_head):
        return ";"
    if re.search(r"\b(?:including|namely|specifically|because|therefore|thus|which means)\s*$", left, re.I):
        return ":"
    if len(TOKEN_RE.findall(right_head)) <= 7 and right_head[:1].islower():
        return ","
    return ":"


def plan_em_dash_revisions(text: str, *, paragraph_index: int | None, section: str) -> list[RevisionProposal]:
    revisions: list[RevisionProposal] = []
    for match in DASH_SURROUNDED_RE.finditer(text):
        dash_offset = next((i for i, char in enumerate(match.group(0)) if char in EM_DASHES), 0)
        dash_index = match.start() + dash_offset
        punctuation = choose_em_dash_replacement(text, dash_index)
        right = text[match.end() :]
        if punctuation == "(":
            replacement = " ("
        elif punctuation == ")":
            replacement = ")" if not right or right[:1] in ".,;:!?)]}" else ") "
        else:
            replacement = punctuation if not right or right[:1] in ".,;:!?)]}" else punctuation + " "
        revisions.append(
            RevisionProposal(
                target=match.group(0),
                replacement=replacement,
                reason="The manuscript profile prohibits em dashes. Replace the mark with punctuation fitted to the sentence relation.",
                commentary="Punctuation was changed without changing the scientific claim.",
                code="AWE-STYLE-001",
                paragraph_index=paragraph_index,
                section=section,
                change_class="mechanical",
                confidence=0.93,
                priority=5,
                metadata={"start": match.start(), "end": match.end()},
            )
        )
    return revisions


def apply_exact_revisions(text: str, revisions: Sequence[RevisionProposal]) -> tuple[str, list[RevisionProposal], list[RevisionProposal]]:
    """Apply exact revisions in stable priority order.

    Each proposal replaces the first exact occurrence. This conservative rule
    avoids guessing when an anchor is ambiguous. Unapplied revisions are
    returned for QC or manual review.
    """

    applied: list[RevisionProposal] = []
    skipped: list[RevisionProposal] = []
    output = text
    for revision in sorted(revisions, key=lambda r: (r.priority, -r.confidence)):
        if not revision.target:
            skipped.append(revision)
            continue
        position = output.find(revision.target)
        if position < 0:
            skipped.append(revision)
            continue
        output = output[:position] + revision.replacement + output[position + len(revision.target) :]
        applied.append(revision)
    return output, applied, skipped


def locate_quote(text: str, quote: str, *, section: str = "other") -> TextAnchor:
    start = text.find(quote)
    if start < 0:
        return TextAnchor(section=section, quote=quote)
    return TextAnchor(start=start, end=start + len(quote), section=section, quote=quote)


def contains_em_dash(text: str) -> bool:
    return bool(EM_DASH_RE.search(text))


def sanitize_prohibited_dashes(text: str) -> str:
    """Remove prohibited dash characters from non-manuscript provider channels."""

    return re.sub(r"[ \t]*[\u2014\u2015][ \t]*", "; ", text)
