"""Quality control for delivered SVG artifacts.

`validate_output` answers "does this render correctly?". It says nothing about
the document itself: whether identifiers collide, whether a reference resolves,
whether the file reaches outside itself, whether it carries active content, or
whether its declared representation matches its contents. Those defects survive
a perfect render and surface later, in another renderer or another editor.

This module is the single gate an artifact passes before it is called
deliverable. It parses the document, runs structural checks, folds in the
render-level result, and emits a ledger with an explicit verdict:

* ``pass`` — no failures, no warnings.
* ``pass_with_warnings`` — nothing incorrect, but something worth knowing.
* ``revise`` — at least one failure. The artifact is not deliverable.

Checks are severity-tagged rather than fatal-on-sight, because "this document
contains an embedded raster" is a failure for Absolute Parity and expected for
Exact Wrapper. The representation decides.

Inspection is structural. Scanning raw markup for tokens like ``NaN`` produces
false positives, because base64 payloads contain those letters by chance; only
values in numeric attributes and path data are examined.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image

from .config import OutputMode, ValidityResult
from .verifier import measure_bit_parity, render_svg_to_png, validate_output

SVG_NS = "http://www.w3.org/2000/svg"

#: Attributes whose values must be finite numbers when present.
NUMERIC_ATTRIBUTES = {
    "x", "y", "width", "height", "cx", "cy", "r", "rx", "ry",
    "x1", "y1", "x2", "y2", "offset", "stroke-width",
    "fill-opacity", "stroke-opacity", "opacity",
}

#: Elements that exist to be referenced rather than painted directly.
DEFINITION_ELEMENTS = {
    "linearGradient", "radialGradient", "pattern", "mask", "clipPath",
    "symbol", "filter", "marker",
}

ACTIVE_CONTENT = re.compile(r"<\s*script\b|<\s*foreignObject\b|javascript:", re.I)
EVENT_HANDLER = re.compile(r"^on[a-z]+$", re.I)
EXTERNAL_REFERENCE = re.compile(r"^\s*(?:https?:|ftp:|file:|//)", re.I)
NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?|NaN|[-+]?Infinity")
NON_FINITE = re.compile(r"\bNaN\b|\bInfinity\b", re.I)


@dataclass
class QCFinding:
    check: str
    severity: str  # "fail" | "warn" | "info"
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "severity": self.severity, "detail": self.detail}


@dataclass
class QCReport:
    verdict: str = "pass"
    findings: list[QCFinding] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> list[QCFinding]:
        return [f for f in self.findings if f.severity == "fail"]

    @property
    def warnings(self) -> list[QCFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
            "measurements": self.measurements,
        }

    def render_table(self) -> str:
        symbol = {"fail": "FAIL", "warn": "WARN", "info": "  ok"}
        lines = [f"QC verdict: {self.verdict.replace('_', ' ').upper()}"]
        for finding in self.findings:
            lines.append(f"  [{symbol.get(finding.severity, '?')}] {finding.check}: {finding.detail}")
        return "\n".join(lines)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _numbers_in(text: str) -> list[str]:
    return NUMBER.findall(text or "")


def inspect_structure(svg: str, *, expect_images: bool | None = None) -> list[QCFinding]:
    """Structural checks that a successful render cannot detect."""
    findings: list[QCFinding] = []

    lowered = svg[:4096].lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        findings.append(
            QCFinding("doctype", "fail", "Document declares a DTD or entity.")
        )

    try:
        root = ET.fromstring(svg)
    except ET.ParseError as error:
        return findings + [QCFinding("parse", "fail", f"Document is not well-formed XML: {error}")]

    if _local(root.tag) != "svg":
        findings.append(QCFinding("root", "fail", f"Root element is <{_local(root.tag)}>, not <svg>."))
    else:
        findings.append(QCFinding("parse", "info", "Well-formed SVG document."))

    if not root.get("viewBox"):
        findings.append(QCFinding("viewbox", "warn", "No viewBox; the document will not scale predictably."))
    if not (root.get("width") and root.get("height")):
        findings.append(QCFinding("dimensions", "warn", "Root width/height are not both declared."))

    identifiers: list[str] = []
    non_finite: list[str] = []
    empty_paths = 0
    images = 0
    external: list[str] = []
    handlers: list[str] = []

    for element in root.iter():
        name = _local(element.tag)
        if name == "image":
            images += 1
        if name == "path":
            data = element.get("d", "")
            if not data.strip():
                empty_paths += 1
            elif NON_FINITE.search(data):
                non_finite.append("path data")
        identifier = element.get("id")
        if identifier:
            identifiers.append(identifier)
        for key, value in element.attrib.items():
            local_key = _local(key)
            if EVENT_HANDLER.match(local_key):
                handlers.append(local_key)
            if local_key in {"href", "src"} and EXTERNAL_REFERENCE.match(value):
                external.append(value[:60])
            if local_key in NUMERIC_ATTRIBUTES:
                for token in _numbers_in(value):
                    try:
                        if not math.isfinite(float(token)):
                            non_finite.append(f"{local_key}")
                    except ValueError:
                        non_finite.append(f"{local_key}")

    if ACTIVE_CONTENT.search(svg):
        findings.append(QCFinding("active_content", "fail", "Document contains script or foreignObject content."))
    if handlers:
        findings.append(
            QCFinding("event_handlers", "fail", f"Event handler attributes present: {sorted(set(handlers))[:4]}")
        )
    if external:
        findings.append(
            QCFinding("self_contained", "fail", f"External references present: {sorted(set(external))[:3]}")
        )
    else:
        findings.append(QCFinding("self_contained", "info", "No external references; renders offline."))

    duplicates = sorted({name for name in identifiers if identifiers.count(name) > 1})
    if duplicates:
        findings.append(
            QCFinding("unique_ids", "fail", f"Duplicate id values: {duplicates[:4]}")
        )

    referenced = set(re.findall(r"url\(#([^)\"']+)\)", svg)) | set(re.findall(r'href="#([^"]+)"', svg))
    unresolved = sorted(referenced - set(identifiers))
    if unresolved:
        findings.append(
            QCFinding("references", "fail", f"Unresolved internal references: {unresolved[:4]}")
        )
    # Only identifiers on *definition* elements are dead weight when
    # unreferenced. An id on a painted element is a naming feature — the logo
    # route emits `logo-shape-0001` deliberately so the scene graph is legible —
    # and flagging those makes the check noise rather than signal.
    definition_ids = {
        element.get("id")
        for element in root.iter()
        if element.get("id") and _local(element.tag) in DEFINITION_ELEMENTS
    }
    orphaned = sorted(definition_ids - referenced)
    if orphaned:
        findings.append(
            QCFinding(
                "unused_definitions",
                "warn",
                f"{len(orphaned)} definition(s) never referenced: {orphaned[:3]}",
            )
        )

    if non_finite:
        findings.append(
            QCFinding("finite_numbers", "fail", f"Non-finite values in {sorted(set(non_finite))[:4]}")
        )
    else:
        findings.append(QCFinding("finite_numbers", "info", "All numeric attributes and path data are finite."))

    if empty_paths:
        findings.append(QCFinding("empty_paths", "warn", f"{empty_paths} path element(s) carry no geometry."))

    if expect_images is True and images == 0:
        findings.append(QCFinding("representation", "fail", "Representation requires an embedded raster but none is present."))
    elif expect_images is False and images:
        findings.append(
            QCFinding("representation", "fail", f"Representation forbids embedded raster but {images} <image> present.")
        )
    else:
        findings.append(QCFinding("representation", "info", f"Embedded raster images: {images}."))

    return findings


def run_qc(
    svg: str,
    original: Image.Image,
    *,
    mode: OutputMode | str = OutputMode.PURE_VECTOR,
    target_quality: float = 0.0,
    validity: ValidityResult | None = None,
    rendered: Image.Image | None = None,
) -> QCReport:
    """Run the full artifact gate and return a verdict with its ledger."""
    selected = mode if isinstance(mode, OutputMode) else OutputMode(mode)
    source = original.convert("RGBA")
    exact_modes = {OutputMode.ABSOLUTE_PARITY, OutputMode.EXACT_WRAPPER}
    expect_images = {
        OutputMode.ABSOLUTE_PARITY: False,
        OutputMode.EXACT_WRAPPER: True,
    }.get(selected)

    report = QCReport()
    report.findings.extend(inspect_structure(svg, expect_images=expect_images))

    try:
        proof = rendered if rendered is not None else render_svg_to_png(svg, source.size)
    except Exception as error:  # renderer failure is a QC failure, not a crash
        report.findings.append(QCFinding("render", "fail", f"Document failed to render: {error}"))
        report.verdict = "revise"
        return report
    report.findings.append(QCFinding("render", "info", "Rendered successfully at source dimensions."))

    if proof.size != source.size:
        report.findings.append(
            QCFinding(
                "dimensions",
                "fail",
                f"Rendered {proof.size[0]}×{proof.size[1]} against source {source.size[0]}×{source.size[1]}.",
            )
        )

    parity = measure_bit_parity(source, proof)
    if selected in exact_modes:
        if parity["bit_exact"]:
            report.findings.append(
                QCFinding("bit_parity", "info", f"Pixel-identical. Digest sha256:{parity['parity_digest'][:16]}.")
            )
        else:
            report.findings.append(
                QCFinding(
                    "bit_parity",
                    "fail",
                    f"{parity['mismatched_pixels']:,} pixels differ in an exact representation.",
                )
            )
    else:
        report.findings.append(
            QCFinding("bit_parity", "info", f"Approximate representation; {parity['mismatched_pixels']:,} pixels differ.")
        )

    result = validity or validate_output(
        svg,
        source,
        proof,
        target_quality=target_quality,
        require_bit_parity=selected is OutputMode.ABSOLUTE_PARITY,
    )
    for error in result.errors:
        report.findings.append(QCFinding("output_contract", "fail", error))
    for warning in result.warnings:
        report.findings.append(QCFinding("output_contract", "warn", warning))
    if result.passed:
        report.findings.append(QCFinding("output_contract", "info", "Output contract satisfied."))

    report.measurements = {
        "mode": selected.value,
        "width": proof.size[0],
        "height": proof.size[1],
        "svg_bytes": len(svg.encode("utf-8")),
        "path_count": result.path_count,
        "image_count": result.image_count,
        "bit_exact": bool(parity["bit_exact"]),
        "mismatched_pixels": int(parity["mismatched_pixels"]),
        "parity_digest": parity["parity_digest"],
    }
    if report.failures:
        report.verdict = "revise"
    elif report.warnings:
        report.verdict = "pass_with_warnings"
    else:
        report.verdict = "pass"
    return report
