#!/usr/bin/env python3
"""Citation Verification & Hard Gate Engine for WriteRoute Studio.

Doctrine & Hard Rule:
  1. Scientific / structured-data references (SDT or equivalent) MUST have a resolvable DOI.
  2. Non-scientific references MUST have a live URL.
  3. Hard Gate: If any citation in scope fails, the entire pipeline STOPS.
     Never invent or fabricate identifiers.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass, field
from typing import Any

CROSSREF_API = "https://api.crossref.org/works/"
DATACITE_API = "https://api.datacite.org/dois/"
USER_AGENT = {"User-Agent": "WriteRoute-CitationVerifier/2.0 (mailto:scientific-support@writeroute.org)"}
DEFAULT_TIMEOUT = 8

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+")


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_TAG = "{%s}" % W_NS


def norm_doi(doi: str) -> str:
    """Normalizes a DOI by stripping doi.org URL prefixes."""
    doi = (doi or "").strip()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def looks_like_doi(s: str) -> bool:
    """Returns True if the string is formatted as a valid standard DOI."""
    return bool(DOI_PATTERN.match(norm_doi(s or "")))


def classify_citation(cit: dict[str, Any]) -> str:
    """Classifies citation as 'scientific' or 'non_scientific'.
    Defaults ambiguous references to 'scientific' so the stricter DOI rule applies.
    """
    t = (cit.get("type") or cit.get("item_type") or "").strip().lower()
    if t in ("scientific", "article-journal", "journal-article", "paper-conference", "proceedings", "report"):
        return "scientific"
    if t in ("non_scientific", "non-scientific", "webpage", "website", "news", "broadcast", "post"):
        return "non_scientific"
    
    if cit.get("doi") or looks_like_doi(cit.get("url", "")):
        return "scientific"
        
    venue = " ".join(
        str(cit.get(k, "")) for k in ("journal", "container_title", "venue", "publisher", "container")
    ).lower()
    
    if any(w in venue for w in ("journal", "proceedings", "ieee", "acm", "springer",
                                "elsevier", "nature", "lancet", "nejm", "plos", "wiley",
                                "biorxiv", "medrxiv", "arxiv", "dataset", "doi")):
        return "scientific"
        
    # Default ambiguous cases to scientific
    return "scientific"


def _title_token_overlap(expected: str | None, got: str | None) -> bool:
    """Checks if returned metadata title has sufficient token overlap (>= 40%)."""
    if not expected or not got:
        return True
    
    def tokenize(s: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]{3,}", s.lower()))
    
    exp_tokens = tokenize(expected)
    got_tokens = tokenize(got)
    if not exp_tokens:
        return True
    overlap = exp_tokens & got_tokens
    return (len(overlap) / len(exp_tokens)) >= 0.4


@dataclass
class VerificationResult:
    key: str
    citation_type: str
    ok: bool
    reason: str
    doi: str | None = None
    url: str | None = None
    resolved_title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CitationAuditReport:
    all_passed: bool
    total_count: int
    passed_count: int
    failed_count: int
    results: list[VerificationResult] = field(default_factory=list)
    verified: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "total_count": self.total_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "results": [r.to_dict() for r in self.results],
            "verified": self.verified,
        }


class CitationVerifier:
    """High-assurance citation verification engine with live resolution & hard-gate enforcement."""

    @classmethod
    def resolve_doi_live(cls, doi: str, expected_title: str = "", timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str, str | None]:
        doi_clean = norm_doi(doi)
        if not looks_like_doi(doi_clean):
            return False, "Missing or malformed DOI", None

        # 1. Try Crossref
        try:
            req = urllib.request.Request(
                f"{CROSSREF_API}{urllib.request.quote(doi_clean)}",
                headers=USER_AGENT,
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    meta = json.loads(resp.read().decode("utf-8", "replace"))
                    titles = meta.get("message", {}).get("title", [])
                    resolved_title = titles[0] if titles else ""
                    if _title_token_overlap(expected_title, resolved_title):
                        return True, f"Resolved via Crossref ({resolved_title[:50]}...)", resolved_title
                    return False, f"Metadata mismatch: expected '{expected_title[:40]}...', got '{resolved_title[:40]}...'", resolved_title
        except urllib.error.HTTPError as e:
            if e.code != 404:
                return False, f"Crossref error: HTTP {e.code}", None
        except Exception:
            pass

        # 2. Try DataCite
        try:
            req = urllib.request.Request(
                f"{DATACITE_API}{urllib.request.quote(doi_clean)}",
                headers=USER_AGENT,
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    meta = json.loads(resp.read().decode("utf-8", "replace"))
                    titles = meta.get("data", {}).get("attributes", {}).get("titles", [])
                    resolved_title = titles[0].get("title", "") if titles else ""
                    if _title_token_overlap(expected_title, resolved_title):
                        return True, f"Resolved via DataCite ({resolved_title[:50]}...)", resolved_title
                    return False, f"Metadata mismatch: expected '{expected_title[:40]}...', got '{resolved_title[:40]}...'", resolved_title
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False, "DOI not found in Crossref or DataCite (404)", None
            return False, f"DataCite error: HTTP {e.code}", None
        except Exception as e:
            # If network is offline but DOI is syntactically valid and well-formed
            return False, f"Network resolution unavailable ({e})", None

        return False, "DOI did not resolve in registry", None

    @classmethod
    def verify_url_live(cls, url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str]:
        url = (url or "").strip()
        if not url:
            return False, "Missing URL"
        if not URL_PATTERN.match(url):
            return False, "URL is malformed"

        try:
            req = urllib.request.Request(url, headers=USER_AGENT, method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 400:
                    return True, f"Live URL (HTTP {resp.status})"
                return False, f"URL unreachable (HTTP {resp.status})"
        except urllib.error.HTTPError as e:
            if e.code in (403, 405):  # Fallback to GET
                try:
                    req_get = urllib.request.Request(url, headers=USER_AGENT)
                    with urllib.request.urlopen(req_get, timeout=timeout) as resp2:
                        if 200 <= resp2.status < 400:
                            return True, f"Live URL (HTTP {resp2.status})"
                except Exception:
                    pass
            return False, f"URL unreachable (HTTP {e.code})"
        except Exception as e:
            return False, f"URL unreachable (network: {e})"

    @classmethod
    def verify_one(cls, cit: dict[str, Any], live_network: bool = True) -> VerificationResult:
        c_type = classify_citation(cit)
        key = cit.get("key") or cit.get("cite_key") or cit.get("title") or "<unkeyed>"
        title = cit.get("title") or ""
        doi = norm_doi(cit.get("doi") or "")
        url = cit.get("url") or ""

        if c_type == "scientific":
            if not doi:
                return VerificationResult(
                    key=key,
                    citation_type=c_type,
                    ok=False,
                    reason="Hard Gate Failed: Scientific reference MUST have a DOI.",
                    doi=None,
                    url=url or None,
                )
            if not looks_like_doi(doi):
                return VerificationResult(
                    key=key,
                    citation_type=c_type,
                    ok=False,
                    reason=f"Hard Gate Failed: '{doi}' is not a valid DOI format.",
                    doi=doi,
                    url=url or None,
                )
            
            if live_network:
                ok, reason, res_title = cls.resolve_doi_live(doi, expected_title=title)
                return VerificationResult(
                    key=key,
                    citation_type=c_type,
                    ok=ok,
                    reason=reason,
                    doi=doi,
                    url=url or None,
                    resolved_title=res_title,
                )
            else:
                return VerificationResult(
                    key=key,
                    citation_type=c_type,
                    ok=True,
                    reason="DOI format valid (offline check)",
                    doi=doi,
                    url=url or None,
                )
        else:
            if not url:
                return VerificationResult(
                    key=key,
                    citation_type=c_type,
                    ok=False,
                    reason="Hard Gate Failed: Non-scientific reference MUST have a URL.",
                    doi=doi or None,
                    url=None,
                )
            if live_network:
                ok, reason = cls.verify_url_live(url)
                return VerificationResult(
                    key=key,
                    citation_type=c_type,
                    ok=ok,
                    reason=reason,
                    doi=doi or None,
                    url=url,
                )
            else:
                return VerificationResult(
                    key=key,
                    citation_type=c_type,
                    ok=True,
                    reason="URL format valid (offline check)",
                    doi=doi or None,
                    url=url,
                )

    @classmethod
    def audit_citations(cls, citations: list[dict[str, Any]], live_network: bool = True) -> CitationAuditReport:
        results = [cls.verify_one(c, live_network=live_network) for c in citations]
        failed = [r for r in results if not r.ok]
        passed = [r for r in results if r.ok]

        verified_payloads = []
        for c, r in zip(citations, results):
            if r.ok:
                verified_c = dict(c)
                verified_c["verified_doi"] = r.doi
                verified_c["verified_url"] = r.url
                verified_c["citation_type"] = r.citation_type
                verified_payloads.append(verified_c)

        return CitationAuditReport(
            all_passed=len(failed) == 0,
            total_count=len(results),
            passed_count=len(passed),
            failed_count=len(failed),
            results=results,
            verified=verified_payloads,
        )

    # ---------------------------------------------------------------- DOCX Extraction
    @classmethod
    def extract_from_docx(cls, docx_bytes_or_path: bytes | str) -> list[dict[str, Any]]:
        """Extracts citation candidates from Word field codes and in-text DOIs/URLs."""
        if isinstance(docx_bytes_or_path, str):
            with open(docx_bytes_or_path, "rb") as f:
                raw_bytes = f.read()
        else:
            raw_bytes = docx_bytes_or_path

        candidates = []
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            if "word/document.xml" not in z.namelist():
                return []
            xml_str = z.read("word/document.xml").decode("utf-8", "replace")

        root = ET.fromstring(xml_str)
        # 1. Field citations
        for i, instr in enumerate(root.iter(f"{W_TAG}instrText"), 1):
            txt = (instr.text or "").strip()
            if "CITATION" in txt.upper() or "ADDIN" in txt.upper():
                candidates.append({
                    "key": f"field-{i}",
                    "source": "word_field",
                    "raw": txt,
                    "title": "",
                    "type": "scientific",
                    "doi": "",
                    "url": "",
                })

        # 2. Body text in-line DOIs and URLs
        body_text = " ".join(t.text or "" for t in root.iter(f"{W_TAG}t"))
        seen_doi = set()
        for m in DOI_IN_TEXT.findall(body_text):
            if m not in seen_doi:
                seen_doi.add(m)
                candidates.append({
                    "key": f"doi-{len(seen_doi)}",
                    "source": "body_text",
                    "type": "scientific",
                    "doi": m,
                    "title": f"Reference ({m})",
                    "url": "",
                })

        seen_url = set()
        for m in URL_IN_TEXT.findall(body_text):
            if "doi.org" in m.lower():
                continue
            if m not in seen_url:
                seen_url.add(m)
                candidates.append({
                    "key": f"url-{len(seen_url)}",
                    "source": "body_text",
                    "type": "non_scientific",
                    "url": m,
                    "title": f"Web Resource ({m})",
                    "doi": "",
                })

        return candidates

    # ---------------------------------------------------------------- OOXML Insertion
    @classmethod
    def insert_verified_citations_ooxml(cls, docx_bytes: bytes, verified_citations: list[dict[str, Any]]) -> bytes:
        """Inserts verified citation fields directly into word/document.xml with round-trip integrity."""
        ET.register_namespace("w", W_NS)
        in_buf = io.BytesIO(docx_bytes)
        out_buf = io.BytesIO()

        with zipfile.ZipFile(in_buf, "r") as zin:
            with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename == "word/document.xml":
                        root = ET.fromstring(data)
                        body = root.find(W_TAG + "body")
                        if body is not None:
                            sect = body.find(W_TAG + "sectPr")
                            for cit in verified_citations:
                                ident = cit.get("doi") or cit.get("url") or cit.get("key", "ref")
                                p = ET.Element(W_TAG + "p")
                                # Begin field
                                rb = ET.SubElement(p, W_TAG + "r")
                                ET.SubElement(rb, W_TAG + "fldChar", {W_TAG + "fldCharType": "begin"})
                                # Instruction
                                ri = ET.SubElement(p, W_TAG + "r")
                                instr = ET.SubElement(ri, W_TAG + "instrText", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                                instr.text = f'CITATION {cit.get("key", "ref")} \\l 1033 \\m "{ident}"'
                                # Separate
                                rs = ET.SubElement(p, W_TAG + "r")
                                ET.SubElement(rs, W_TAG + "fldChar", {W_TAG + "fldCharType": "separate"})
                                # Display text
                                rd = ET.SubElement(p, W_TAG + "r")
                                disp = ET.SubElement(rd, W_TAG + "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                                disp.text = f'({cit.get("key") or ident})'
                                # End field
                                re_elem = ET.SubElement(p, W_TAG + "r")
                                ET.SubElement(re_elem, W_TAG + "fldChar", {W_TAG + "fldCharType": "end"})

                                if sect is not None:
                                    body.insert(list(body).index(sect), p)
                                else:
                                    body.append(p)
                        data = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
                    zout.writestr(item, data)

        res_bytes = out_buf.getvalue()
        # Verify valid zip package
        with zipfile.ZipFile(io.BytesIO(res_bytes)) as z_test:
            bad = z_test.testzip()
            if bad:
                raise ValueError(f"Rewritten OOXML package failed testzip at {bad}")
            ET.fromstring(z_test.read("word/document.xml"))
            
        return res_bytes
