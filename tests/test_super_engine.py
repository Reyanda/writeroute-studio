from __future__ import annotations

import io
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from writeroute.super_engine import SuperEngine
from stats_brain import StatsBrainReviewer, AuctorBridge, ReviewContext
from scientific_pattern_engine import PatternEngine, default_lookup_path
from lucid_sci import LucidSciEvaluator
from app import app


SAMPLE_MANUSCRIPT = """
In today's rapidly evolving scientific landscape, it is critically important to note that our groundbreaking study aims to delve into the multifaceted mechanisms of neonatal mortality.
The clinical intervention caused a massive and highly significant reduction in 30-day mortality (p < 0.001).
We evaluated an observational cohort of 1,200 infants across 14 health centers in Sub-Saharan Africa. The odds ratio was 0.58 (95% CI: 0.44 to 0.76).
However, unmeasured confounding and residual selection bias cannot be entirely ruled out.
"""


def test_super_engine_audit():
    engine = SuperEngine()
    result = engine.audit_text(
        text=SAMPLE_MANUSCRIPT,
        section="results",
        study_design="observational_cohort",
        target_guideline="CONSORT",
    )

    assert result.summary.overall_score > 0
    assert result.summary.overall_score <= 100
    assert result.summary.total_findings_count > 0
    assert len(result.statistical_findings) >= 0
    assert len(result.pattern_findings) >= 0
    assert len(result.lucid_findings) >= 0
    assert "score" in result.guideline_checks


def test_stats_brain_standalone():
    reviewer = StatsBrainReviewer()
    ctx = ReviewContext(
        manuscript_text=SAMPLE_MANUSCRIPT,
        manifest={"study_design": "observational_cohort", "section": "results"},
    )
    report = reviewer.review(ctx)
    assert len(report.dimension_scores) > 0
    assert report.release_status in ("ready", "minor_revision", "author_review_required", "blocked")

    packet = AuctorBridge.packet(report)
    assert "qc" in packet
    assert "commentary" in packet



def test_scientific_pattern_engine_standalone():
    lookup_path = default_lookup_path()
    engine = PatternEngine.from_yaml(lookup_path)
    res = engine.analyze(SAMPLE_MANUSCRIPT, metadata={"section": "results"})
    assert "scores" in res
    assert isinstance(res["findings"], list)


def test_lucid_sci_evaluator():
    evaluator = LucidSciEvaluator()
    res = evaluator.evaluate(SAMPLE_MANUSCRIPT)
    assert "score" in res
    assert res["findings_count"] > 0
    assert any(f["category"] == "ai_slop_phrases" for f in res["findings"])


def test_api_endpoints():
    client = TestClient(app)

    # 1. Health
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["superEngine"] is True

    # 2. Super Audit
    res = client.post("/api/super-audit", json={
        "text": SAMPLE_MANUSCRIPT,
        "section": "results",
        "study_design": "observational_cohort",
        "target_guideline": "CONSORT",
    })
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert data["summary"]["overall_score"] > 0

    # 3. Stats Review
    res = client.post("/api/stats-review", json={
        "text": SAMPLE_MANUSCRIPT,
        "study_design": "observational_cohort",
        "section": "results",
    })
    assert res.status_code == 200
    data = res.json()
    assert "report" in data
    assert "auctor_packet" in data

    # 4. Pattern Audit
    res = client.post("/api/pattern-audit", json={
        "text": SAMPLE_MANUSCRIPT,
        "section": "results",
    })
    assert res.status_code == 200
    assert "scores" in res.json()


    # 5. Lucid Lint
    res = client.post("/api/lucid-lint", json={"text": SAMPLE_MANUSCRIPT})
    assert res.status_code == 200
    assert "score" in res.json()

    # 6. Guidelines
    res = client.get("/api/guidelines")
    assert res.status_code == 200
    assert len(res.json()["guidelines"]) > 0
