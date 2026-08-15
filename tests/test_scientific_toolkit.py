"""Automated Test Suite for WriteRoute Formalized Scientific Toolkit Suite.

Tests ScientificTableEngine, ConsolidatedSlopEngine, ReportingGuidelinesEngine,
ScientificEquationEngine, and their respective REST API endpoints in app.py.
"""

import pytest
from fastapi.testclient import TestClient
from app import app
from writeroute.toolkit.table_engine import ScientificTableEngine, format_scientific_table, TableFormatOptions
from writeroute.toolkit.slop_engine import ConsolidatedSlopEngine, run_slop_audit
from writeroute.toolkit.guidelines_engine import ReportingGuidelinesEngine, run_guideline_audit
from writeroute.toolkit.equations_engine import ScientificEquationEngine

client = TestClient(app)


def test_scientific_table_engine():
    raw_csv = """Variable, Treated (n=120), Control (n=120), p-value
Age (years), 64.2 ± 8.1, 63.8 ± 7.9, 0.68
Mortality (%), 12 (10.0%), 28 (23.3%), 0.007
Odds Ratio, 0.36 (0.17-0.76), Reference, 0.007"""

    headers, rows = ScientificTableEngine.parse_csv_or_tsv(raw_csv)
    assert len(headers) == 4
    assert len(rows) == 3
    assert headers[0] == "Variable"

    # Test Three-Line HTML Table
    opts = TableFormatOptions(caption="Table 1: Baseline Characteristics", notes="Data presented as n (%)")
    html = ScientificTableEngine.to_html(headers, rows, opts)
    assert '<table class="scientific-table three-line-table">' in html
    assert "<caption><strong>Table 1: Baseline Characteristics</strong></caption>" in html
    assert '<th style="text-align: right">p-value</th>' in html

    # Test LaTeX Booktabs Table
    tex = ScientificTableEngine.to_latex_booktabs(headers, rows, opts)
    assert "\\toprule" in tex
    assert "\\midrule" in tex
    assert "\\bottomrule" in tex
    assert "\\caption{Table 1: Baseline Characteristics}" in tex

    # Test Markdown Table
    md = ScientificTableEngine.to_markdown(headers, rows, opts)
    assert "| Variable | Treated (n=120) | Control (n=120) | p-value |" in md
    assert "---:" in md  # Right alignment for numeric columns


def test_consolidated_slop_engine():
    slop_text = "In today's rapidly evolving technological landscape, it is important to remember that we can move the needle and unlock synergies."
    rep = ConsolidatedSlopEngine.audit(slop_text)
    assert rep.score < 80
    assert len(rep.findings) > 0


def test_reporting_guidelines_engine():
    trial_text = """
    A randomised controlled trial evaluating drug efficacy.
    Methods: Parallel 1:1 allocation ratio was computer-generated with sealed opaque envelopes.
    Primary outcome was mortality at 30 days. Sample size calculation assumed 80% power.
    Results: Enrolled 500 patients. Hazard ratio was 0.65 (95% CI 0.45-0.92). No serious adverse events observed.
    Registration: NCT04829102 at ClinicalTrials.gov.
    """
    rep = ReportingGuidelinesEngine.audit(trial_text, "consort")
    assert rep.guideline == "CONSORT"
    assert rep.compliance_score >= 60
    assert rep.compliant_count > 5


def test_equations_engine():
    templates = ScientificEquationEngine.list_templates()
    assert len(templates) >= 5
    ids = [t["id"] for t in templates]
    assert "odds_ratio" in ids
    assert "aipw_estimator" in ids

    # Test LaTeX validation
    valid_res = ScientificEquationEngine.validate_latex(r"OR = \frac{a \cdot d}{b \cdot c}")
    assert valid_res.valid is True
    assert valid_res.error is None

    invalid_res = ScientificEquationEngine.validate_latex(r"OR = \frac{a \cdot d}{b \cdot c")
    assert invalid_res.valid is False
    assert "Unbalanced curly braces" in invalid_res.error


def test_toolkit_api_endpoints():
    # 1. Format table
    res = client.post("/api/toolkit/tables/format", json={
        "raw_data": "Treatment, Response Rate, p-value\nDrug A, 78%, 0.01\nPlacebo, 45%, Reference",
        "caption": "Table 1. Efficacy Results",
        "label": "tab:efficacy",
        "notes": "p < 0.05",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["row_count"] == 2
    assert "\\toprule" in data["latex"]
    assert "three-line-table" in data["html"]

    # 2. Slop audit
    res = client.post("/api/toolkit/slop/audit", json={
        "text": "The patient cohort exhibited a doubly robust AIPW estimated treatment effect of 0.42."
    })
    assert res.status_code == 200
    data = res.json()
    assert "score" in data

    # 3. Guidelines audit
    res = client.post("/api/toolkit/guidelines/audit", json={
        "text": "We performed a systematic review searching PubMed and Embase. Two independent reviewers extracted data.",
        "guideline": "prisma"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["guideline"] == "PRISMA"

    # 4. Equation templates
    res = client.get("/api/toolkit/equations/templates")
    assert res.status_code == 200
    assert len(res.json()["templates"]) >= 5

    # 5. Equation validate
    res = client.post("/api/toolkit/equations/validate", json={
        "latex": r"\hat{\theta} = \sum_{i=1}^n w_i X_i"
    })
    assert res.status_code == 200
    assert res.json()["valid"] is True
