import pytest
from fastapi.testclient import TestClient
from app import app
from writeroute.super_engine import SuperEngine

client = TestClient(app)

SAMPLE_SLOPPY = """
In today's fast-paced world, it is crucial to remember that our prospective observational cohort enrolled 1,200 infants.
We leverage cutting-edge solutions to move the needle and empower users with low-hanging fruit.
"Prior et al. demonstrated a 15% reduction in mortality," according to Smith (2024).
We utilized a doubly robust estimator to evaluate the hazard ratio of 0.62 (95% CI: 0.48 to 0.81; p < 0.001).
"""

SAMPLE_CLEAN = """
The prospective observational cohort enrolled 1,200 infants across 14 health centers in Sub-Saharan Africa.
The intervention reduced neonatal mortality with an adjusted hazard ratio of 0.62 (95% CI: 0.48 to 0.81; p < 0.001).
"""


def test_aiwd_packs_endpoint():
    res = client.get("/api/aiwd/packs")
    assert res.status_code == 200
    data = res.json()
    assert "packs" in data
    assert "families" in data
    assert data["feature_count"] > 0
    assert "LexicalPatterns" in data["families"]
    assert "EpistemicStance" in data["families"]
    assert "ProbabilisticFeatures" in data["families"]



def test_aiwd_scan_endpoint():
    res = client.post("/api/aiwd/scan", json={"text": SAMPLE_SLOPPY, "genre": "academic"})
    assert res.status_code == 200
    data = res.json()
    assert "detectionResult" in data
    assert "tokenCount" in data
    assert data["tokenCount"] > 10
    # Check allowlist exemptions (e.g. doubly robust)
    assert "allowListExemptions" in data
    # Check reported voice discounts
    assert "reportedVoiceDiscounts" in data


def test_aiwd_clean_endpoint_preservation():
    res = client.post("/api/aiwd/clean", json={"text": SAMPLE_SLOPPY})
    assert res.status_code == 200
    data = res.json()
    assert "cleaned_text" in data
    assert data["passes_preservation_gate"] is True
    # Numbers and CIs must remain intact
    assert "1,200" in data["cleaned_text"] or "1200" in data["cleaned_text"]
    assert "0.62" in data["cleaned_text"]


def test_super_engine_with_aiwd():
    engine = SuperEngine()
    result = engine.audit_text(SAMPLE_CLEAN, section="results", study_design="observational_cohort")
    assert result.summary.aiwd_score >= 0
    assert result.summary.overall_score >= 0
    assert result.aiwd_findings is not None
    assert "detection" in result.aiwd_findings


def test_static_writing_master_ui():
    res = client.get("/studio.html")
    assert res.status_code == 200
    html = res.text
    assert 'data-panel="writingmaster"' in html
    assert 'id="panel-writingmaster"' in html
    assert 'id="runAiwdAuditBtn"' in html
    assert 'id="applyAiwdCleanBtn"' in html
