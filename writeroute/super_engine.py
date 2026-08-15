from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# Sub-engine imports
try:
    from auctor_engine import (
        AcademicWritingEngine,
        Channel,
        ChannelBundle,
        EvidencePacket,
        ManuscriptDocxEngine,
        ReportingGuidelineRegistry,
    )
except ImportError:
    AcademicWritingEngine = None
    ManuscriptDocxEngine = None
    ReportingGuidelineRegistry = None

try:
    from stats_brain import AuctorBridge, ReviewContext, ReviewReport, StatsBrainReviewer
except ImportError:
    StatsBrainReviewer = None
    AuctorBridge = None

try:
    from scientific_pattern_engine import PatternEngine, default_lookup_path
except ImportError:
    PatternEngine = None
    default_lookup_path = None


try:
    from lucid_sci import LucidSciEvaluator
except ImportError:
    LucidSciEvaluator = None

try:
    from writeroute.audit import run_audit
    from writeroute.candidates import generate_repair_candidates
    from writeroute.model import AuditReport as ProseAuditReport
except ImportError:
    run_audit = None
    generate_repair_candidates = None


@dataclass
class SuperAuditSummary:
    overall_score: int
    statistical_score: int
    style_burden_score: int
    prose_quality_score: int
    lucid_clarity_score: int
    guidelines_score: int
    total_findings_count: int
    critical_findings_count: int
    fatal_findings_count: int


@dataclass
class SuperAuditResult:
    summary: SuperAuditSummary
    statistical_findings: list[dict[str, Any]]
    pattern_findings: list[dict[str, Any]]
    prose_findings: list[dict[str, Any]]
    lucid_findings: list[dict[str, Any]]
    guideline_checks: dict[str, Any]
    repair_candidates: list[dict[str, Any]]
    auctor_evidence_packet: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": asdict(self.summary),
            "statistical_findings": self.statistical_findings,
            "pattern_findings": self.pattern_findings,
            "prose_findings": self.prose_findings,
            "lucid_findings": self.lucid_findings,
            "guideline_checks": self.guideline_checks,
            "repair_candidates": self.repair_candidates,
            "auctor_evidence_packet": self.auctor_evidence_packet,
        }


class SuperEngine:
    """WriteRoute Unified Super Engine: Integrates Prose, STATS-BRAIN, Auctor,

    Scientific Pattern Engine, and LUCID-SCI into one seamless scientific
    document intelligence pipeline.
    """

    def __init__(self) -> None:
        self.stats_reviewer = StatsBrainReviewer() if StatsBrainReviewer else None
        lookup_p = default_lookup_path() if default_lookup_path else None
        self.pattern_engine = PatternEngine.from_yaml(lookup_p) if PatternEngine and lookup_p else None
        self.lucid_evaluator = LucidSciEvaluator() if LucidSciEvaluator else None
        self.guideline_registry = ReportingGuidelineRegistry() if ReportingGuidelineRegistry else None
        self.docx_engine = ManuscriptDocxEngine() if ManuscriptDocxEngine else None
        self.auctor_pipeline = AcademicWritingEngine() if AcademicWritingEngine else None


    def audit_text(
        self,
        text: str,
        section: str = "general",
        study_design: str | None = None,
        target_guideline: str | None = None,
    ) -> SuperAuditResult:
        """Run all five analytical engines on input text."""
        # 1. Prose Engine Audit
        prose_findings = []
        repair_candidates = []
        prose_score = 100
        if run_audit:
            try:
                p_report = run_audit(text)
                prose_findings = [asdict(f) if hasattr(f, "__dataclass_fields__") else f for f in p_report.findings]
                if generate_repair_candidates:
                    repair_candidates = [
                        asdict(c) if hasattr(c, "__dataclass_fields__") else c
                        for c in generate_repair_candidates(text, p_report)
                    ]
                prose_score = max(0, 100 - len(prose_findings) * 5)
            except Exception as e:
                prose_findings.append({"id": "prose_audit_err", "message": str(e), "severity": "warning"})

        # 2. STATS-BRAIN Review
        stats_findings = []
        stats_score = 100
        auctor_packet_dict = None
        if self.stats_reviewer:
            try:
                s_ctx = ReviewContext(
                    manuscript_text=text,
                    manifest={"study_design": study_design or "observational_cohort", "section": section},
                )
                s_report = self.stats_reviewer.review(s_ctx)
                if s_report.dimension_scores:
                    stats_score = int(sum(s_report.dimension_scores.values()) / len(s_report.dimension_scores))
                else:
                    stats_score = 100
                stats_findings = [f.to_dict() if hasattr(f, "to_dict") else asdict(f) for f in s_report.findings]
                
                # Auctor Bridge
                if AuctorBridge:
                    auctor_packet_dict = AuctorBridge.packet(s_report)
            except Exception as e:
                stats_findings.append({"rule_id": "stats_err", "summary": str(e), "severity": "query"})


        # 3. Scientific Pattern Engine
        pattern_findings = []
        style_score = 100
        if self.pattern_engine:
            try:
                p_dict = self.pattern_engine.analyze(text, metadata={"section": section})
                p_scores = p_dict.get("scores", {})
                style_burden = float(p_scores.get("style_pattern_burden", {}).get("score", 0.0))
                style_score = max(0, 100 - int(style_burden))
                for f in p_dict.get("findings", []):
                    pattern_findings.append({
                        "rule_id": f.get("rule_id", "PATTERN"),
                        "category": f.get("category", "style"),
                        "severity": f.get("severity", "medium"),
                        "message": f.get("message", ""),
                        "start": f.get("start", 0),
                        "end": f.get("end", 0),
                        "matched_text": f.get("matched_text", ""),
                        "suggestion": f.get("suggestion", ""),
                    })
            except Exception as e:
                pattern_findings.append({"rule_id": "pattern_err", "message": str(e), "severity": "minor"})


        # 4. LUCID-SCI Evaluation
        lucid_findings = []
        lucid_score = 100
        if self.lucid_evaluator:
            try:
                l_res = self.lucid_evaluator.evaluate(text)
                lucid_score = l_res.get("score", 100)
                lucid_findings = l_res.get("findings", [])
            except Exception as e:
                lucid_findings.append({"id": "lucid_err", "message": str(e), "severity": "warning"})

        # 5. Reporting Guidelines
        guideline_checks = {}
        guidelines_score = 100
        if self.guideline_registry and target_guideline:
            try:
                g_key = target_guideline.lower()
                profile = self.guideline_registry.profiles.get(g_key)
                if profile:
                    proxies = profile.get("coverage_proxies", [])
                    issues = self.guideline_registry.audit_section(text, section=section, profile_ids=[g_key])
                    total_proxies = max(1, len(proxies))
                    compliant_count = max(0, total_proxies - len(issues))
                    guidelines_score = int((compliant_count / total_proxies) * 100)
                    guideline_checks = {
                        "guideline": target_guideline.upper(),
                        "score": guidelines_score,
                        "title": profile.get("title", target_guideline),
                        "issues": [asdict(issue) for issue in issues],
                    }
                else:
                    guideline_checks = {"guideline": target_guideline.upper(), "score": 100, "issues": []}
            except Exception:
                guideline_checks = {"guideline": target_guideline.upper(), "score": 100, "issues": []}


        # Calculate Unified Overall Score & Severities
        total_findings = len(prose_findings) + len(stats_findings) + len(pattern_findings) + len(lucid_findings)
        fatal_count = sum(1 for f in stats_findings if f.get("severity") == "fatal")
        crit_count = (
            sum(1 for f in stats_findings if f.get("severity") in ("critical", "major"))
            + sum(1 for f in pattern_findings if f.get("severity") in ("critical", "high"))
            + sum(1 for f in lucid_findings if f.get("severity") == "critical")
        )

        overall_score = int(
            stats_score * 0.35 + style_score * 0.20 + prose_score * 0.20 + lucid_score * 0.15 + (guidelines_score * 0.10 if target_guideline else 10)
        )
        overall_score = max(0, min(100, overall_score))

        summary = SuperAuditSummary(
            overall_score=overall_score,
            statistical_score=stats_score,
            style_burden_score=style_score,
            prose_quality_score=prose_score,
            lucid_clarity_score=lucid_score,
            guidelines_score=guidelines_score,
            total_findings_count=total_findings,
            critical_findings_count=crit_count,
            fatal_findings_count=fatal_count,
        )

        return SuperAuditResult(
            summary=summary,
            statistical_findings=stats_findings,
            pattern_findings=pattern_findings,
            prose_findings=prose_findings,
            lucid_findings=lucid_findings,
            guideline_checks=guideline_checks,
            repair_candidates=repair_candidates,
            auctor_evidence_packet=auctor_packet_dict,
        )

    def process_docx(
        self,
        docx_path: str | Path,
        output_path: str | Path,
        evidence_packet: dict[str, Any] | None = None,
        author: str = "WriteRoute SuperEngine",
        apply_safe_edits: bool = True,
        track_changes: bool = True,
        add_comments: bool = True,
    ) -> dict[str, Any]:
        """Apply tracked changes, comments, and evidence repairs to a Word DOCX file using Auctor ManuscriptDocxEngine."""
        if not self.docx_engine:
            raise RuntimeError("Auctor ManuscriptDocxEngine is not available.")

        return self.docx_engine.prepare(
            source=Path(docx_path),
            output=Path(output_path),
            apply_safe_edits=apply_safe_edits,
            track_changes=track_changes,
            add_comments=add_comments,
            author=author,
        )

