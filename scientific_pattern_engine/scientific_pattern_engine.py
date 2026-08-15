#!/usr/bin/env python3
"""Transparent scientific-writing pattern and quality audit engine.

The engine detects revision-worthy patterns. It deliberately does not estimate
whether a manuscript was written by AI, and its scores are not authorship
probabilities.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

TOKEN_RE = re.compile(r"\b[\w][\w’'\-]*\b", re.UNICODE)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z])(?:[-+]?\d+(?:[.,]\d+)?(?:\s*(?:%|percentage points?|pp|fold))?|"
    r"(?:RR|OR|HR|PR|IRR|MD|SMD)\s*[=:]?\s*[-+]?\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
UNCERTAINTY_RE = re.compile(
    r"\b(?:95\s*%?\s*(?:CI|CrI)|confidence interval|credible interval|standard error|SE\s*[=:]|"
    r"IQR|interquartile range|standard deviation|SD\s*[=:]|p\s*[<=>]|q\s*[<=>])\b",
    re.IGNORECASE,
)
CITATION_RE = re.compile(
    r"(?:\[(?:\d+[a-z]?(?:\s*[-,;]\s*\d+[a-z]?)*|[A-Za-z][^\]]{0,80}\d{4}[^\]]{0,20})\]|"
    r"\((?:[A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?(?:,|\s)\s*)?\d{4}[a-z]?(?:;[^)]*)?\))"
)
EFFECT_MEASURE_RE = re.compile(
    r"\b(?:risk ratio|relative risk|odds ratio|hazard ratio|prevalence ratio|rate ratio|risk difference|"
    r"mean difference|standardized mean difference|standardised mean difference|incidence rate ratio|"
    r"absolute risk|number needed to treat|RR|OR|HR|PR|IRR|SMD|NNT)\b",
    re.IGNORECASE,
)
MECHANISM_RE = re.compile(
    r"\b(?:through|via|mediated by|mediation|because|owing to|due to|by increasing|by decreasing|"
    r"by reducing|pathway|mechanism|receptor|transporter|enzyme|gene|protein|cytokine|inflammation|"
    r"immune|metabolic|microbiome|hormone|signalling|signaling)\b",
    re.IGNORECASE,
)
PROPER_NOUN_PROXY_RE = re.compile(r"\b(?:[A-Z]{2,}[A-Z0-9-]*|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
DENOMINATOR_RE = re.compile(r"\b(?:n\s*=\s*\d+|\d+\s*/\s*\d+)\b", re.IGNORECASE)
PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
SPECIFIC_LIMITATION_RE = re.compile(
    r"\b(?:selection bias|measurement error|misclassification|residual confounding|unmeasured confounding|"
    r"recall bias|attrition|loss to follow-up|missing data|small sample|sample size|limited power|"
    r"generalizability|generalisability|external validity|reverse causation|temporal ambiguity|"
    r"model misspecification|positivity|overlap|immortal time|survivor bias|informative censoring)\b",
    re.IGNORECASE,
)
CONCRETE_ACTION_RE = re.compile(
    r"\b(?:clinicians?|ministr(?:y|ies)|health systems?|programme managers?|program managers?|"
    r"policymakers?|researchers?|laboratories|hospitals?|clinics?|governments?|funders?)\b"
    r".{0,90}\b(?:implement|adopt|stop|start|screen|test|treat|refer|monitor|fund|revise|target|"
    r"allocate|train|report|collect|replace|prioritize|prioritise|evaluate|integrate)\b",
    re.IGNORECASE,
)
POPULATION_RE = re.compile(
    r"\b(?:children|adolescents|adults|women|men|patients|participants|households|mothers|infants|"
    r"newborns|pregnant women|people with|survivors|caregivers|clinicians|communities|population)\b",
    re.IGNORECASE,
)

EXCLUDED_SECTION_NAMES = {
    "references",
    "bibliography",
    "acknowledgements",
    "acknowledgments",
    "supplementary references",
}

SECTION_ALIASES = {
    "title": "title",
    "abstract": "abstract",
    "summary": "abstract",
    "background": "introduction",
    "introduction": "introduction",
    "objective": "introduction",
    "objectives": "introduction",
    "aim": "introduction",
    "aims": "introduction",
    "methods": "methods",
    "method": "methods",
    "methodology": "methods",
    "materials and methods": "methods",
    "patients and methods": "methods",
    "participants and methods": "methods",
    "study design": "methods",
    "statistical analysis": "methods",
    "results": "results",
    "findings": "results",
    "discussion": "discussion",
    "interpretation": "discussion",
    "strengths and limitations": "limitations",
    "limitations": "limitations",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "references": "references",
    "bibliography": "references",
    "acknowledgements": "acknowledgements",
    "acknowledgments": "acknowledgements",
}

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
ENGINE_VERSION = "2.0.0"


@dataclass(frozen=True)
class TextSegment:
    start: int
    end: int
    text: str
    section: str
    paragraph_index: int | None = None
    sentence_index: int | None = None


@dataclass
class Finding:
    rule_id: str
    name: str
    family: str
    dimension: str
    evidence_tier: str
    association: str
    severity: str
    confidence: float
    start: int
    end: int
    line: int
    column: int
    section: str
    paragraph_index: int | None
    sentence_index: int | None
    excerpt: str
    why: str
    revision: str
    guards_applied: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    points_override: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(self.confidence, 3)
        return data


class PatternEngine:
    """Run deterministic, section-aware scientific-writing diagnostics."""

    def __init__(self, lookup: Mapping[str, Any]):
        self.lookup = dict(lookup)
        self.rules = [r for r in self.lookup.get("rules", []) if r.get("status") == "implemented"]
        self.semantic_rules = [r for r in self.lookup.get("rules", []) if r.get("status") != "implemented"]
        self.rule_by_id = {r["id"]: r for r in self.lookup.get("rules", [])}
        self.interactions = list(self.lookup.get("interactions", []))
        self.lexicons = self.lookup.get("lexicons", {})
        self._lexicon_regex_cache: dict[str, tuple[re.Pattern[str], dict[str, str]]] = {}

        scoring = self.lookup.get("scoring", {})
        self.severity_points = scoring.get(
            "severity_points", {"info": 1.0, "low": 2.0, "medium": 4.0, "high": 7.0, "critical": 10.0}
        )
        self.evidence_weights = scoring.get("evidence_weights", {"A": 1.0, "B": 0.9, "C": 0.55})
        self.dimension_scales = scoring.get(
            "dimension_scales",
            {"style_pattern_burden": 20.0, "scientific_quality_risk": 24.0, "integrity_risk": 12.0},
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PatternEngine":
        with Path(path).open("r", encoding="utf-8") as f:
            lookup = yaml.safe_load(f)
        if not isinstance(lookup, dict):
            raise ValueError("Lookup YAML must contain a mapping at the root.")
        return cls(lookup)

    def analyze(self, text: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        metadata = dict(metadata or {})
        word_count = len(list(TOKEN_RE.finditer(text)))
        document_factor, document_confidence = self._document_confidence(word_count)

        headings = self._find_headings(text)
        paragraphs = self._paragraphs(text, headings)
        sentences = self._sentences(text, paragraphs)
        excluded_ranges = self._excluded_ranges(text, headings, paragraphs)
        quote_ranges = self._quote_ranges(text)

        context: dict[str, Any] = {
            "text": text,
            "metadata": metadata,
            "word_count": word_count,
            "document_factor": document_factor,
            "document_confidence": document_confidence,
            "headings": headings,
            "paragraphs": paragraphs,
            "sentences": sentences,
            "excluded_ranges": excluded_ranges,
            "quote_ranges": quote_ranges,
            "tokens": self._tokens(text, headings),
            "design": self._infer_design(text, metadata),
        }

        findings: list[Finding] = []
        for rule in self.rules:
            try:
                findings.extend(self._apply_rule(rule, context))
            except Exception as exc:  # Defensive: a rule should not abort the audit.
                findings.append(
                    self._make_finding(
                        rule={
                            "id": f"ENGINE-{rule.get('id', 'UNKNOWN')}",
                            "name": "Rule execution error",
                            "family": "engine",
                            "dimension": "integrity_risk",
                            "evidence_tier": "C",
                            "association": "engine",
                            "severity": "low",
                            "rationale": "A detector failed and its result was omitted.",
                            "revision": "Inspect the rule configuration and input encoding.",
                        },
                        context=context,
                        start=0,
                        end=min(len(text), 1),
                        confidence=1.0,
                        details={"failed_rule": rule.get("id"), "error": str(exc)},
                    )
                )

        findings = self._deduplicate(findings)
        interaction_findings = self._apply_interactions(findings, context)
        findings.extend(interaction_findings)
        findings.sort(key=lambda f: (f.start, -SEVERITY_ORDER.get(f.severity, 0), f.rule_id))

        scores = self._score(findings)
        sections_present = sorted({p.section for p in paragraphs if p.section not in EXCLUDED_SECTION_NAMES})

        report = {
            "report_schema_version": "1.0.0",
            "engine": {
                "name": self.lookup.get("name"),
                "engine_version": ENGINE_VERSION,
                "lookup_schema_version": self.lookup.get("schema_version"),
                "purpose": self.lookup.get("purpose"),
            },
            "document": {
                "word_count": word_count,
                "character_count": len(text),
                "sections_detected": sections_present,
                "study_design_inference": context["design"],
                "confidence_band": document_confidence,
                "short_text_factor": round(document_factor, 3),
            },
            "authorship": {
                "status": "not_inferred",
                "statement": (
                    "This audit reports revision signals and scientific-quality risks. "
                    "It is not evidence that a person used AI and must not be interpreted as an AI probability."
                ),
            },
            "scores": scores,
            "summary": {
                "finding_count": len(findings),
                "rule_count_triggered": len({f.rule_id for f in findings}),
                "by_severity": self._count_by(findings, "severity"),
                "by_family": self._count_by(findings, "family"),
                "by_section": self._count_by(findings, "section"),
            },
            "findings": [f.to_dict() for f in findings],
            "semantic_checks_not_run": [
                {"rule_id": r["id"], "name": r["name"], "requires": r.get("detector", {}).get("requires", [])}
                for r in self.semantic_rules
            ],
            "interpretation_notes": [
                "Scores are bounded revision-priority indices, not calibrated probabilities.",
                "Lexical markers are interpreted only as clusters and are suppressed in quotations and reference sections.",
                "Syntax and rhythm rules are low-weight heuristics until calibrated on a discipline- and section-matched human corpus.",
                "Human review is required for causal, statistical, and domain-specific interpretation.",
            ],
        }
        return report

    # ------------------------------------------------------------------
    # Document segmentation and context
    # ------------------------------------------------------------------

    def _find_headings(self, text: str) -> list[tuple[int, str, str]]:
        """Return (content_start, canonical_section, raw_heading)."""
        headings: list[tuple[int, str, str]] = [(0, "other", "")]
        offset = 0
        for line in text.splitlines(keepends=True):
            raw = line.strip()
            if raw:
                candidate = raw
                md = re.match(r"^#{1,6}\s+(.+?)\s*#*$", candidate)
                if md:
                    candidate = md.group(1).strip()
                candidate = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", candidate).strip()
                candidate = candidate.rstrip(":").strip()
                key = re.sub(r"\s+", " ", candidate.lower())
                canonical = SECTION_ALIASES.get(key)
                looks_like_heading = bool(md) or (
                    canonical is not None and len(candidate) <= 60 and not candidate.endswith((".", "?", "!"))
                )
                if looks_like_heading and canonical:
                    headings.append((offset + len(line), canonical, raw))
            offset += len(line)
        # If multiple headings begin at 0, retain the more specific final one.
        headings.sort(key=lambda x: x[0])
        dedup: list[tuple[int, str, str]] = []
        for item in headings:
            if dedup and dedup[-1][0] == item[0]:
                dedup[-1] = item
            else:
                dedup.append(item)
        return dedup

    @staticmethod
    def _section_at(offset: int, headings: Sequence[tuple[int, str, str]]) -> str:
        starts = [h[0] for h in headings]
        idx = bisect.bisect_right(starts, offset) - 1
        return headings[max(0, idx)][1]

    def _paragraphs(self, text: str, headings: Sequence[tuple[int, str, str]]) -> list[TextSegment]:
        paragraphs: list[TextSegment] = []
        # Mask heading lines without changing offsets. This prevents a Markdown
        # heading and its first sentence from being treated as one paragraph.
        masked_chars = list(text)
        for content_start, _canonical, _raw in headings[1:]:
            if content_start <= 0:
                continue
            line_end = content_start
            line_start = text.rfind("\n", 0, max(0, line_end - 1)) + 1
            for pos in range(line_start, line_end):
                if masked_chars[pos] != "\n":
                    masked_chars[pos] = " "
        masked = "".join(masked_chars)

        # A paragraph is any non-whitespace run bounded by one or more blank lines.
        pattern = re.compile(r"(?ms)(?<!\S)(\S.*?)(?=\n\s*\n|\Z)")
        index = 0
        for match in pattern.finditer(masked):
            start, end = match.span(1)
            raw_span = text[start:end]
            body = raw_span.strip()
            if not body:
                continue
            leading = len(raw_span) - len(raw_span.lstrip())
            trailing = len(raw_span) - len(raw_span.rstrip())
            start += leading
            end -= trailing
            section = self._section_at(start, headings)
            paragraphs.append(TextSegment(start, end, text[start:end], section, paragraph_index=index))
            index += 1
        if not paragraphs and text.strip():
            start = len(text) - len(text.lstrip())
            end = len(text.rstrip())
            paragraphs.append(TextSegment(start, end, text[start:end], self._section_at(start, headings), 0))
        return paragraphs

    def _sentences(self, text: str, paragraphs: Sequence[TextSegment]) -> list[TextSegment]:
        sentences: list[TextSegment] = []
        idx = 0
        for paragraph in paragraphs:
            local = paragraph.text
            boundaries = [0]
            for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9(\[“\"'])", local):
                boundaries.append(m.end())
            boundaries.append(len(local))
            for a, b in zip(boundaries, boundaries[1:]):
                fragment = local[a:b].strip()
                if not fragment:
                    continue
                rel_start = a + (len(local[a:b]) - len(local[a:b].lstrip()))
                rel_end = b - (len(local[a:b]) - len(local[a:b].rstrip()))
                start = paragraph.start + rel_start
                end = paragraph.start + rel_end
                sentences.append(
                    TextSegment(start, end, text[start:end], paragraph.section, paragraph.paragraph_index, idx)
                )
                idx += 1
        return sentences

    def _tokens(self, text: str, headings: Sequence[tuple[int, str, str]]) -> list[dict[str, Any]]:
        tokens: list[dict[str, Any]] = []
        for idx, match in enumerate(TOKEN_RE.finditer(text)):
            tokens.append(
                {
                    "index": idx,
                    "text": match.group(0),
                    "lower": match.group(0).lower().replace("’", "'"),
                    "start": match.start(),
                    "end": match.end(),
                    "section": self._section_at(match.start(), headings),
                }
            )
        return tokens

    def _excluded_ranges(
        self,
        text: str,
        headings: Sequence[tuple[int, str, str]],
        paragraphs: Sequence[TextSegment],
    ) -> list[tuple[int, int, str]]:
        ranges: list[tuple[int, int, str]] = []
        # Fenced code and Markdown blockquotes are examples or machinery, not manuscript voice.
        for m in re.finditer(r"(?ms)```.*?```", text):
            ranges.append((m.start(), m.end(), "fenced_code"))
        for p in paragraphs:
            if p.section in EXCLUDED_SECTION_NAMES or p.section == "references":
                ranges.append((p.start, p.end, "excluded_section"))
            if p.text.lstrip().startswith(">"):
                ranges.append((p.start, p.end, "blockquote"))
        return self._merge_ranges(ranges)

    @staticmethod
    def _quote_ranges(text: str) -> list[tuple[int, int, str]]:
        ranges: list[tuple[int, int, str]] = []
        patterns = [r"“[^”\n]{1,800}”", r'"[^"\n]{1,800}"']
        for pattern in patterns:
            for m in re.finditer(pattern, text):
                ranges.append((m.start(), m.end(), "quotation"))
        return PatternEngine._merge_ranges(ranges)

    @staticmethod
    def _merge_ranges(ranges: Sequence[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
        if not ranges:
            return []
        ordered = sorted(ranges, key=lambda x: (x[0], x[1]))
        merged: list[tuple[int, int, str]] = [ordered[0]]
        for start, end, reason in ordered[1:]:
            ps, pe, pr = merged[-1]
            if start <= pe:
                merged[-1] = (ps, max(pe, end), pr + "+" + reason)
            else:
                merged.append((start, end, reason))
        return merged

    @staticmethod
    def _overlap_reason(start: int, end: int, ranges: Sequence[tuple[int, int, str]]) -> str | None:
        for rs, re_, reason in ranges:
            if start < re_ and end > rs:
                return reason
        return None

    @staticmethod
    def _segment_containing(offset: int, segments: Sequence[TextSegment]) -> TextSegment | None:
        for segment in segments:
            if segment.start <= offset < segment.end:
                return segment
        return None

    @staticmethod
    def _document_confidence(word_count: int) -> tuple[float, str]:
        if word_count < 80:
            return 0.45, "very_low"
        if word_count < 150:
            return 0.60, "low"
        if word_count < 300:
            return 0.80, "moderate"
        return 1.00, "high"

    @staticmethod
    def _infer_design(text: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        supplied = str(metadata.get("study_design", "")).strip()
        lower = text.lower()
        observational_terms = [
            "cross-sectional", "cross sectional", "retrospective", "prospective cohort", "cohort study",
            "case-control", "case control", "ecological study", "observational study", "secondary analysis",
            "survey data",
        ]
        causal_design_terms = [
            "randomized", "randomised", "target trial", "instrumental variable", "regression discontinuity",
            "difference-in-differences", "difference in differences", "g-computation", "g computation",
            "inverse probability", "doubly robust", "causal inference", "propensity score",
        ]
        if supplied:
            supplied_lower = supplied.lower()
            is_observational = any(t in supplied_lower for t in observational_terms) or "observ" in supplied_lower
            has_causal_design = any(t in supplied_lower for t in causal_design_terms) or any(
                t in lower for t in causal_design_terms
            )
            return {
                "source": "metadata",
                "label": supplied,
                "observational": is_observational,
                "explicit_causal_design": has_causal_design,
            }
        observed = [t for t in observational_terms if t in lower]
        causal = [t for t in causal_design_terms if t in lower]
        label = "observational" if observed else ("causal_or_experimental" if causal else "not_inferred")
        return {
            "source": "text_heuristic",
            "label": label,
            "observational": bool(observed),
            "explicit_causal_design": bool(causal),
            "matched_observational_terms": observed[:5],
            "matched_causal_design_terms": causal[:5],
        }

    # ------------------------------------------------------------------
    # Rule dispatch
    # ------------------------------------------------------------------

    def _apply_rule(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        kind = rule.get("detector", {}).get("kind")
        dispatch = {
            "regex": self._detect_regex,
            "phrase": self._detect_phrase,
            "lexical_cluster": self._detect_lexical_cluster,
            "hedge_stack": self._detect_hedge_stack,
            "transition_repetition": self._detect_transition_repetition,
            "sentence_length_regularity": self._detect_sentence_regularity,
            "paragraph_length_regularity": self._detect_paragraph_regularity,
            "nominalization_density": self._detect_nominalization_density,
            "participial_clause_density": self._detect_participial_density,
            "that_subject_density": self._detect_that_subject_density,
            "results_claim_without_number": self._detect_results_claim_without_number,
            "results_number_without_uncertainty": self._detect_results_number_without_uncertainty,
            "generic_claim_without_anchor": self._detect_generic_claim_without_anchor,
            "section_anchor_deficit": self._detect_section_anchor_deficit,
            "causal_design_mismatch": self._detect_causal_design_mismatch,
            "significant_without_support": self._detect_significant_without_support,
            "percentage_without_denominator": self._detect_percentage_without_denominator,
        }
        detector = dispatch.get(kind)
        if detector is None:
            return []
        return detector(rule, context)

    @staticmethod
    def _rule_applies(rule: Mapping[str, Any], section: str) -> bool:
        sections = rule.get("sections") or ["*"]
        return "*" in sections or section in sections

    def _eligible_span(
        self,
        rule: Mapping[str, Any],
        context: Mapping[str, Any],
        start: int,
        end: int,
        *,
        exclude_quotes: bool = True,
    ) -> tuple[bool, list[str]]:
        section = self._section_at(start, context["headings"])
        if not self._rule_applies(rule, section):
            return False, ["section_scope"]
        reason = self._overlap_reason(start, end, context["excluded_ranges"])
        if reason:
            return False, [reason]
        if exclude_quotes:
            quote_reason = self._overlap_reason(start, end, context["quote_ranges"])
            if quote_reason:
                return False, [quote_reason]
        return True, []

    def _base_confidence(self, rule: Mapping[str, Any], context: Mapping[str, Any], detector_kind: str) -> float:
        tier = rule.get("evidence_tier", "C")
        base = {"A": 0.82, "B": 0.88, "C": 0.58}.get(tier, 0.60)
        if detector_kind in {"regex", "phrase", "significant_without_support", "percentage_without_denominator"}:
            base += 0.06
        if detector_kind in {
            "sentence_length_regularity", "paragraph_length_regularity", "nominalization_density",
            "participial_clause_density", "that_subject_density",
        }:
            base -= 0.08
        factor = float(context["document_factor"])
        if rule.get("dimension") == "integrity_risk":
            factor = max(factor, 0.90)
        elif rule.get("evidence_tier") == "B" and detector_kind in {"regex", "phrase"}:
            factor = max(factor, 0.78)
        return max(0.20, min(0.99, base * factor))

    def _make_finding(
        self,
        rule: Mapping[str, Any],
        context: Mapping[str, Any],
        start: int,
        end: int,
        confidence: float,
        details: Mapping[str, Any] | None = None,
        guards: Sequence[str] | None = None,
        points_override: float | None = None,
    ) -> Finding:
        text = context["text"]
        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))
        sentence = self._segment_containing(start, context["sentences"])
        paragraph = self._segment_containing(start, context["paragraphs"])
        section = self._section_at(start, context["headings"])
        line = text.count("\n", 0, start) + 1
        line_start = text.rfind("\n", 0, start) + 1
        column = start - line_start + 1
        excerpt = self._excerpt(text, start, end)
        return Finding(
            rule_id=str(rule["id"]),
            name=str(rule.get("name", rule["id"])),
            family=str(rule.get("family", "other")),
            dimension=str(rule.get("dimension", "scientific_quality_risk")),
            evidence_tier=str(rule.get("evidence_tier", "C")),
            association=str(rule.get("association", "heuristic")),
            severity=str(rule.get("severity", "low")),
            confidence=confidence,
            start=start,
            end=end,
            line=line,
            column=column,
            section=section,
            paragraph_index=paragraph.paragraph_index if paragraph else None,
            sentence_index=sentence.sentence_index if sentence else None,
            excerpt=excerpt,
            why=str(rule.get("rationale", rule.get("message", ""))),
            revision=str(rule.get("revision", "")),
            guards_applied=list(guards or []),
            details=dict(details or {}),
            points_override=points_override,
        )

    @staticmethod
    def _excerpt(text: str, start: int, end: int, width: int = 90) -> str:
        left = max(0, start - width)
        right = min(len(text), end + width)
        excerpt = re.sub(r"\s+", " ", text[left:right]).strip()
        if left > 0:
            excerpt = "…" + excerpt
        if right < len(text):
            excerpt += "…"
        return excerpt

    # ------------------------------------------------------------------
    # Detectors
    # ------------------------------------------------------------------

    def _detect_regex(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        flags = re.MULTILINE
        if detector.get("case_insensitive"):
            flags |= re.IGNORECASE
        if detector.get("dotall"):
            flags |= re.DOTALL
        findings: list[Finding] = []
        max_findings = int(detector.get("max_findings", 8))
        for pattern in detector.get("patterns", []):
            compiled = re.compile(pattern, flags)
            for match in compiled.finditer(context["text"]):
                if len(findings) >= max_findings:
                    return findings
                eligible, guards = self._eligible_span(rule, context, match.start(), match.end())
                if not eligible:
                    continue
                sentence = self._segment_containing(match.start(), context["sentences"])
                sentence_text = sentence.text if sentence else context["text"][match.start():match.end()]
                near = context["text"][max(0, match.start() - 140): min(len(context["text"]), match.end() + 140)]

                suppress_phrases = detector.get("suppress_if_near", [])
                matched_suppressor = next((p for p in suppress_phrases if p.lower() in near.lower()), None)
                if matched_suppressor:
                    continue
                if detector.get("require_no_citation_nearby") and self._has_citation(sentence_text + " " + near):
                    continue
                if detector.get("flag_if_no_anchor_in_sentence") and self._has_any_anchor(sentence_text):
                    continue
                if detector.get("flag_if_no_specific_limitation_nearby") and SPECIFIC_LIMITATION_RE.search(near):
                    continue
                if detector.get("post_filter") == "noun_stack_proxy" and not self._noun_stack_filter(match.group(0)):
                    continue

                findings.append(
                    self._make_finding(
                        rule,
                        context,
                        match.start(),
                        match.end(),
                        self._base_confidence(rule, context, "regex"),
                        details={"matched_text": match.group(0), "pattern": pattern},
                        guards=guards,
                    )
                )
        return findings

    def _detect_phrase(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        findings: list[Finding] = []
        max_findings = int(detector.get("max_findings", 8))
        flags = re.IGNORECASE if detector.get("case_insensitive", True) else 0
        for phrase in detector.get("patterns", []):
            pattern = r"(?<!\w)" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?!\w)"
            for match in re.finditer(pattern, context["text"], flags):
                if len(findings) >= max_findings:
                    return findings
                eligible, guards = self._eligible_span(rule, context, match.start(), match.end())
                if not eligible:
                    continue
                findings.append(
                    self._make_finding(
                        rule,
                        context,
                        match.start(),
                        match.end(),
                        self._base_confidence(rule, context, "phrase"),
                        details={"matched_phrase": phrase},
                        guards=guards,
                    )
                )
        return findings

    def _compiled_lexicon(self, name: str) -> tuple[re.Pattern[str], dict[str, str]]:
        if name in self._lexicon_regex_cache:
            return self._lexicon_regex_cache[name]
        aliases: Mapping[str, Sequence[str]] = self.lexicons.get(name, {})
        alias_to_canonical: dict[str, str] = {}
        escaped: list[str] = []
        for canonical, forms in aliases.items():
            for form in forms:
                normalized = form.lower().replace("’", "'")
                alias_to_canonical[normalized] = canonical
                escaped_form = re.escape(form).replace(r"\ ", r"\s+")
                escaped.append(escaped_form)
        if not escaped:
            compiled = re.compile(r"(?!x)x")
        else:
            escaped.sort(key=len, reverse=True)
            compiled = re.compile(r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)", re.IGNORECASE)
        self._lexicon_regex_cache[name] = (compiled, alias_to_canonical)
        return compiled, alias_to_canonical

    def _lexicon_occurrences(
        self,
        name: str,
        rule: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        compiled, alias_map = self._compiled_lexicon(name)
        token_starts = [t["start"] for t in context["tokens"]]
        occurrences: list[dict[str, Any]] = []
        for match in compiled.finditer(context["text"]):
            eligible, _ = self._eligible_span(rule, context, match.start(), match.end())
            if not eligible:
                continue
            raw = re.sub(r"\s+", " ", match.group(0).lower().replace("’", "'")).strip()
            canonical = alias_map.get(raw, raw)
            token_index = max(0, bisect.bisect_right(token_starts, match.start()) - 1)
            sentence = self._segment_containing(match.start(), context["sentences"])
            paragraph = self._segment_containing(match.start(), context["paragraphs"])
            occurrences.append(
                {
                    "canonical": canonical,
                    "raw": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                    "token_index": token_index,
                    "sentence_index": sentence.sentence_index if sentence else None,
                    "paragraph_index": paragraph.paragraph_index if paragraph else None,
                    "section": self._section_at(match.start(), context["headings"]),
                }
            )
        return occurrences

    def _detect_lexical_cluster(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        name = detector["lexicon"]
        occurrences = self._lexicon_occurrences(name, rule, context)
        min_distinct = int(detector.get("min_distinct", 3))
        window = int(detector.get("window_tokens", 80))
        max_findings = int(detector.get("max_findings", 4))
        scope = detector.get("scope", "window")
        findings: list[Finding] = []

        clusters: list[list[dict[str, Any]]] = []
        if scope == "sentence":
            by_sentence: dict[int | None, list[dict[str, Any]]] = {}
            for occurrence in occurrences:
                by_sentence.setdefault(occurrence["sentence_index"], []).append(occurrence)
            for group in by_sentence.values():
                if len({o["canonical"] for o in group}) >= min_distinct:
                    clusters.append(group)
        else:
            i = 0
            while i < len(occurrences):
                j = i
                distinct: set[str] = set()
                while j < len(occurrences) and occurrences[j]["token_index"] - occurrences[i]["token_index"] < window:
                    distinct.add(occurrences[j]["canonical"])
                    j += 1
                if len(distinct) >= min_distinct:
                    group = occurrences[i:j]
                    # Keep the shortest prefix that satisfies the threshold.
                    seen: set[str] = set()
                    k = 0
                    while k < len(group) and len(seen) < min_distinct:
                        seen.add(group[k]["canonical"])
                        k += 1
                    clusters.append(group[:k])
                    i = max(i + 1, i + max(1, k // 2))
                else:
                    i += 1

        used_spans: list[tuple[int, int]] = []
        for cluster in clusters:
            if len(findings) >= max_findings:
                break
            start = min(o["start"] for o in cluster)
            end = max(o["end"] for o in cluster)
            if any(start < e and end > s for s, e in used_spans):
                continue
            used_spans.append((start, end))
            distinct_terms = sorted({o["canonical"] for o in cluster})
            confidence = self._base_confidence(rule, context, "lexical_cluster")
            # More terms than threshold increase pattern-match confidence, not authorship confidence.
            confidence = min(0.96, confidence + 0.035 * max(0, len(distinct_terms) - min_distinct))
            findings.append(
                self._make_finding(
                    rule,
                    context,
                    start,
                    end,
                    confidence,
                    details={
                        "distinct_markers": distinct_terms,
                        "marker_count": len(distinct_terms),
                        "window_tokens": window,
                        "standalone_trigger": False,
                    },
                )
            )
        return findings

    def _detect_hedge_stack(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        # Reuse cluster detection, then relabel details to emphasize epistemic calibration.
        return self._detect_lexical_cluster(rule, context)

    def _detect_transition_repetition(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        openers = sorted(detector.get("openers", []), key=len, reverse=True)
        lookback = int(detector.get("lookback_paragraphs", 5))
        minimum = int(detector.get("min_occurrences", 3))
        max_findings = int(detector.get("max_findings", 6))
        eligible_paragraphs = [
            p for p in context["paragraphs"]
            if self._rule_applies(rule, p.section) and p.section not in EXCLUDED_SECTION_NAMES
        ]
        tagged: list[tuple[TextSegment, str | None]] = []
        for p in eligible_paragraphs:
            normalized = re.sub(r"^[\s\-*#\d.)]+", "", p.text).lower()
            opener = next((o for o in openers if re.match(r"^" + re.escape(o) + r"\b", normalized)), None)
            tagged.append((p, opener))
        findings: list[Finding] = []
        seen: set[tuple[str, int, int]] = set()
        for i in range(len(tagged)):
            window_items = tagged[max(0, i - lookback + 1): i + 1]
            counts: dict[str, list[TextSegment]] = {}
            for p, opener in window_items:
                if opener:
                    counts.setdefault(opener, []).append(p)
            for opener, ps in counts.items():
                if len(ps) >= minimum:
                    key = (opener, ps[0].paragraph_index or 0, ps[-1].paragraph_index or 0)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(
                        self._make_finding(
                            rule,
                            context,
                            ps[0].start,
                            ps[-1].end,
                            self._base_confidence(rule, context, "transition_repetition"),
                            details={"opener": opener, "occurrences": len(ps), "paragraph_window": lookback},
                        )
                    )
                    if len(findings) >= max_findings:
                        return findings
        return findings

    def _detect_sentence_regularity(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        lengths = [
            len(list(TOKEN_RE.finditer(s.text))) for s in context["sentences"]
            if self._rule_applies(rule, s.section) and s.section not in EXCLUDED_SECTION_NAMES
            and len(list(TOKEN_RE.finditer(s.text))) >= 4
        ]
        if len(lengths) < int(detector.get("min_sentences", 12)):
            return []
        mean = statistics.fmean(lengths)
        if mean < float(detector.get("min_mean_words", 9)):
            return []
        cv = statistics.pstdev(lengths) / mean if mean else 1.0
        if cv > float(detector.get("max_cv", 0.22)):
            return []
        start = context["sentences"][0].start if context["sentences"] else 0
        end = context["sentences"][-1].end if context["sentences"] else min(1, len(context["text"]))
        return [
            self._make_finding(
                rule,
                context,
                start,
                end,
                self._base_confidence(rule, context, "sentence_length_regularity"),
                details={"sentence_count": len(lengths), "mean_words": round(mean, 2), "coefficient_of_variation": round(cv, 3)},
            )
        ]

    def _detect_paragraph_regularity(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        paragraphs = [
            p for p in context["paragraphs"]
            if self._rule_applies(rule, p.section) and p.section not in EXCLUDED_SECTION_NAMES
        ]
        lengths = [len(list(TOKEN_RE.finditer(p.text))) for p in paragraphs if len(list(TOKEN_RE.finditer(p.text))) >= 10]
        if len(lengths) < int(detector.get("min_paragraphs", 5)):
            return []
        mean = statistics.fmean(lengths)
        if mean < float(detector.get("min_mean_words", 35)):
            return []
        cv = statistics.pstdev(lengths) / mean if mean else 1.0
        if cv > float(detector.get("max_cv", 0.25)):
            return []
        return [
            self._make_finding(
                rule,
                context,
                paragraphs[0].start,
                paragraphs[-1].end,
                self._base_confidence(rule, context, "paragraph_length_regularity"),
                details={"paragraph_count": len(lengths), "mean_words": round(mean, 2), "coefficient_of_variation": round(cv, 3)},
            )
        ]

    def _detect_nominalization_density(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        tokens = [
            t for t in context["tokens"]
            if t["section"] not in EXCLUDED_SECTION_NAMES and self._rule_applies(rule, t["section"])
        ]
        if len(tokens) < int(detector.get("min_words", 180)):
            return []
        suffixes = tuple(detector.get("suffixes", []))
        stop = {"information", "population", "association", "condition", "section", "function"}
        nominalized = [t for t in tokens if len(t["lower"]) >= 7 and t["lower"].endswith(suffixes) and t["lower"] not in stop]
        density = 100 * len(nominalized) / len(tokens)
        if density < float(detector.get("threshold_per_100", 7.5)):
            return []
        return [
            self._make_finding(
                rule,
                context,
                tokens[0]["start"],
                tokens[-1]["end"],
                self._base_confidence(rule, context, "nominalization_density"),
                details={"word_count": len(tokens), "nominalization_proxy_count": len(nominalized), "per_100_words": round(density, 2)},
            )
        ]

    def _detect_participial_density(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        sentences = [
            s for s in context["sentences"]
            if self._rule_applies(rule, s.section) and s.section not in EXCLUDED_SECTION_NAMES
        ]
        if len(sentences) < int(detector.get("min_sentences", 10)):
            return []
        excluded = {"during", "according", "including", "following", "regarding", "concerning", "using"}
        matches: list[TextSegment] = []
        for sentence in sentences:
            m = re.match(r"^[\s\"“(']*([A-Z][a-z]+ing)\b[^,]{0,80},", sentence.text)
            if m and m.group(1).lower() not in excluded:
                matches.append(sentence)
        fraction = len(matches) / len(sentences)
        if fraction < float(detector.get("threshold_fraction", 0.18)):
            return []
        return [
            self._make_finding(
                rule,
                context,
                matches[0].start,
                matches[-1].end,
                self._base_confidence(rule, context, "participial_clause_density"),
                details={"sentence_count": len(sentences), "initial_participial_count": len(matches), "fraction": round(fraction, 3)},
            )
        ]

    def _detect_that_subject_density(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        sentences = [
            s for s in context["sentences"]
            if self._rule_applies(rule, s.section) and s.section not in EXCLUDED_SECTION_NAMES
        ]
        if len(sentences) < int(detector.get("min_sentences", 10)):
            return []
        matches = [
            s for s in sentences
            if re.match(r"^[\s\"“(']*That\b.{1,100}\b(?:is|was|has|had|may|might|can|could|should|would)\b", s.text)
        ]
        fraction = len(matches) / len(sentences)
        if fraction < float(detector.get("threshold_fraction", 0.12)):
            return []
        return [
            self._make_finding(
                rule,
                context,
                matches[0].start,
                matches[-1].end,
                self._base_confidence(rule, context, "that_subject_density"),
                details={"sentence_count": len(sentences), "that_subject_count": len(matches), "fraction": round(fraction, 3)},
            )
        ]

    def _detect_results_claim_without_number(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        terms = sorted(detector.get("claim_terms", []), key=len, reverse=True)
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)
        findings: list[Finding] = []
        for sentence in context["sentences"]:
            if sentence.section != "results" or not self._rule_applies(rule, sentence.section):
                continue
            if not pattern.search(sentence.text):
                continue
            if self._has_number(sentence.text):
                continue
            eligible, guards = self._eligible_span(rule, context, sentence.start, sentence.end)
            if not eligible:
                continue
            findings.append(
                self._make_finding(
                    rule,
                    context,
                    sentence.start,
                    sentence.end,
                    self._base_confidence(rule, context, "results_claim_without_number"),
                    details={"claim_terms": sorted(set(m.group(0).lower() for m in pattern.finditer(sentence.text)))},
                    guards=guards,
                )
            )
            if len(findings) >= int(detector.get("max_findings", 12)):
                break
        return findings

    def _detect_results_number_without_uncertainty(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        terms = sorted(detector.get("effect_terms", []), key=len, reverse=True)
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)
        findings: list[Finding] = []
        for sentence in context["sentences"]:
            if sentence.section not in {"results", "abstract"} or not self._rule_applies(rule, sentence.section):
                continue
            if not pattern.search(sentence.text) or not self._has_number(sentence.text) or self._has_uncertainty(sentence.text):
                continue
            # Descriptive percentages without a comparator are handled separately.
            comparative = bool(re.search(r"\b(?:than|versus|vs\.?|compared with|difference|ratio|higher|lower|increase|decrease|reduction|effect)\b", sentence.text, re.I))
            if not comparative and not EFFECT_MEASURE_RE.search(sentence.text):
                continue
            eligible, guards = self._eligible_span(rule, context, sentence.start, sentence.end)
            if not eligible:
                continue
            findings.append(
                self._make_finding(
                    rule,
                    context,
                    sentence.start,
                    sentence.end,
                    self._base_confidence(rule, context, "results_number_without_uncertainty"),
                    details={"uncertainty_found": False},
                    guards=guards,
                )
            )
            if len(findings) >= int(detector.get("max_findings", 10)):
                break
        return findings

    def _detect_generic_claim_without_anchor(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        patterns = [re.compile(p, re.IGNORECASE) for p in detector.get("claim_patterns", [])]
        findings: list[Finding] = []
        for sentence in context["sentences"]:
            if not self._rule_applies(rule, sentence.section):
                continue
            match = next((p.search(sentence.text) for p in patterns if p.search(sentence.text)), None)
            if not match or self._has_any_anchor(sentence.text):
                continue
            eligible, guards = self._eligible_span(rule, context, sentence.start + match.start(), sentence.start + match.end())
            if not eligible:
                continue
            findings.append(
                self._make_finding(
                    rule,
                    context,
                    sentence.start + match.start(),
                    sentence.start + match.end(),
                    self._base_confidence(rule, context, "generic_claim_without_anchor"),
                    details={"anchors_found": []},
                    guards=guards,
                )
            )
            if len(findings) >= int(detector.get("max_findings", 10)):
                break
        return findings

    def _detect_section_anchor_deficit(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        target = detector.get("section", "conclusion")
        paragraphs = [p for p in context["paragraphs"] if p.section == target]
        if not paragraphs:
            return []
        section_text = "\n\n".join(p.text for p in paragraphs)
        if len(list(TOKEN_RE.finditer(section_text))) < int(detector.get("min_words", 25)):
            return []
        checks = {
            "number": self._has_number(section_text),
            "effect_measure": bool(EFFECT_MEASURE_RE.search(section_text)),
            "population": bool(POPULATION_RE.search(section_text)),
            "action": bool(CONCRETE_ACTION_RE.search(section_text)),
        }
        required = detector.get("required_any", [])
        if any(checks.get(name, False) for name in required):
            return []
        return [
            self._make_finding(
                rule,
                context,
                paragraphs[0].start,
                paragraphs[-1].end,
                self._base_confidence(rule, context, "section_anchor_deficit"),
                details={"anchors": checks},
            )
        ]

    def _detect_causal_design_mismatch(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        design = context["design"]
        if not design.get("observational") or design.get("explicit_causal_design"):
            return []
        detector = rule["detector"]
        terms = sorted(detector.get("causal_terms", []), key=len, reverse=True)
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.IGNORECASE)
        findings: list[Finding] = []
        for sentence in context["sentences"]:
            if sentence.section not in {"abstract", "results", "discussion", "conclusion", "other"}:
                continue
            for match in pattern.finditer(sentence.text):
                start = sentence.start + match.start()
                end = sentence.start + match.end()
                eligible, guards = self._eligible_span(rule, context, start, end)
                if not eligible:
                    continue
                findings.append(
                    self._make_finding(
                        rule,
                        context,
                        start,
                        end,
                        self._base_confidence(rule, context, "causal_design_mismatch"),
                        details={"causal_phrase": match.group(0), "design": design},
                        guards=guards,
                    )
                )
                if len(findings) >= int(detector.get("max_findings", 10)):
                    return findings
        return findings

    def _detect_significant_without_support(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        findings: list[Finding] = []
        pattern = re.compile(r"\bsignificant(?:ly)?\b", re.IGNORECASE)
        for sentence in context["sentences"]:
            if sentence.section not in {"results", "abstract"} or not self._rule_applies(rule, sentence.section):
                continue
            for match in pattern.finditer(sentence.text):
                # Ignore explicit non-statistical phrases in abstracts where the rule is not applicable.
                if re.search(r"\bsignificant implications?\b", sentence.text, re.I):
                    continue
                if self._has_uncertainty(sentence.text) or re.search(r"\bstatistically significant\b", sentence.text, re.I) and self._has_number(sentence.text):
                    continue
                start = sentence.start + match.start()
                end = sentence.start + match.end()
                eligible, guards = self._eligible_span(rule, context, start, end)
                if not eligible:
                    continue
                findings.append(
                    self._make_finding(
                        rule,
                        context,
                        start,
                        end,
                        self._base_confidence(rule, context, "significant_without_support"),
                        details={"numeric_anchor": self._has_number(sentence.text), "uncertainty_or_p_value": self._has_uncertainty(sentence.text)},
                        guards=guards,
                    )
                )
                if len(findings) >= int(detector.get("max_findings", 10)):
                    return findings
        return findings

    def _detect_percentage_without_denominator(self, rule: Mapping[str, Any], context: Mapping[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        findings: list[Finding] = []
        for sentence in context["sentences"]:
            if sentence.section not in {"results", "abstract"} or not self._rule_applies(rule, sentence.section):
                continue
            if DENOMINATOR_RE.search(sentence.text) or re.search(r"\bof\s+\d+\b", sentence.text, re.I):
                continue
            for match in PERCENT_RE.finditer(sentence.text):
                start = sentence.start + match.start()
                end = sentence.start + match.end()
                eligible, guards = self._eligible_span(rule, context, start, end)
                if not eligible:
                    continue
                findings.append(
                    self._make_finding(
                        rule,
                        context,
                        start,
                        end,
                        self._base_confidence(rule, context, "percentage_without_denominator"),
                        details={"percentage": match.group(0)},
                        guards=guards,
                    )
                )
                if len(findings) >= int(detector.get("max_findings", 10)):
                    return findings
        return findings

    # ------------------------------------------------------------------
    # Anchor and guard helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_number(text: str) -> bool:
        # Remove table/figure numbering before looking for empirical values.
        cleaned = re.sub(r"\b(?:Table|Figure|Fig\.?|Appendix|Section)\s+\d+[A-Za-z]?\b", "", text, flags=re.I)
        return bool(NUMBER_RE.search(cleaned))

    @staticmethod
    def _has_uncertainty(text: str) -> bool:
        return bool(UNCERTAINTY_RE.search(text))

    @staticmethod
    def _has_citation(text: str) -> bool:
        return bool(CITATION_RE.search(text))

    def _has_any_anchor(self, text: str) -> bool:
        return any(
            [
                self._has_number(text),
                self._has_citation(text),
                bool(MECHANISM_RE.search(text)),
                bool(PROPER_NOUN_PROXY_RE.search(text)),
                bool(EFFECT_MEASURE_RE.search(text)),
            ]
        )

    @staticmethod
    def _noun_stack_filter(text: str) -> bool:
        words = [w.lower() for w in TOKEN_RE.findall(text)]
        if not (6 <= len(words) <= 12):
            return False
        function_or_verb_words = {
            "the", "a", "an", "of", "to", "for", "in", "on", "with", "and", "or", "but", "from", "by",
            "is", "are", "was", "were", "be", "been", "being", "that", "which", "who", "we", "our", "this",
            "these", "those", "using", "used", "showed", "found", "reported", "among", "between", "during",
            "cause", "caused", "causes", "improve", "improved", "improves", "reduce", "reduced", "reduces",
            "increase", "increased", "increases", "suggest", "suggested", "suggests", "indicate", "indicated",
            "indicates", "demonstrate", "demonstrated", "demonstrates", "provide", "provided", "provides",
            "may", "might", "could", "would", "should", "can", "will", "has", "have", "had",
        }
        if any(w in function_or_verb_words or w.endswith("ly") for w in words):
            return False
        noun_suffixes = (
            "tion", "sion", "ment", "ness", "ity", "ism", "age", "ance", "ence", "ship", "hood",
            "ure", "ics", "ology", "graphy", "metry", "scope", "type", "ware", "work", "hood",
        )
        domain_nouns = {
            "child", "health", "outcome", "risk", "study", "data", "model", "framework", "analysis",
            "intervention", "treatment", "exposure", "measurement", "system", "programme", "program",
            "policy", "care", "research", "method", "population", "trial", "cohort", "survey", "disease",
            "mortality", "recovery", "nutrition", "infection", "evidence", "quality", "effect", "estimate",
        }
        noun_like = sum(w.endswith(noun_suffixes) or w in domain_nouns for w in words)
        return noun_like >= 4

    # ------------------------------------------------------------------
    # Interactions, deduplication, and scoring
    # ------------------------------------------------------------------

    def _apply_interactions(self, findings: Sequence[Finding], context: Mapping[str, Any]) -> list[Finding]:
        by_rule: dict[str, list[Finding]] = {}
        for finding in findings:
            by_rule.setdefault(finding.rule_id, []).append(finding)
        output: list[Finding] = []
        for interaction in self.interactions:
            groups: list[list[str]] = interaction.get("requires_any", [])
            if not groups or not all(any(rule_id in by_rule for rule_id in group) for group in groups):
                continue
            candidates = [
                [f for rule_id in group for f in by_rule.get(rule_id, [])]
                for group in groups
            ]
            selected: list[Finding] | None = None
            if interaction.get("same_sentence_or_paragraph"):
                for first in candidates[0]:
                    for second in candidates[1]:
                        same_sentence = first.sentence_index is not None and first.sentence_index == second.sentence_index
                        same_paragraph = first.paragraph_index is not None and first.paragraph_index == second.paragraph_index
                        if same_sentence or same_paragraph:
                            selected = [first, second]
                            break
                    if selected:
                        break
            else:
                selected = [group[0] for group in candidates if group]
            if not selected:
                continue
            start = min(f.start for f in selected)
            end = max(f.end for f in selected)
            pseudo_rule = {
                "id": interaction["id"],
                "name": interaction["name"],
                "family": "interaction",
                "dimension": interaction["dimension"],
                "evidence_tier": "B",
                "association": "interaction",
                "severity": interaction.get("severity", "high"),
                "rationale": interaction.get("message", ""),
                "revision": interaction.get("revision", ""),
            }
            output.append(
                self._make_finding(
                    pseudo_rule,
                    context,
                    start,
                    end,
                    confidence=min(f.confidence for f in selected),
                    details={"component_rules": [f.rule_id for f in selected]},
                    points_override=float(interaction.get("bonus_points", 0.0)),
                )
            )
        return output

    @staticmethod
    def _deduplicate(findings: Sequence[Finding]) -> list[Finding]:
        output: list[Finding] = []
        seen: set[tuple[str, int, int]] = set()
        for finding in sorted(findings, key=lambda f: (f.rule_id, f.start, f.end)):
            key = (finding.rule_id, finding.start, finding.end)
            if key in seen:
                continue
            # Collapse near-identical nested spans from the same rule.
            duplicate = False
            for prior in output[-20:]:
                if prior.rule_id != finding.rule_id:
                    continue
                overlap = max(0, min(prior.end, finding.end) - max(prior.start, finding.start))
                smaller = max(1, min(prior.end - prior.start, finding.end - finding.start))
                if overlap / smaller >= 0.85:
                    duplicate = True
                    break
            if not duplicate:
                output.append(finding)
                seen.add(key)
        return output

    def _score(self, findings: Sequence[Finding]) -> dict[str, Any]:
        by_dimension: dict[str, dict[str, list[Finding]]] = {
            dimension: {} for dimension in self.dimension_scales
        }
        for finding in findings:
            by_dimension.setdefault(finding.dimension, {}).setdefault(finding.rule_id, []).append(finding)

        scores: dict[str, Any] = {}
        for dimension, scale in self.dimension_scales.items():
            raw = 0.0
            rule_contributions: list[dict[str, Any]] = []
            for rule_id, items in by_dimension.get(dimension, {}).items():
                items = sorted(items, key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), -f.confidence, f.start))
                contribution = 0.0
                for rank, finding in enumerate(items, start=1):
                    if finding.points_override is not None:
                        points = finding.points_override
                    else:
                        severity = float(self.severity_points.get(finding.severity, 2.0))
                        evidence = float(self.evidence_weights.get(finding.evidence_tier, 0.55))
                        points = severity * evidence * finding.confidence
                    contribution += points / math.sqrt(rank)
                raw += contribution
                rule_contributions.append({"rule_id": rule_id, "points": round(contribution, 3), "findings": len(items)})
            score = 100.0 * (1.0 - math.exp(-raw / float(scale))) if scale else 0.0
            scores[dimension] = {
                "score": round(min(100.0, score), 1),
                "band": self._score_band(score),
                "weighted_points": round(raw, 3),
                "top_contributors": sorted(rule_contributions, key=lambda x: x["points"], reverse=True)[:8],
                "interpretation": "revision_priority_not_probability",
            }
        return scores

    @staticmethod
    def _score_band(score: float) -> str:
        if score < 20:
            return "low"
        if score < 40:
            return "guarded"
        if score < 60:
            return "moderate"
        if score < 80:
            return "high"
        return "very_high"

    @staticmethod
    def _count_by(findings: Sequence[Finding], attr: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in findings:
            key = str(getattr(finding, attr))
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def report_to_markdown(report: Mapping[str, Any], max_findings: int | None = None) -> str:
    """Render an audit report as review-friendly Markdown."""
    lines: list[str] = []
    lines.append("# Scientific Writing Pattern Audit")
    lines.append("")
    lines.append(
        "**Authorship status: not inferred.** This report identifies revision signals and scientific-quality risks; "
        "its scores are not probabilities of AI use."
    )
    lines.append("")
    doc = report["document"]
    lines.append(
        f"Document: **{doc['word_count']} words** · confidence: **{doc['confidence_band']}** · "
        f"sections: {', '.join(doc['sections_detected']) or 'none detected'}"
    )
    lines.append("")
    lines.append("## Scores")
    lines.append("")
    lines.append("| Dimension | Score | Band | Meaning |")
    lines.append("|---|---:|---|---|")
    labels = {
        "style_pattern_burden": "Style pattern burden",
        "scientific_quality_risk": "Scientific quality risk",
        "integrity_risk": "Integrity risk",
    }
    for key, value in report["scores"].items():
        lines.append(
            f"| {labels.get(key, key)} | {value['score']:.1f}/100 | {value['band']} | Revision priority, not AI probability |"
        )
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    findings = list(report.get("findings", []))
    findings.sort(key=lambda f: (-SEVERITY_ORDER.get(f["severity"], 0), -f["confidence"], f["line"]))
    if max_findings is not None:
        findings = findings[:max_findings]
    if not findings:
        lines.append("No configured patterns were triggered.")
    else:
        for finding in findings:
            lines.append(
                f"### {finding['rule_id']} · {finding['name']} [{finding['severity']}, {finding['evidence_tier']}]"
            )
            lines.append("")
            lines.append(
                f"**Location:** {finding['section']}, line {finding['line']} · "
                f"**confidence:** {finding['confidence']:.2f}"
            )
            lines.append("")
            lines.append(f"> {finding['excerpt']}")
            lines.append("")
            lines.append(f"**Why:** {finding['why']}")
            lines.append("")
            lines.append(f"**Revision:** {finding['revision']}")
            lines.append("")
    if report.get("semantic_checks_not_run"):
        lines.append("## Semantic checks not run")
        lines.append("")
        lines.append(
            "These checks require claim extraction, entity linking, or domain models and are intentionally not faked by the deterministic engine: "
            + ", ".join(item["rule_id"] for item in report["semantic_checks_not_run"])
            + "."
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def default_lookup_path() -> Path:
    """Resolve the bundled lookup in source, --target, or virtualenv installs."""
    filename = "scientific_pattern_lookup_v2.yaml"
    candidates = [
        Path(__file__).with_name(filename),
        Path(sys.prefix) / filename,
        Path(sys.prefix) / "share" / "scientific-pattern-engine" / filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _load_metadata(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Metadata JSON must contain an object.")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit scientific prose for revision-worthy style, quality, and integrity patterns."
    )
    parser.add_argument("input", help="UTF-8 manuscript text/Markdown file, or - for stdin")
    parser.add_argument(
        "--lookup",
        default=str(default_lookup_path()),
        help="Path to the YAML lookup",
    )
    parser.add_argument("--metadata", help="Optional JSON object, e.g. {\"study_design\": \"cross-sectional\"}")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--output", help="Output file; stdout when omitted")
    parser.add_argument("--max-findings", type=int, default=None, help="Limit findings in Markdown output")
    args = parser.parse_args(argv)

    try:
        text = _read_text(args.input)
        metadata = _load_metadata(args.metadata)
        engine = PatternEngine.from_yaml(args.lookup)
        report = engine.analyze(text, metadata)
        if args.format == "json":
            rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        else:
            rendered = report_to_markdown(report, max_findings=args.max_findings)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
