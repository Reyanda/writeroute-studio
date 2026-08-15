from __future__ import annotations

import io
import json
import zipfile
import pytest
from fastapi.testclient import TestClient

from app import app
from writeroute.citation_verifier import (
    CitationVerifier,
    classify_citation,
    looks_like_doi,
    norm_doi,
)

client = TestClient(app)


def test_classify_and_norm_doi():
    # Scientific reference with DOI
    cit1 = {"title": "Clinical Trial", "doi": "https://doi.org/10.1016/j.cell.2023.01.001", "type": "scientific"}
    assert classify_citation(cit1) == "scientific"
    assert norm_doi(cit1["doi"]) == "10.1016/j.cell.2023.01.001"
    assert looks_like_doi(norm_doi(cit1["doi"])) is True

    # Ambiguous defaults to scientific
    cit2 = {"title": "Study of Malaria", "journal": "The Lancet"}
    assert classify_citation(cit2) == "scientific"

    # Non-scientific webpage
    cit3 = {"title": "WHO News Release", "type": "webpage", "url": "https://www.who.int/news/item/2024-01-01"}
    assert classify_citation(cit3) == "non_scientific"


def test_citation_hard_gate_offline():
    citations = [
        {"key": "valid_doi", "title": "Lancet Study", "type": "scientific", "doi": "10.1016/S0140-6736(24)00123-4"},
        {"key": "missing_doi", "title": "Scientific Study With No DOI", "type": "scientific"},
        {"key": "invalid_doi", "title": "Malformed DOI", "type": "scientific", "doi": "not-a-doi"},
        {"key": "valid_url", "title": "News Resource", "type": "webpage", "url": "https://example.com/news"},
        {"key": "missing_url", "title": "Web Resource With No URL", "type": "webpage"},
    ]

    report = CitationVerifier.audit_citations(citations, live_network=False)
    assert report.all_passed is False
    assert report.total_count == 5
    assert report.passed_count == 2
    assert report.failed_count == 3

    # Check that missing DOI failed hard gate
    res_missing_doi = next(r for r in report.results if r.key == "missing_doi")
    assert res_missing_doi.ok is False
    assert "MUST have a DOI" in res_missing_doi.reason

    # Check that missing URL failed hard gate
    res_missing_url = next(r for r in report.results if r.key == "missing_url")
    assert res_missing_url.ok is False
    assert "MUST have a URL" in res_missing_url.reason


def test_api_citation_verify_endpoint():
    payload = {
        "citations": [
            {"key": "ref1", "title": "Valid Study", "type": "scientific", "doi": "10.1056/NEJMoa2300000"},
            {"key": "ref2", "title": "Unkeyed", "type": "scientific"},
        ],
        "live_network": False,
    }
    resp = client.post("/api/citations/verify", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["all_passed"] is False
    assert data["failed_count"] == 1
    assert len(data["verified"]) == 1


def test_author_matching_and_discrimination():
    from writeroute.citation_verifier import _authors_match

    # Exact surname matches
    ok, _ = _authors_match("Smith, John and Jones, Alice", ["Smith", "Jones"])
    assert ok is True

    # "Smith et al." matching single author
    ok, _ = _authors_match("Smith et al.", ["Smith", "Williams"])
    assert ok is True

    # Dict format from reference items
    ok, _ = _authors_match([{"family": "Richard", "given": "S"}], ["Richard", "Black"])
    assert ok is True

    # Disagreeing first author (wrong paper with valid DOI)
    ok, reason = _authors_match("Richard, S", ["Geldsetzer", "Vaikath"])
    assert ok is False
    assert "author mismatch" in reason
    assert "expected 'richard'" in reason

    # Permissive only when citation names no author
    ok, reason = _authors_match("", ["Geldsetzer"])
    assert ok is True
    assert "permissive" in reason


def test_ooxml_citation_insertion():
    # Build a minimal docx package in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>Manuscript body text.</w:t></w:r></w:p>
            <w:sectPr/>
          </w:body>
        </w:document>"""
        zout.writestr("word/document.xml", doc_xml)
        zout.writestr("[Content_Types].xml", "<Types/>")

    initial_bytes = buf.getvalue()
    verified = [
        {"key": "smith2024", "doi": "10.1016/S0140-6736(24)00123-4", "title": "Lancet Study"},
    ]

    out_bytes = CitationVerifier.insert_verified_citations_ooxml(initial_bytes, verified)
    assert len(out_bytes) > len(initial_bytes)

    # Verify that the new package contains CITATION instruction
    with zipfile.ZipFile(io.BytesIO(out_bytes)) as zin:
        new_doc_xml = zin.read("word/document.xml").decode("utf-8")
        assert "CITATION smith2024" in new_doc_xml
        assert "10.1016/S0140-6736(24)00123-4" in new_doc_xml

