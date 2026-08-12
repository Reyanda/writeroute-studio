"""Candidate generation, safety checks and ranking for editorial findings."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from .audit import AuditReport, Finding, audit_text
from .genres import GenreProfile
from .integrity import IntegrityReport, extract_anchors, get_policy, verify_integrity
from .model import build_document
from .patterns import PatternSpec, load_patterns
from .voice import VoiceProfile, voice_distance


@dataclass
class Candidate:
    finding_id: str
    kind: str
    start: int | None
    end: int | None
    replacement: str | None
    strategy: str
    risk: str
    safe_to_apply: bool
    score: float
    rationale: str
    preview: str
    burden_before: float | None = None
    burden_after: float | None = None
    integrity: dict[str, Any] | None = None
    introduced_findings: list[str] = field(default_factory=list)
    requires_author_input: bool = False
    document_text: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "edit": None if self.start is None else {
                "start": self.start,
                "end": self.end,
                "replacement": self.replacement,
            },
            "strategy": self.strategy,
            "risk": self.risk,
            "safeToApply": self.safe_to_apply,
            "score": round(self.score, 2),
            "rationale": self.rationale,
            "preview": self.preview,
            "burdenBefore": None if self.burden_before is None else round(self.burden_before, 1),
            "burdenAfter": None if self.burden_after is None else round(self.burden_after, 1),
            "integrity": self.integrity,
            "introducedFindings": self.introduced_findings,
            "requiresAuthorInput": self.requires_author_input,
        }


@dataclass(frozen=True)
class Proposal:
    start: int
    end: int
    replacement: str
    strategy: str
    risk: str
    rationale: str


_RHETORICAL_DELETE_ALLOWED: dict[str, frozenset[str]] = {
    # These exceptions are deliberately narrow. They permit only the semantic
    # token that is part of the recognised non-document formula. Any number,
    # date, citation, name, quotation, code token, scope change or added anchor
    # still blocks the edit.
    "assistant_meta_preface": frozenset({"length"}),
    "closing_offer": frozenset({"length", "modal_force"}),
    "fake_profound_kicker": frozenset({"length", "negation"}),
    "reasoning_leak": frozenset({"length"}),
}

_FACTUAL_ANCHOR_CATEGORIES = frozenset({
    "url", "doi", "citation", "statistic", "cross_reference", "date", "number",
    "quotation", "acronym", "named_entity", "inline_code", "cli_flag",
    "file_path", "heading",
})


def _bounded_contextual_override(
    text: str,
    finding: Finding,
    proposal: Proposal,
    genre: GenreProfile,
    report: IntegrityReport,
) -> frozenset[str]:
    """Return the only integrity categories a recognised deletion may consume.

    This is not a general licence to drop semantic anchors. The candidate must
    delete, not replace; the changed categories must be pattern-specific; no
    hard category may be added; and the deleted span may contain no factual or
    technical anchor protected by the genre policy.
    """
    allowed = _RHETORICAL_DELETE_ALLOWED.get(finding.pattern_id)
    if not allowed or proposal.replacement != "" or report.passes:
        return frozenset()
    hard = [violation for violation in report.violations if violation.severity == "hard"]
    hard_categories = {violation.category for violation in hard}
    if not hard_categories or not hard_categories.issubset(allowed):
        return frozenset()
    if any(violation.added for violation in hard):
        return frozenset()
    deleted = text[proposal.start:proposal.end]
    policy = get_policy(genre.integrity_policy)
    for anchor in extract_anchors(deleted):
        if anchor.kind in _FACTUAL_ANCHOR_CATEGORIES and anchor.kind in policy.hard_categories:
            return frozenset()
    return frozenset(hard_categories)


_NOMINALISATIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)^conducted an analysis of\s*$"), "analysed"),
    (re.compile(r"(?i)^conduct an analysis of\s*$"), "analyse"),
    (re.compile(r"(?i)^conducting an analysis of\s*$"), "analysing"),
    (re.compile(r"(?i)^performed an analysis of\s*$"), "analysed"),
    (re.compile(r"(?i)^perform an analysis of\s*$"), "analyse"),
    (re.compile(r"(?i)^performed an evaluation of\s*$"), "evaluated"),
    (re.compile(r"(?i)^perform an evaluation of\s*$"), "evaluate"),
    (re.compile(r"(?i)^conducted an assessment of\s*$"), "assessed"),
    (re.compile(r"(?i)^conduct an assessment of\s*$"), "assess"),
    (re.compile(r"(?i)^made a decision to\s*$"), "decided to"),
    (re.compile(r"(?i)^make a decision to\s*$"), "decide to"),
    (re.compile(r"(?i)^provided an explanation of\s*$"), "explained"),
    (re.compile(r"(?i)^provide an explanation of\s*$"), "explain"),
    (re.compile(r"(?i)^carried out an investigation into\s*$"), "investigated"),
    (re.compile(r"(?i)^carry out an investigation into\s*$"), "investigate"),
    (re.compile(r"(?i)^undertook a review of\s*$"), "reviewed"),
    (re.compile(r"(?i)^undertake a review of\s*$"), "review"),
]

_FRAME_MAP: dict[str, list[str]] = {
    "qc_assurance": [
        "We checked [output] against [reference/criterion] and found [result].",
        "[Named procedure] tested [specific property] under [conditions].",
    ],
    "unsubstantiated_quality_claim": [
        "[Method] achieved [metric] on [validation dataset/comparator].",
        "We tested [property] using [named procedure]; [result and limitation].",
    ],
    "vague_attribution": [
        "[Named source, year] found [specific result] in [population/context].",
        "The available evidence does not establish [claim]; [bounded statement supported by the source].",
    ],
    "importance_puffery": [
        "[Event] was the first/only [bounded factual distinction].",
        "[Event] changed [specific process/outcome] by [measure or mechanism].",
    ],
    "vague_impact": [
        "[Action] changed [outcome] from [baseline] to [result] for [population] over [time].",
        "[Mechanism] enables [actor] to [specific action], reducing/increasing [measured consequence].",
    ],
    "portability_phrase": [
        "[Actor] now [specific action] using [mechanism], which changes [measured consequence].",
        "Replace the portable phrase with one sentence naming the actor, action, object and result.",
    ],
    "corporate_jargon": [
        "[Team] will [specific action] by [date], measured by [metric].",
        "Name the operational change instead of the business slogan.",
    ],
    "unsupported_superlative": [
        "Among [defined comparison set] measured on [date/criterion], [subject] ranked [position/result].",
        "Replace the ranking with the specific property the evidence supports.",
    ],
    "unsupported_novelty_claim": [
        "A search of [sources] through [date] found no prior [defined type of work].",
        "The contribution is [specific method/data/decision], without claiming priority.",
    ],
    "unsupported_causal_claim": [
        "[Exposure] was associated with [outcome] after adjustment for [pre-specified covariates].",
        "Under [identification assumptions], the estimated effect of [intervention] on [outcome] was [estimate and uncertainty].",
    ],
    "causal_overreach_observational": [
        "[Exposure] was associated with [outcome]; residual confounding and reverse causation remain possible.",
        "Under [named causal design and assumptions], [estimand] was [estimate and uncertainty].",
    ],
    "unsupported_safety_claim": [
        "In [tested population/conditions], [intervention] had [adverse-event result] over [follow-up].",
        "The evidence did not identify [specific harm], but it cannot exclude [limitation].",
    ],
    "unsupported_guarantee": [
        "The command is idempotent when [precondition]; verify success by [observable test].",
        "Under [version/platform/condition], the system passed [named test]; failures remain possible when [condition].",
    ],
    "statistical_significance_without_estimate": [
        "[Outcome] differed by [effect estimate] ([uncertainty interval]; p=[value]).",
        "Report the effect size and uncertainty; omit the significance label if it adds no decision value.",
    ],
    "unsupported_recommendation": [
        "[Actor] should [action] by [time] because [evidence/rationale], subject to [constraint].",
        "Choose [option] when [condition]; use [alternative] when [trade-off/constraint].",
    ],
    "abstraction_without_substrate": [
        "Add one sentence naming the actor, action, object, mechanism and measurable consequence.",
        "Replace one abstract noun chain with a concrete example drawn from the evidence.",
    ],
    "self_signposting": [
        "Replace the roadmap with the first substantive claim of the announced section.",
    ],
    "superficial_analysis": [
        "Replace the -ing clause with the concrete consequence: [fact], so [specific implication].",
        "Delete the interpretation if the evidence already makes the point.",
    ],
    "fake_strong_verb": [
        "[Subject] [direct verb] [objects/actions] in one place.",
    ],
    "expletive_opening": [
        "Move the real subject to the front: [subject] [direct verb] [object].",
    ],
    "overloaded_sentence": [
        "Sentence 1: governing claim and result. Sentence 2: condition, method, limitation or consequence.",
    ],
    "uniform_sentence_rhythm": [
        "Shorten the sentence carrying the decision; retain length where a qualification is methodologically necessary.",
    ],
    "uniform_paragraph_shape": [
        "Give each paragraph one rhetorical job, and end it when that job is complete.",
    ],
    "repeated_sentence_opener": [
        "Use the actual subject of each sentence; do not rotate synonyms merely to create variety.",
    ],
    "staccato_run": [
        "Combine fragments that form one proposition; keep a short sentence only for a genuine decision or consequence.",
    ],
    "duplicate_conclusion": [
        "Delete the repeated recap and retain only the new implication, decision or next action.",
    ],
    "bold_label_listicle": [
        "Use prose for an argument, a table for comparison, and a list for independent scan-ready items.",
    ],
    "adjective_tricolon": [
        "Keep one demonstrable property and state the evidence for it.",
    ],
    "second_person_coaching": [
        "State the instruction directly: [imperative verb] [object] [condition].",
    ],
    "em_dash_cluster": [
        "Use periods between independent claims; use parentheses for a true aside; use a colon only for an explanation or list.",
    ],
}


def _pattern_map() -> dict[str, PatternSpec]:
    return {spec.id: spec for spec in load_patterns()}


def _find_match(text: str, finding: Finding, spec: PatternSpec) -> re.Match[str] | None:
    regex = re.compile(spec.pattern)
    for match in regex.finditer(text):
        if match.start() == finding.start and match.end() == finding.end:
            return match
    # A structural edit elsewhere may have shifted offsets; exact original text is a
    # safe fallback only when it appears once.
    if finding.original and text.count(finding.original) == 1:
        start = text.index(finding.original)
        for match in regex.finditer(text):
            if match.start() == start and match.group(0) == finding.original:
                return match
    return None


def _case_like(original: str, replacement: str) -> str:
    if not replacement:
        return replacement
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _boundary_apply(text: str, start: int, end: int, replacement: str) -> str:
    before, after = text[:start], text[end:]
    replacement = replacement or ""
    if not replacement and end == len(text):
        before = before.rstrip(" \t")
    # A deleted opener may leave duplicated spaces or a lowercase sentence start.
    if not replacement:
        if before.endswith((" ", "\t")) and after.startswith((" ", "\t")):
            after = after.lstrip(" \t")
        if (start == 0 or before.rstrip().endswith((".", "!", "?", "\n"))) and after[:1].islower():
            after = after[0].upper() + after[1:]
        if after.startswith((",", ";", ":")):
            after = after[1:].lstrip()
    result = before + replacement + after
    # Only repair the immediate join. Global whitespace normalization could damage
    # code blocks, tables or deliberate line breaks.
    join = len(before) + len(replacement)
    left = max(0, join - 4)
    right = min(len(result), join + 4)
    local = result[left:right]
    local = re.sub(r"[ \t]+([,.;:!?])", r"\1", local)
    local = re.sub(r" {2,}", " ", local)
    return result[:left] + local + result[right:]


def _sentence_bounds(text: str, finding: Finding) -> tuple[int, int]:
    document = build_document(text)
    sentence = document.sentence_for_span(finding.start, finding.end)
    if not sentence:
        return finding.start, finding.end
    start, end = sentence.start, sentence.end
    # Include horizontal space following a standalone sentence, but preserve
    # paragraph boundaries and blank lines.
    while end < len(text) and text[end] in " \t":
        end += 1
    return start, end


def _mapping_replacement(spec: PatternSpec, original: str) -> str | None:
    direct = spec.replacements.get(original.casefold())
    return _case_like(original, direct) if direct is not None else None


def _nominalisation_replacement(original: str) -> str | None:
    compact = re.sub(r"\s+", " ", original.strip())
    for pattern, replacement in _NOMINALISATIONS:
        if pattern.fullmatch(compact):
            return _case_like(compact, replacement)
    return None


def _sentence_case_heading(original: str) -> str:
    marker_match = re.match(r"^(\s*#{1,6}\s+)(.+?)\s*$", original)
    if not marker_match:
        return original
    marker, heading = marker_match.groups()
    words = heading.split()
    if not words:
        return original
    keep_upper = {w for w in words if w.isupper() or any(c.isdigit() for c in w)}
    lowered = [words[0][:1].upper() + words[0][1:].lower()]
    for word in words[1:]:
        lowered.append(word if word in keep_upper else word.lower())
    return marker + " ".join(lowered)


def _proposals(text: str, finding: Finding) -> list[Proposal]:
    spec = _pattern_map().get(finding.pattern_id)
    if not spec:
        return []
    match = _find_match(text, finding, spec)
    strategy = spec.strategy
    proposals: list[Proposal] = []
    if strategy == "mapping":
        replacement = _mapping_replacement(spec, finding.original)
        if replacement is not None:
            proposals.append(Proposal(finding.start, finding.end, replacement, strategy, "low", "Use the direct equivalent."))
    elif strategy == "nominalisation":
        replacement = _nominalisation_replacement(finding.original)
        if replacement is not None:
            proposals.append(Proposal(finding.start, finding.end, replacement, strategy, "low", "Make the verb carry the action."))
    elif strategy == "delete_span":
        proposals.append(Proposal(finding.start, finding.end, "", strategy, "low", "Remove the non-substantive scaffold."))
    elif strategy == "delete_sentence":
        start, end = _sentence_bounds(text, finding)
        proposals.append(Proposal(start, end, "", strategy, "medium", "Remove the sentence because it contributes no document content."))
    elif strategy.startswith("keep_group_") and match:
        group = strategy.removeprefix("keep_group_")
        replacement = match.groupdict().get(group)
        if replacement:
            replacement = replacement.strip()
            replacement = _case_like(finding.original, replacement)
            proposals.append(Proposal(finding.start, finding.end, replacement, strategy, "medium", "Keep the substantive clause and remove the rhetorical frame."))
    elif strategy == "remove_emoji":
        replacement = re.sub(r"^[\s#]*[\U0001F300-\U0001FAFF\u2600-\u27BF]\s*", lambda m: "#" * finding.original.count("#") + (" " if "#" in finding.original else ""), finding.original)
        proposals.append(Proposal(finding.start, finding.end, replacement, strategy, "low", "Remove the decorative symbol while preserving the heading text."))
    elif strategy == "sentence_case_heading":
        replacement = _sentence_case_heading(finding.original)
        if replacement != finding.original:
            proposals.append(Proposal(finding.start, finding.end, replacement, strategy, "medium", "Apply sentence case while preserving acronyms and numerals."))
    return proposals


def _new_unsafe_findings(before: AuditReport, after: AuditReport) -> list[str]:
    before_counts = Counter((f.pattern_id, f.severity) for f in before.findings if f.severity in {"hard", "review"})
    after_counts = Counter((f.pattern_id, f.severity) for f in after.findings if f.severity in {"hard", "review"})
    extra = after_counts - before_counts
    return [f"{pattern} ({severity}) ×{count}" for (pattern, severity), count in sorted(extra.items())]


def _preview(text: str, start: int, end: int, replacement: str, radius: int = 90) -> str:
    before = text[max(0, start - radius):start]
    after = text[end:min(len(text), end + radius)]
    value = (before + "⟦" + replacement + "⟧" + after).replace("\n", " ")
    return re.sub(r"\s+", " ", value).strip()


def evaluate_proposal(
    text: str,
    finding: Finding,
    proposal: Proposal,
    genre: GenreProfile,
    audit_before: AuditReport,
    voice_profile: VoiceProfile | dict | str | None = None,
) -> Candidate:
    candidate_text = _boundary_apply(text, proposal.start, proposal.end, proposal.replacement)
    integrity_report = verify_integrity(text, candidate_text, genre.integrity_policy)
    audit_after = audit_text(candidate_text, genre=genre.id)
    introduced = _new_unsafe_findings(audit_before, audit_after)
    target_before = sum(f.pattern_id == finding.pattern_id for f in audit_before.findings)
    target_after = sum(f.pattern_id == finding.pattern_id for f in audit_after.findings)
    target_removed = target_after < target_before
    voice_penalty = 0.0
    voice_note = ""
    if voice_profile is not None:
        before_voice = voice_distance(voice_profile, text).score
        after_voice = voice_distance(voice_profile, candidate_text).score
        voice_penalty = max(0.0, after_voice - before_voice)
        if voice_penalty > 5:
            voice_note = f" Voice distance worsened by {voice_penalty:.1f} points."
    burden_reduction = audit_before.editorial_burden - audit_after.editorial_burden
    changed_chars = max(proposal.end - proposal.start, len(proposal.replacement))
    minimality = min(1.0, changed_chars / max(1, len(text)))
    contextual_categories = _bounded_contextual_override(
        text, finding, proposal, genre, integrity_report
    )
    rhetorical_delete_override = bool(contextual_categories)
    surface_length_override = False
    if not integrity_report.passes and proposal.strategy == "delete_span" and finding.source == "surface":
        hard_categories = {v.category for v in integrity_report.violations if v.severity == "hard"}
        surface_length_override = hard_categories == {"length"} and finding.confidence >= 0.85
        if surface_length_override:
            contextual_categories = frozenset({"length"})
    effective_integrity = integrity_report.passes or rhetorical_delete_override or surface_length_override
    safe = (
        effective_integrity
        and not introduced
        and target_removed
        and burden_reduction >= -0.05
        and voice_penalty <= 5
    )
    risk = proposal.risk if safe else "blocked"
    score = (
        55.0 * max(-0.2, min(1.0, burden_reduction / max(5.0, audit_before.editorial_burden)))
        + 25.0 * (1.0 - minimality)
        + 15.0 * (1.0 if target_removed else 0.0)
        + 5.0 * (1.0 if integrity_report.passes else 0.0)
        - 2.0 * voice_penalty
        - 15.0 * len(introduced)
    )
    rationale = proposal.rationale
    if not integrity_report.passes and effective_integrity:
        categories = ", ".join(sorted(contextual_categories))
        rationale += (
            " Accepted under a bounded deletion exception for " + categories +
            "; the deleted formula contains no protected factual or technical anchor."
        )
    elif not integrity_report.passes:
        rationale += " Blocked because a meaning-preservation invariant changed."
    if introduced:
        rationale += " Blocked because the edit introduced a new hard or review-level finding."
    rationale += voice_note
    return Candidate(
        finding_id=finding.id,
        kind="replacement",
        start=proposal.start,
        end=proposal.end,
        replacement=proposal.replacement,
        strategy=proposal.strategy,
        risk=risk,
        safe_to_apply=safe,
        score=score,
        rationale=rationale,
        preview=_preview(text, proposal.start, proposal.end, proposal.replacement),
        burden_before=audit_before.editorial_burden,
        burden_after=audit_after.editorial_burden,
        integrity={
            **integrity_report.to_dict(),
            "contextualOverride": (rhetorical_delete_override or surface_length_override),
            "contextualOverrideCategories": sorted(contextual_categories),
        },
        introduced_findings=introduced,
        document_text=candidate_text,
    )


def frame_candidates(finding: Finding) -> list[Candidate]:
    frames = _FRAME_MAP.get(finding.pattern_id, [])
    return [
        Candidate(
            finding_id=finding.id,
            kind="rewrite_frame",
            start=None,
            end=None,
            replacement=None,
            strategy="author_input",
            risk="review",
            safe_to_apply=False,
            score=0.0,
            rationale="This frame identifies the missing information without inventing it.",
            preview=frame,
            requires_author_input=True,
        )
        for frame in frames
    ]


def candidates_for_finding(
    text: str,
    finding: Finding,
    genre: GenreProfile,
    audit_before: AuditReport,
    *,
    voice_profile: VoiceProfile | dict | str | None = None,
    max_candidates: int = 3,
) -> list[Candidate]:
    candidates = [
        evaluate_proposal(text, finding, proposal, genre, audit_before, voice_profile)
        for proposal in _proposals(text, finding)
    ]
    candidates.extend(frame_candidates(finding))
    candidates.sort(key=lambda c: (c.safe_to_apply, c.score, not c.requires_author_input), reverse=True)
    return candidates[:max_candidates]
