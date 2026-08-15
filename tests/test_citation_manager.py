from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from app import app
from writeroute.citation_engine import (
    ReferenceItem,
    Author,
    parse_bibtex,
    parse_ris,
    CitationFormatter,
    export_library_to_bibtex,
    export_library_to_ris,
)

client = TestClient(app)

SAMPLE_BIBTEX = """
@article{smith2024neonatal,
  title = {Neonatal Survival in Low-Resource Clinical Settings},
  author = {Smith, John and Jones, Alice},
  journal = {The Lancet},
  year = {2024},
  volume = {403},
  number = {10432},
  pages = {120-128},
  doi = {10.1016/S0140-6736(24)00123-4}
}
@article{taylor2023maternal,
  title = {Maternal Health Interventions in Rural Districts},
  author = {Taylor, Robert and Williams, David and Brown, Carol},
  journal = {BMJ Global Health},
  year = {2023},
  volume = {8},
  pages = {e012345},
  doi = {10.1136/bmjgh-2023-012345}
}
"""

SAMPLE_RIS = """
TY  - JOUR
TI  - Neonatal Survival in Low-Resource Clinical Settings
AU  - Smith, John
AU  - Jones, Alice
PY  - 2024
JO  - The Lancet
VL  - 403
IS  - 10432
SP  - 120
EP  - 128
DO  - 10.1016/S0140-6736(24)00123-4
ER  - 
"""


def test_parse_bibtex():
    items = parse_bibtex(SAMPLE_BIBTEX)
    assert len(items) == 2
    assert items[0].cite_key == "smith2024neonatal"
    assert items[0].title == "Neonatal Survival in Low-Resource Clinical Settings"
    assert len(items[0].authors) == 2
    assert items[0].authors[0].family == "Smith"
    assert items[0].year == "2024"
    assert items[0].journal == "The Lancet"


def test_parse_ris():
    items = parse_ris(SAMPLE_RIS)
    assert len(items) == 1
    assert items[0].title == "Neonatal Survival in Low-Resource Clinical Settings"
    assert len(items[0].authors) == 2
    assert items[0].authors[0].family == "Smith"
    assert items[0].year == "2024"
    assert items[0].pages == "120-128"


def test_citation_formatter():
    items = parse_bibtex(SAMPLE_BIBTEX)
    
    # In-text APA
    apa_intext = CitationFormatter.format_in_text([items[0]], style="apa")
    assert apa_intext == "(Smith & Jones, 2024)"

    # In-text 3+ authors
    apa_intext_3 = CitationFormatter.format_in_text([items[1]], style="apa")
    assert apa_intext_3 == "(Taylor et al., 2023)"

    # In-text Vancouver / IEEE
    van_intext = CitationFormatter.format_in_text(items, style="vancouver", indices=[1, 2])
    assert van_intext == "[1, 2]"

    ieee_intext = CitationFormatter.format_in_text(items, style="ieee", indices=[1, 2])
    assert ieee_intext == "[1], [2]"

    # In-text Nature
    nature_intext = CitationFormatter.format_in_text(items, style="nature", indices=[1, 2])
    assert nature_intext == "<sup>1, 2</sup>"

    # Bibliography entry APA
    apa_bib = CitationFormatter.format_bibliography_entry(items[0], style="apa")
    assert "Smith, J., & Jones, A." in apa_bib
    assert "<em>The Lancet</em>" in apa_bib
    assert "https://doi.org/10.1016/S0140-6736(24)00123-4" in apa_bib

    # Bibliography entry Vancouver
    van_bib = CitationFormatter.format_bibliography_entry(items[0], style="vancouver", index=1)
    assert "1. Smith J, Jones A." in van_bib
    assert "2024;403(10432):120-128." in van_bib


def test_export_library():
    items = parse_bibtex(SAMPLE_BIBTEX)
    bib_out = export_library_to_bibtex(items)
    assert "@article{smith2024neonatal" in bib_out
    assert "author = {Smith, John and Jones, Alice}" in bib_out

    ris_out = export_library_to_ris(items)
    assert "TY  - JOUR" in ris_out
    assert "AU  - Smith, John" in ris_out
    assert "ER  -" in ris_out


def test_api_citation_endpoints():
    # /api/citations/parse
    res_parse = client.post(
        "/api/citations/parse",
        json={"raw_text": SAMPLE_BIBTEX, "format": "bibtex"},
    )
    assert res_parse.status_code == 200
    data_parse = res_parse.json()
    assert data_parse["count"] == 2
    assert data_parse["items"][0]["cite_key"] == "smith2024neonatal"

    # /api/citations/format
    res_format = client.post(
        "/api/citations/format",
        json={"items": data_parse["items"], "style": "apa"},
    )
    assert res_format.status_code == 200
    data_format = res_format.json()
    assert len(data_format["entries"]) == 2
    assert "Smith & Jones" in data_format["entries"][0]["in_text"]

    # /api/citations/export
    res_export = client.post(
        "/api/citations/export",
        json={"items": data_parse["items"], "format": "ris"},
    )
    assert res_export.status_code == 200
    data_export = res_export.json()
    assert "TY  - JOUR" in data_export["content"]
