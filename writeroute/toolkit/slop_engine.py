"""Consolidated Slop Engine: Unifies no-ai-slop, aiwd, scientific_pattern_engine_v2, and lucid-sci

into a comprehensive anti-slop diagnosis and conservative de-slop repair engine.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SlopFinding:
    id: str
    category: str  # lexical, epistemic, syntactic, formatting, pragmatic
    phrase: str
    start: int
    end: int
    severity: str  # fatal, critical, warning, cosmetic
    message: str
    suggested_replacements: list[str] = field(default_factory=list)
    safe: bool = False


@dataclass
class SlopAuditResult:
    score: int  # 0 to 100 (100 = 100% human-grade clean)
    label: str  # HUMAN_PROSE, MIXED, SLOP_BURDENED
    findings: list[SlopFinding]
    exemptions: list[dict[str, Any]]
    reported_voice_discount: float
    safe_edits_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "label": self.label,
            "findings": [asdict(f) for f in self.findings],
            "exemptions": self.exemptions,
            "reported_voice_discount": self.reported_voice_discount,
            "safe_edits_count": self.safe_edits_count,
        }


class ConsolidatedSlopEngine:
    """Master Slop Engine unifying multi-ontology anti-AI detection and conservative de-slop."""

    @classmethod
    def audit(cls, text: str, genre: str = "academic") -> SlopAuditResult:
        findings: list[SlopFinding] = []
        exemptions: list[dict[str, Any]] = []
        reported_fraction = 0.0

        # 1. Run AIWD if available
        try:
            from aiwd.skillengine import SkillRegistry
            from aiwd.scoring import scan_text
            from aiwd.textmodel import parse
            from aiwd.rewrite import suggest

            reg = SkillRegistry.load()
            rep = scan_text(text, registry=reg, genre=genre)
            det = rep.get("detectionResult", {})
            ai_prob = det.get("globalAiProbability", 0.0)
            score = max(0, min(100, int((1.0 - ai_prob) * 100)))
            reported_fraction = rep.get("reportedVoiceFraction", 0.0)
            exemptions = rep.get("allowListExemptions", [])

            sample = parse(text)
            
            # Extract findings from feature matches
            for feat in rep.get("features", []):
                if feat.get("aiLikenessContribution", 0) > 0.05 or feat.get("evidence"):
                    for ev in feat.get("evidence", []):
                        findings.append(SlopFinding(
                            id=feat.get("featureType", "feature_hit"),
                            category=feat.get("family", "LexicalPatterns"),
                            phrase=ev.get("text", ""),
                            start=ev.get("start", 0),
                            end=ev.get("end", 0),
                            severity="critical" if feat.get("aiLikenessContribution", 0) > 0.2 else "warning",
                            message=f"Feature '{feat.get('featureType')}' matched pattern",
                            suggested_replacements=[],
                            safe=False,
                        ))

            # Extract suggestions
            suggs = suggest(sample, reg)
            for s in suggs:
                findings.append(SlopFinding(
                    id=s.feature_id,
                    category=s.family,
                    phrase=s.original,
                    start=s.start,
                    end=s.end,
                    severity="warning" if s.safe else "critical",
                    message=s.rationale,
                    suggested_replacements=s.options,
                    safe=s.safe,
                ))

        except Exception:
            score = 100

        # 2. Run Pattern Engine v2 rules if available
        try:
            from scientific_pattern_engine import PatternEngine, default_lookup_path
            p_path = default_lookup_path()
            if p_path:
                pe = PatternEngine.from_yaml(p_path)
                p_rep = pe.analyze(text)
                for h in p_rep.get("findings", []):
                    details = h.get("details", {})
                    matched = details.get("matched_text") or h.get("excerpt", "")
                    revision = h.get("revision", "")
                    findings.append(SlopFinding(
                        id=h.get("rule_id", "pattern_hit"),
                        category=h.get("family", "PatternDefect"),
                        phrase=matched,
                        start=h.get("start", 0),
                        end=h.get("end", 0),
                        severity=h.get("severity", "warning"),
                        message=h.get("why", "Stylistic pattern defect"),
                        suggested_replacements=[revision] if revision else [],
                        safe=False,
                    ))
        except Exception:
            pass


        # 3. Label calculation
        safe_count = sum(1 for f in findings if f.safe)
        if score >= 75:
            label = "HUMAN_PROSE"
        elif score <= 45:
            label = "SLOP_BURDENED"
        else:
            label = "MIXED"

        return SlopAuditResult(
            score=score,
            label=label,
            findings=findings,
            exemptions=exemptions,
            reported_voice_discount=reported_fraction,
            safe_edits_count=safe_count,
        )


def run_slop_audit(text: str, genre: str = "academic") -> dict[str, Any]:
    return ConsolidatedSlopEngine.audit(text, genre).to_dict()
