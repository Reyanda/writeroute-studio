from __future__ import annotations
import io
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from app import app
from writeroute.pdf_tools import (
    merge_pdfs,
    split_pdf,
    rotate_pdf,
    watermark_pdf,
    redact_pdf,
    extract_pdf_semantic,
)
from writeroute.latex_export import markdown_to_latex

client = TestClient(app)


def make_sample_pdf(text: str = "Test PDF Document Page", num_pages: int = 2) -> bytes:
    """Helper to generate a multi-page PDF in memory."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for p in range(1, num_pages + 1):
        c.drawString(100, 700, f"{text} - Page {p}")
        c.drawString(100, 650, "Patient Name: Jane Doe PHI-12345")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_pdf_tools_direct():
    pdf1 = make_sample_pdf("Doc A", 2)
    pdf2 = make_sample_pdf("Doc B", 1)

    # Merge
    merged = merge_pdfs([pdf1, pdf2])
    assert len(merged) > len(pdf1)
    extracted_meta = extract_pdf_semantic(merged)
    assert extracted_meta["page_count"] == 3

    # Split
    split_data = split_pdf(merged, [0, 2])
    split_meta = extract_pdf_semantic(split_data)
    assert split_meta["page_count"] == 2

    # Rotate
    rotated = rotate_pdf(pdf1, 90)
    assert len(rotated) > 0

    # Watermark
    wm = watermark_pdf(pdf1, text="DRAFT PREPRINT")
    assert len(wm) > len(pdf1)

    # Redact
    redacted = redact_pdf(pdf1, ["Jane Doe", "PHI-12345"])
    assert len(redacted) > 0
    redacted_meta = extract_pdf_semantic(redacted)
    assert "Jane Doe" not in redacted_meta["full_text"]


def test_api_pdf_endpoints():
    pdf1 = make_sample_pdf("Section 1", 2)
    pdf2 = make_sample_pdf("Section 2", 1)

    # Test /api/pdf/merge
    res_merge = client.post(
        "/api/pdf/merge",
        files=[
            ("files", ("doc1.pdf", pdf1, "application/pdf")),
            ("files", ("doc2.pdf", pdf2, "application/pdf")),
        ],
    )
    assert res_merge.status_code == 200
    assert res_merge.headers["content-type"] == "application/pdf"
    assert res_merge.content.startswith(b"%PDF")

    # Test /api/pdf/split
    res_split = client.post(
        "/api/pdf/split",
        files={"file": ("doc1.pdf", pdf1, "application/pdf")},
        data={"pages": "1"},
    )
    assert res_split.status_code == 200
    assert res_split.content.startswith(b"%PDF")

    # Test /api/pdf/rotate
    res_rot = client.post(
        "/api/pdf/rotate",
        files={"file": ("doc1.pdf", pdf1, "application/pdf")},
        data={"angle": 180, "pages": "1-2"},
    )
    assert res_rot.status_code == 200
    assert res_rot.content.startswith(b"%PDF")

    # Test /api/pdf/watermark
    res_wm = client.post(
        "/api/pdf/watermark",
        files={"file": ("doc1.pdf", pdf1, "application/pdf")},
        data={"text": "CONFIDENTIAL", "opacity": 0.3, "angle": 45},
    )
    assert res_wm.status_code == 200
    assert res_wm.content.startswith(b"%PDF")

    # Test /api/pdf/redact
    res_red = client.post(
        "/api/pdf/redact",
        files={"file": ("doc1.pdf", pdf1, "application/pdf")},
        data={"terms": "Jane Doe, PHI-12345"},
    )
    assert res_red.status_code == 200
    assert res_red.content.startswith(b"%PDF")

    # Test /api/pdf/extract
    res_ext = client.post(
        "/api/pdf/extract",
        files={"file": ("doc1.pdf", pdf1, "application/pdf")},
    )
    assert res_ext.status_code == 200
    data = res_ext.json()
    assert data["page_count"] == 2
    assert "Section 1" in data["full_text"]


def test_api_latex_preview_and_classes():
    sample_text = """# Neonatal Survival Study
## Methods
$$ OR = \\frac{a \\cdot d}{b \\cdot c} $$
| Parameter | Estimate |
| --- | --- |
| Beta | 0.84 |

Prior work (Smith, 2024) indicates positive trends.
"""
    # Standard Article
    res = client.post(
        "/api/latex/preview",
        json={"text": sample_text, "title": "Lancet Study", "doc_class": "article", "author": "Dr. Smith"},
    )
    assert res.status_code == 200
    tex = res.json()["latex"]
    assert "\\documentclass[11pt,a4paper]{article}" in tex
    assert "\\section{Neonatal Survival Study}" in tex
    assert "\\begin{table}" in tex

    # IEEEtran
    res_ieee = client.post(
        "/api/latex/preview",
        json={"text": sample_text, "title": "IEEE Paper", "doc_class": "IEEEtran"},
    )
    assert res_ieee.status_code == 200
    assert "\\documentclass[journal,compsoc]{IEEEtran}" in res_ieee.json()["latex"]

    # ACM
    res_acm = client.post(
        "/api/latex/preview",
        json={"text": sample_text, "title": "ACM Paper", "doc_class": "acmart"},
    )
    assert res_acm.status_code == 200
    assert "\\documentclass[sigconf]{acmart}" in res_acm.json()["latex"]


def test_citation_styles_extended():
    bibtex = """
    @article{smith2024,
      author = {Smith, Jane and Doe, John},
      title = {Clinical Trials in Neonatology},
      journal = {The Lancet},
      year = {2024},
      volume = {403},
      pages = {100-110}
    }
    """
    res_ieee = client.post("/api/citations/format", json={"bibtex": bibtex, "style": "ieee"})
    assert res_ieee.status_code == 200
    assert "[1]" in res_ieee.json()["citations"][0]["in_text"]

    res_chicago = client.post("/api/citations/format", json={"bibtex": bibtex, "style": "chicago"})
    assert res_chicago.status_code == 200
    assert "(Smith 2024)" in res_chicago.json()["citations"][0]["in_text"]
