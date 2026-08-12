"""Editorial audit: surface patterns, substantive risks and document shape.

The audit reports observable defects and revision risks. It never claims that a
human or an AI wrote the text; authorship is not identifiable from style alone.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .genres import GenreProfile, get_genre, infer_genre
from .model import Document, build_document
from .patterns import PatternHit, scan_patterns
from .substance import RawFinding, scan_substance


@dataclass
class Finding:
    id: str
    pattern_id: str
    title: str
    category: str
    severity: str
    confidence: float
    start: int
    end: int
    original: str
    sentence: str
    rationale: str
    action: str
    source: str
    reported_voice: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "patternId": self.pattern_id,
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "confidence": round(self.confidence, 3),
            "span": {"start": self.start, "end": self.end},
            "original": self.original,
            "sentence": self.sentence,
            "rationale": self.rationale,
            "action": self.action,
            "source": self.source,
            "reportedVoice": self.reported_voice,
            "candidates": self.candidates,
        }


@dataclass
class AuditReport:
    genre: str
    genre_inference: dict[str, Any]
    word_count: int
    sentence_count: int
    paragraph_count: int
    editorial_burden: float
    status: str
    clean: bool
    findings: list[Finding]
    dimension_scores: dict[str, float]
    metrics: dict[str, Any]
    principles: tuple[str, ...] = (
        "Score editorial defects, not presumed authorship.",
        "Treat meaning preservation as a hard gate, not a style preference.",
        "Do not rewrite source-reported language as if it were the current author's claim.",
    )

    def to_dict(self) -> dict[str, Any]:
        counts = Counter(f.severity for f in self.findings)
        return {
            "schemaVersion": "2.0",
            "genre": self.genre,
            "genreInference": self.genre_inference,
            "counts": {
                "words": self.word_count,
                "sentences": self.sentence_count,
                "paragraphs": self.paragraph_count,
                "findings": len(self.findings),
                "hard": counts.get("hard", 0),
                "review": counts.get("review", 0),
                "soft": counts.get("soft", 0),
            },
            "editorialBurden": round(self.editorial_burden, 1),
            "status": self.status,
            "clean": self.clean,
            "dimensionScores": {k: round(v, 1) for k, v in self.dimension_scores.items()},
            "metrics": self.metrics,
            "findings": [f.to_dict() for f in self.findings],
            "principles": list(self.principles),
            "authorshipClaim": None,
        }


_SEVERITY_WEIGHT = {"hard": 7.0, "review": 5.0, "soft": 2.5}
_CATEGORY_DIMENSION = {
    "meta_commentary": "directness",
    "delay": "directness",
    "compression": "directness",
    "syntax": "clarity",
    "rhythm": "rhythm",
    "punctuation": "rhythm",
    "cohesion": "structure",
    "ending": "structure",
    "rhetorical_template": "voice",
    "reader_manipulation": "voice",
    "reader_address": "audience_fit",
    "chatbot_residue": "voice",
    "formatting": "formatting",
    "jargon": "specificity",
    "abstraction": "specificity",
    "specificity": "specificity",
    "inflation": "claim_discipline",
    "quality_claim": "claim_discipline",
    "evidence": "claim_discipline",
    "epistemic": "claim_discipline",
    "unsupported_interpretation": "claim_discipline",
    "claim_support": "claim_discipline",
    "statistical_reporting": "claim_discipline",
    "decision_support": "decision_quality",
}


def _finding_from_pattern(index: int, document: Document, hit: PatternHit) -> Finding:
    sentence = document.sentence_for_span(hit.start, hit.end)
    return Finding(
        id=f"F{index:04d}",
        pattern_id=hit.spec.id,
        title=hit.spec.title,
        category=hit.spec.category,
        severity=hit.severity,
        confidence=hit.confidence,
        start=hit.start,
        end=hit.end,
        original=document.text[hit.start:hit.end],
        sentence=sentence.text if sentence else document.text[hit.start:hit.end],
        rationale=hit.spec.rationale,
        action=hit.spec.action,
        source="surface",
        reported_voice=hit.reported_voice,
    )


def _finding_from_raw(index: int, document: Document, raw: RawFinding) -> Finding:
    sentence = document.sentence_for_span(raw.start, raw.end)
    return Finding(
        id=f"F{index:04d}",
        pattern_id=raw.pattern_id,
        title=raw.title,
        category=raw.category,
        severity=raw.severity,
        confidence=raw.confidence,
        start=raw.start,
        end=raw.end,
        original=document.text[raw.start:raw.end],
        sentence=sentence.text if sentence else document.text[raw.start:raw.end],
        rationale=raw.rationale,
        action=raw.action,
        source=raw.source,
        reported_voice=raw.reported_voice,
    )


def _token_set(text: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is", "are", "was", "were", "this", "that", "it", "we", "our"}
    return {w.casefold() for w in re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}\b", text) if w.casefold() not in stop}


def _jaccard(a: str, b: str) -> float:
    left, right = _token_set(a), _token_set(b)
    return len(left & right) / max(1, len(left | right))


# One line, anchored, with no quantified whitespace that can span a newline. The
# previous form was `(?m)^\s*[-*]?\s*\*\*[^*\n]{2,45}\*\*\s*[:.]?` over masked text.
# Masking replaces a protected span with spaces, so a fenced code block becomes a long
# whitespace run, and `\s*[-*]?\s*` can divide that run in exponentially many ways.
# An 8 KB markdown document with one fenced block took over twenty seconds; a 16 KB one
# did not finish. Scanning line by line removes the ambiguity and bounds the work to
# the length of a single line.
_BOLD_LABEL_LINE = re.compile(r"^[ \t]*(?:[-*][ \t]*)?\*\*([^*\n]{2,45})\*\*[ \t]*[:.]?")


@dataclass(frozen=True)
class _LabelHit:
    start_offset: int
    end_offset: int

    def start(self) -> int:
        return self.start_offset

    def end(self) -> int:
        return self.end_offset


def _find_bold_labels(text: str) -> list[_LabelHit]:
    hits: list[_LabelHit] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _BOLD_LABEL_LINE.match(line)
        if match:
            hits.append(_LabelHit(offset + match.start(), offset + match.end()))
        offset += len(line)
    return hits


def _structure_findings(document: Document, genre: GenreProfile) -> tuple[list[RawFinding], dict[str, Any]]:
    sample = document.sample
    findings: list[RawFinding] = []
    lengths = [len(s.tokens) for s in sample.sentences if s.tokens]
    paragraph_lengths = [p.token_count for p in sample.paragraphs if p.token_count]
    mean = statistics.fmean(lengths) if lengths else 0.0
    stdev = statistics.stdev(lengths) if len(lengths) > 1 else 0.0
    cv = stdev / mean if mean else 0.0
    pmean = statistics.fmean(paragraph_lengths) if paragraph_lengths else 0.0
    pstdev = statistics.stdev(paragraph_lengths) if len(paragraph_lengths) > 1 else 0.0
    pcv = pstdev / pmean if pmean else 0.0

    long_limit = {
        "legal": 55, "scientific": 48, "systematic-review": 48,
        "technical": 40, "policy-brief": 34, "email": 32,
    }.get(genre.id, 40)
    for sentence in sample.sentences:
        if len(sentence.tokens) > long_limit and not document.is_protected(sentence.start, sentence.end):
            findings.append(RawFinding(
                pattern_id="overloaded_sentence",
                title="Overloaded sentence",
                category="syntax",
                severity="soft",
                confidence=min(0.96, 0.65 + (len(sentence.tokens) - long_limit) / 80),
                start=sentence.start,
                end=sentence.end,
                rationale=f"The sentence carries {len(sentence.tokens)} words; its main action and qualifications compete for attention.",
                action="Keep the governing claim in one sentence and move a distinct condition, method or consequence into the next.",
                source="structure",
                reported_voice=document.is_reported_voice(sentence.start, sentence.end),
            ))

    if len(lengths) >= 7 and cv < 0.18:
        findings.append(RawFinding(
            pattern_id="uniform_sentence_rhythm", title="Uniform sentence rhythm", category="rhythm",
            severity="soft", confidence=min(0.92, 0.7 + (0.18 - cv)),
            start=sample.sentences[0].start, end=sample.sentences[-1].end,
            rationale="Sentence lengths vary unusually little, creating a mechanically even cadence.",
            action="Vary sentence shape only where argument structure warrants it: shorten a decision, lengthen a necessary qualification, and leave natural sentences alone.",
            source="structure",
        ))

    if len(paragraph_lengths) >= 4 and pcv < 0.16:
        findings.append(RawFinding(
            pattern_id="uniform_paragraph_shape", title="Uniform paragraph shape", category="structure",
            severity="soft", confidence=0.74,
            start=sample.paragraphs[0].start, end=sample.paragraphs[-1].end,
            rationale="Paragraphs have nearly identical lengths, which can indicate outline-shaped prose rather than idea-shaped paragraphs.",
            action="Let each paragraph end when its rhetorical job is complete; merge or divide only where the argument changes.",
            source="structure",
        ))

    openers = [s.tokens[0] for s in sample.sentences if s.tokens]
    opener_counts = Counter(openers)
    if len(openers) >= 6:
        opener, count = opener_counts.most_common(1)[0]
        share = count / len(openers)
        if count >= 3 and share >= 0.34:
            relevant = [s for s in sample.sentences if s.tokens and s.tokens[0] == opener]
            findings.append(RawFinding(
                pattern_id="repeated_sentence_opener", title="Repeated sentence opener", category="rhythm",
                severity="soft", confidence=min(0.94, 0.65 + share / 2),
                start=relevant[0].start, end=relevant[-1].end,
                rationale=f"{count} of {len(openers)} sentences begin with “{opener}”, creating a repeated syntactic template.",
                action="Recast only the sentences whose logical subject or sequence becomes clearer; do not rotate synonyms mechanically.",
                source="structure",
            ))

    # A single connective can express a real logical relation. Flag only repeated
    # connective scaffolding across a document, not isolated uses such as
    # “In addition” or “However”.
    connective_re = re.compile(
        r"^(?:furthermore|moreover|additionally|in addition|on the other hand|"
        r"that said|with that being said|moving forward|going forward)[,;:]\s*",
        re.IGNORECASE,
    )
    connective_hits: list[tuple[Any, re.Match[str]]] = []
    for sentence in sample.sentences:
        match = connective_re.match(sentence.text.strip())
        if match:
            connective_hits.append((sentence, match))
    connective_share = len(connective_hits) / max(1, len(sample.sentences))
    if len(connective_hits) >= 3 and connective_share >= 0.24:
        findings.append(RawFinding(
            pattern_id="empty_transition", title="Repeated connective scaffolding", category="cohesion",
            severity="soft", confidence=min(0.94, 0.68 + connective_share / 2),
            start=connective_hits[0][0].start, end=connective_hits[-1][0].end,
            rationale=(
                f"{len(connective_hits)} of {len(sample.sentences)} sentences begin with generic "
                "connectives, so paragraph flow is being carried by labels rather than the claims themselves."
            ),
            action=(
                "Retain connectives that express a necessary contrast or addition; remove repeated scaffolds "
                "and make the logical relation explicit in the sentence itself."
            ),
            source="structure",
        ))

    # Four consecutive short declarative sentences are rarely accidental outside
    # social copy or a deliberately voiced op-ed.
    if genre.id not in {"op-ed", "email"}:
        run: list[Any] = []
        for sentence in sample.sentences:
            if 1 <= len(sentence.tokens) <= 6:
                run.append(sentence)
                if len(run) >= 4:
                    findings.append(RawFinding(
                        pattern_id="staccato_run", title="Stacked short-sentence run", category="rhythm",
                        severity="soft", confidence=0.88,
                        start=run[0].start, end=run[-1].end,
                        rationale="Four or more very short sentences create manufactured punch and interrupt the argument.",
                        action="Combine sentences that share one grammatical or logical unit; keep a short sentence only where the emphasis is earned.",
                        source="structure",
                    ))
                    break
            else:
                run = []

    # Repeated conclusion: compare the final paragraph to each earlier paragraph,
    # but require enough content words to avoid flagging short procedural endings.
    if len(sample.paragraphs) >= 3:
        final = sample.paragraphs[-1]
        if len(_token_set(final.text)) >= 8:
            best = max(
                ((_jaccard(final.text, paragraph.text), paragraph) for paragraph in sample.paragraphs[:-1]),
                key=lambda item: item[0],
            )
            if best[0] >= 0.58:
                findings.append(RawFinding(
                    pattern_id="duplicate_conclusion", title="Conclusion repeats an earlier paragraph", category="ending",
                    severity="soft", confidence=min(0.97, 0.65 + best[0] / 3),
                    start=final.start, end=final.end,
                    rationale=f"The final paragraph repeats much of an earlier paragraph (content overlap {best[0]:.0%}).",
                    action="Delete the recap or retain only the decision, implication or next action that is genuinely new.",
                    source="structure",
                ))

    # Bold-label listicles are useful in reference documentation, but repeated tiny
    # labelled sections are often presentation scaffolding in reports and essays.
    bold_labels = _find_bold_labels(document.masked_text)
    if len(bold_labels) >= 4 and genre.id not in {"technical"}:
        findings.append(RawFinding(
            pattern_id="bold_label_listicle", title="Bold-label listicle", category="formatting",
            severity="soft", confidence=0.84,
            start=bold_labels[0].start(), end=bold_labels[-1].end(),
            rationale="Repeated bold labels turn a prose argument into a template-like list of mini-sections.",
            action="Use a real table when the task is comparison, a list when the task is scanning, or prose when the points form an argument.",
            source="structure",
        ))

    metrics = {
        "sentenceLengthMean": round(mean, 2),
        "sentenceLengthCv": round(cv, 3),
        "paragraphLengthMean": round(pmean, 2),
        "paragraphLengthCv": round(pcv, 3),
        "topSentenceOpener": opener_counts.most_common(1)[0][0] if opener_counts else None,
        "topSentenceOpenerShare": round(opener_counts.most_common(1)[0][1] / len(openers), 3) if openers else 0.0,
        "connectiveOpenerCount": len(connective_hits),
        "connectiveOpenerShare": round(connective_share, 3),
        "emDashCount": document.text.count("—"),
        "questionCount": document.text.count("?"),
        "protectedSpanCount": len(document.protected),
        "reportedVoiceSpanCount": len(document.reported_voice),
    }
    return findings, metrics


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    # Preserve different substantive categories. Within the same category, suppress
    # a broad structural span when a more specific finding covers the same passage.
    ordered = sorted(findings, key=lambda f: (f.start, f.end - f.start, -f.confidence, f.pattern_id))
    output: list[Finding] = []
    for item in ordered:
        duplicate = False
        for prior in output:
            if item.pattern_id == prior.pattern_id and item.start == prior.start and item.end == prior.end:
                duplicate = True
                break
            if item.category == prior.category and item.source == prior.source:
                overlap = max(0, min(item.end, prior.end) - max(item.start, prior.start))
                shorter = min(item.end - item.start, prior.end - prior.start)
                if shorter > 0 and overlap / shorter > 0.9 and item.confidence <= prior.confidence:
                    duplicate = True
                    break
        if not duplicate:
            output.append(item)
    output.sort(key=lambda f: (f.start, f.end, f.pattern_id))
    for i, item in enumerate(output, 1):
        item.id = f"F{i:04d}"
    return output


def _burden(findings: list[Finding], words: int) -> tuple[float, dict[str, float]]:
    raw_total = 0.0
    dimensions: dict[str, float] = defaultdict(float)
    for finding in findings:
        weight = _SEVERITY_WEIGHT.get(finding.severity, 3.0) * finding.confidence
        # A whole-document structural finding should matter, but not as much as a
        # dozen independent sentence-level defects.
        if finding.end - finding.start > max(250, words * 3):
            weight *= 0.75
        raw_total += weight
        dimension = _CATEGORY_DIMENSION.get(finding.category, "other")
        dimensions[dimension] += weight
    scale = max(12.0, 10.0 + words / 50.0)
    score = 100.0 * (1.0 - math.exp(-raw_total / scale)) if raw_total else 0.0
    dim_scores = {
        name: 100.0 * (1.0 - math.exp(-value / max(4.0, scale * 0.65)))
        for name, value in dimensions.items()
    }
    return min(100.0, score), dim_scores


def _status(score: float, findings: list[Finding]) -> str:
    hard = sum(1 for f in findings if f.severity == "hard")
    review = sum(1 for f in findings if f.severity == "review")
    if hard >= 3 or score >= 75:
        return "rebuild_required"
    if hard or review >= 2 or score >= 45:
        return "substantive_edit"
    if findings or score >= 15:
        return "line_edit"
    return "clean"


# Above this share of masked characters the audit is describing the mask rather than
# the prose, and no verdict drawn from it is reportable.
UNREADABLE_COVERAGE = 0.5


def audit_text(
    text: str,
    genre: str | None = None,
    *,
    include_quoted: bool = False,
) -> AuditReport:
    """Audit observable editorial defects. Never infers authorship.

    `genre` is required. It used to default to "auto", and on the benchmark corpus
    inference agreed with the correct profile on 25% of published documents, 8% of
    agent notes and none of the author's own submission documents. Genre selects the
    severity table and the long-sentence limit, so a silent wrong guess mis-grades the
    whole document. Passing "auto" is still allowed and still returns the inference,
    but it now carries `genreAssumed` so a caller cannot mistake a guess for a choice.
    """
    if not text or not text.strip():
        raise ValueError("Cannot audit empty text")
    inference = infer_genre(text)
    assumed = not genre or genre == "auto"
    selected_name = inference["genre"] if assumed else genre
    selected = get_genre(selected_name)
    document = build_document(text, include_quoted=include_quoted)

    findings: list[Finding] = []
    index = 1
    for hit in scan_patterns(document, selected):
        findings.append(_finding_from_pattern(index, document, hit))
        index += 1
    for raw in scan_substance(document, selected):
        findings.append(_finding_from_raw(index, document, raw))
        index += 1
    structural, metrics = _structure_findings(document, selected)
    for raw in structural:
        findings.append(_finding_from_raw(index, document, raw))
        index += 1

    findings = _deduplicate(findings)
    words = document.sample.token_count
    score, dimensions = _burden(findings, words)
    status = _status(score, findings)
    coverage = document.protected_coverage
    unreadable = coverage >= UNREADABLE_COVERAGE
    if unreadable:
        # Not clean, and not gradeable either. Withholding the verdict is the honest
        # result: the findings that did surface are still real, but their absence
        # elsewhere is an artefact of masking.
        status = "not_assessable"
    clean = status == "clean"
    metrics["protectedCoverage"] = round(coverage, 4)
    metrics["genreAssumed"] = assumed
    return AuditReport(
        genre=selected.id,
        genre_inference=inference,
        word_count=words,
        sentence_count=len(document.sample.sentences),
        paragraph_count=len(document.sample.paragraphs),
        editorial_burden=score,
        status=status,
        clean=clean,
        findings=findings,
        dimension_scores=dimensions,
        metrics=metrics,
    )
