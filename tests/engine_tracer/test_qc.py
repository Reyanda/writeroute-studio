"""The artifact QC gate: it must fail documents that a render cannot fault."""

from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from tracer.config import OutputMode
from tracer.qc import inspect_structure, run_qc

SIZE = (60, 40)


def wrap(body: str, root_attributes: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE[0]}" height="{SIZE[1]}" '
        f'viewBox="0 0 {SIZE[0]} {SIZE[1]}" {root_attributes}>{body}</svg>'
    )


def solid(colour: str = "#3050a0") -> str:
    return wrap(f'<rect width="{SIZE[0]}" height="{SIZE[1]}" fill="{colour}"/>')


def severities(findings, check: str) -> list[str]:
    return [f.severity for f in findings if f.check == check]


class StructuralChecks(unittest.TestCase):
    def test_a_clean_document_raises_nothing(self) -> None:
        findings = inspect_structure(solid())
        self.assertEqual([f for f in findings if f.severity == "fail"], [])

    def test_script_content_fails(self) -> None:
        body = '<rect width="60" height="40" fill="#fff"/><script>alert(1)</script>'
        self.assertIn("fail", severities(inspect_structure(wrap(body)), "active_content"))

    def test_event_handlers_fail(self) -> None:
        body = '<rect width="60" height="40" fill="#fff" onload="x()"/>'
        self.assertIn("fail", severities(inspect_structure(wrap(body)), "event_handlers"))

    def test_external_references_fail(self) -> None:
        body = '<image width="60" height="40" href="https://example.com/a.png"/>'
        self.assertIn("fail", severities(inspect_structure(wrap(body)), "self_contained"))

    def test_duplicate_identifiers_fail(self) -> None:
        body = '<rect id="a" width="10" height="10"/><rect id="a" x="20" width="10" height="10"/>'
        self.assertIn("fail", severities(inspect_structure(wrap(body)), "unique_ids"))

    def test_unresolved_reference_fails(self) -> None:
        body = '<rect width="60" height="40" fill="url(#missing)"/>'
        self.assertIn("fail", severities(inspect_structure(wrap(body)), "references"))

    def test_non_finite_numbers_fail(self) -> None:
        body = '<rect width="60" height="NaN" fill="#fff"/>'
        self.assertIn("fail", severities(inspect_structure(wrap(body)), "finite_numbers"))

    def test_non_finite_path_data_fails(self) -> None:
        body = '<path d="M0 0L NaN 10Z" fill="#fff"/>'
        self.assertIn("fail", severities(inspect_structure(wrap(body)), "finite_numbers"))

    def test_base64_payloads_do_not_trigger_false_positives(self) -> None:
        # Base64 contains the letters N, a, N by chance; a raw text scan for
        # "NaN" flags healthy documents. Only numeric fields may be inspected.
        body = '<image width="60" height="40" href="data:image/png;base64,QQNaNqZg=="/>'
        findings = inspect_structure(wrap(body))
        self.assertNotIn("fail", severities(findings, "finite_numbers"))

    def test_doctype_declaration_fails(self) -> None:
        document = '<!DOCTYPE svg [<!ENTITY x "y">]>' + solid()
        self.assertIn("fail", severities(inspect_structure(document), "doctype"))

    def test_missing_viewbox_warns(self) -> None:
        document = '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"><rect width="60" height="40"/></svg>'
        self.assertIn("warn", severities(inspect_structure(document), "viewbox"))

    def test_malformed_xml_fails(self) -> None:
        self.assertIn("fail", severities(inspect_structure("<svg><rect></svg>"), "parse"))

    def test_representation_expectations_are_enforced(self) -> None:
        raster = '<image width="60" height="40" href="data:image/png;base64,QQ=="/>'
        self.assertIn("fail", severities(inspect_structure(wrap(raster), expect_images=False), "representation"))
        self.assertIn("fail", severities(inspect_structure(solid(), expect_images=True), "representation"))


class VerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        pixels = np.zeros((SIZE[1], SIZE[0], 4), dtype=np.uint8)
        pixels[..., :3] = (0x30, 0x50, 0xA0)
        pixels[..., 3] = 255
        self.original = Image.fromarray(pixels, mode="RGBA")

    def test_matching_document_passes(self) -> None:
        report = run_qc(solid(), self.original, mode=OutputMode.ABSOLUTE_PARITY)
        self.assertEqual(report.verdict, "pass")
        self.assertTrue(report.passed)
        self.assertTrue(report.measurements["bit_exact"])
        self.assertTrue(report.measurements["parity_digest"])

    def test_exact_mode_rejects_an_inexact_render(self) -> None:
        report = run_qc(solid("#ff0000"), self.original, mode=OutputMode.ABSOLUTE_PARITY)
        self.assertEqual(report.verdict, "revise")
        self.assertFalse(report.passed)
        self.assertTrue(any(f.check == "bit_parity" for f in report.failures))

    def test_approximate_mode_reports_difference_without_failing(self) -> None:
        report = run_qc(solid("#3050a1"), self.original, mode=OutputMode.PURE_VECTOR)
        self.assertNotEqual(report.verdict, "revise")

    def test_absolute_parity_rejects_an_embedded_raster(self) -> None:
        report = run_qc(
            wrap('<image width="60" height="40" href="data:image/png;base64,QQ=="/>'),
            self.original,
            mode=OutputMode.ABSOLUTE_PARITY,
        )
        self.assertEqual(report.verdict, "revise")

    def test_report_serialises_for_a_ledger(self) -> None:
        payload = run_qc(solid(), self.original, mode=OutputMode.ABSOLUTE_PARITY).to_dict()
        self.assertIn("verdict", payload)
        self.assertIn("findings", payload)
        self.assertIn("measurements", payload)


class UnusedDefinitionScoping(unittest.TestCase):
    """Naming ids are a feature; only unreferenced definitions are waste."""

    def test_semantic_ids_on_painted_elements_are_not_flagged(self) -> None:
        body = '<g id="logo-artwork"><rect id="logo-shape-0001" width="60" height="40" fill="#123"/></g>'
        self.assertEqual(severities(inspect_structure(wrap(body)), "unused_definitions"), [])

    def test_an_unreferenced_gradient_is_flagged(self) -> None:
        body = (
            '<defs><linearGradient id="dead"><stop offset="0" stop-color="#fff"/>'
            '</linearGradient></defs><rect width="60" height="40" fill="#123"/>'
        )
        self.assertIn("warn", severities(inspect_structure(wrap(body)), "unused_definitions"))

    def test_a_referenced_gradient_is_not_flagged(self) -> None:
        body = (
            '<defs><linearGradient id="live"><stop offset="0" stop-color="#fff"/>'
            '</linearGradient></defs><rect width="60" height="40" fill="url(#live)"/>'
        )
        self.assertEqual(severities(inspect_structure(wrap(body)), "unused_definitions"), [])


if __name__ == "__main__":
    unittest.main()
