from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from app import app
from writeroute.latex_export import markdown_to_latex

client = TestClient(app)

SAMPLE_BIBTEX = """@article{smith2024neonatal,
  author = {Smith, Jane and Doe, John},
  title = {Neonatal health interventions in Sub-Saharan Africa},
  journal = {The Lancet},
  year = {2024},
  volume = {403},
  pages = {112-124},
  doi = {10.1016/S0140-6736(24)00123-4}
}
@article{turner2023clinical,
  author = {Turner, Alice},
  title = {Randomized evaluation of clinical algorithms},
  journal = {New England Journal of Medicine},
  year = {2023},
  volume = {388},
  pages = {45-56}
}
"""

SAMPLE_MANUSCRIPT_MD = """# Neonatal Survival in Cohort Studies

## Abstract
Improved water sources included piped water, boreholes and protected springs.

### Methods & Materials
We evaluated an observational cohort of 1,200 infants across 14 health centers.

| Metric | Group A | Group B |
| --- | --- | --- |
| Sample Size | 600 | 600 |
| Odds Ratio | 0.58 | 1.00 |

- Primary outcome: 30-day mortality
- Secondary outcome: readmission

> Note: unmeasured confounding and residual selection bias cannot be ruled out.

```python
def compute_or(a, b, c, d):
    return (a * d) / (b * c)
```
"""

def test_latex_converter_markdown():
    tex = markdown_to_latex(SAMPLE_MANUSCRIPT_MD, title="Neonatal Survival")
    assert "\\documentclass" in tex
    assert "\\section{Neonatal Survival in Cohort Studies}" in tex
    assert "\\subsection{Abstract}" in tex
    assert "\\subsubsection{Methods \\& Materials}" in tex
    assert "\\begin{table}" in tex
    assert "\\begin{verbatim}" in tex
    assert "\\end{verbatim}" in tex
    assert "\\item" in tex


def test_export_endpoint_latex():
    res = client.post("/api/export", json={
        "text": SAMPLE_MANUSCRIPT_MD,
        "filename": "neonatal-study",
        "format": "tex"
    })
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/x-tex")
    assert 'attachment; filename="neonatal-study.tex"' in res.headers["content-disposition"]
    body = res.text
    assert "\\documentclass" in body
    assert "\\begin{document}" in body
    assert "\\end{document}" in body


def test_citation_formatting_apa():
    res = client.post("/api/citations/format", json={
        "bibtex": SAMPLE_BIBTEX,
        "style": "apa"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert data["style"] == "apa"
    c0 = data["citations"][0]
    assert c0["key"] == "smith2024neonatal"
    assert "Smith, Jane and Doe, John" in c0["full_citation"]
    assert "2024" in c0["full_citation"]
    assert c0["in_text"] == "(Smith, 2024)"


def test_citation_formatting_vancouver():
    res = client.post("/api/citations/format", json={
        "bibtex": SAMPLE_BIBTEX,
        "style": "vancouver"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert data["citations"][0]["in_text"] == "[1]"
    assert data["citations"][1]["in_text"] == "[2]"


def test_citation_formatting_nature():
    res = client.post("/api/citations/format", json={
        "bibtex": SAMPLE_BIBTEX,
        "style": "nature"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert "^{1}" in data["citations"][0]["in_text"]


def test_static_authoring_elements():
    res = client.get("/studio.html")
    assert res.status_code == 200
    html = res.text
    # Check toolbar authoring tools
    assert 'id="toolbarTableBtn"' in html
    assert 'id="toolbarEqBtn"' in html
    assert 'id="toolbarCiteBtn"' in html
    assert 'id="toolbarCalloutBtn"' in html
    assert 'id="toolbarFigBtn"' in html
    assert 'id="findToggleBtn"' in html
    # Check panels
    assert 'id="panel-outline"' in html
    assert 'id="panel-citations"' in html
    assert 'id="panel-analytics"' in html
    assert 'id="panel-history"' in html
    # Check modals
    assert 'id="modalTable"' in html
    assert 'id="modalEquation"' in html
    assert 'id="modalCitation"' in html
    assert 'id="modalCallout"' in html
    assert 'id="modalFigure"' in html
    assert 'id="findReplaceBar"' in html
