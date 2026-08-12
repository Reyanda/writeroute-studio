"""PDF form detection and filling, against forms generated at test time.

Replaces coverage that the inherited suite provided by asserting against private
documents. These forms are built from scratch and contain nothing real.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pdfstudio

from .synthetic import character_cell_form, date_parts_form, ruled_line_form


def detect(path: str) -> list[dict]:
    unbundler = pdfstudio.TracerUnbundler(path)
    detector = pdfstudio.TracerSlotDetector()
    try:
        document = unbundler.unbundle_document()
        fields: list[dict] = []
        for index, page in enumerate(document.get("pages", [])):
            fields.extend(detector.detect_slots_for_page(page, unbundler.doc, index))
        return fields
    finally:
        unbundler.close()


class Detection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name: str) -> str:
        return str(Path(self.tmp.name) / name)

    def test_a_ruled_line_is_detected_as_a_writable_field(self):
        fields = detect(ruled_line_form(self.path("ruled.pdf")))
        self.assertEqual(len(fields), 2, "one field per ruled line")
        for field in fields:
            self.assertEqual(field["slot_type"], "line")
            self.assertIn("rect", field)
            self.assertGreater(field["width"], 0)

    def test_every_cell_carries_a_unique_id(self):
        fields = detect(character_cell_form(self.path("cells.pdf")))
        ids = [f["id"] for f in fields if "id" in f]
        self.assertTrue(ids, "cell fields should carry ids")
        self.assertEqual(len(ids), len(set(ids)), "duplicate field ids would collide on fill")

    def test_a_character_cell_row_yields_one_field_per_cell(self):
        fields = detect(character_cell_form(self.path("cells.pdf"), cells=6))
        cells = [f for f in fields if f.get("slot_type") == "cell"]
        self.assertGreaterEqual(len(cells), 6,
                                "each box in a character row should be its own field")

    def test_date_parts_do_not_merge_into_one_field(self):
        """The regression the source project called out: Day, Month and Year are three
        groups, and detecting them as a single eight-character run loses the structure."""
        fields = detect(date_parts_form(self.path("date.pdf")))
        groups = {f.get("group_id") for f in fields if f.get("group_id")}
        self.assertGreaterEqual(len(groups), 3,
                                f"expected at least three groups, saw {len(groups)}")

    def test_fields_sit_inside_the_page(self):
        import fitz

        path = ruled_line_form(self.path("bounds.pdf"))
        with fitz.open(path) as doc:
            rect = doc[0].rect
        for field in detect(path):
            x0, y0, x1, y1 = field["rect"]
            self.assertGreaterEqual(x0, -1)
            self.assertGreaterEqual(y0, -1)
            self.assertLessEqual(x1, rect.width + 1)
            self.assertLessEqual(y1, rect.height + 1)


class Filling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_filled_form_is_a_readable_pdf_that_keeps_the_original_text(self):
        import fitz

        source = ruled_line_form(str(Path(self.tmp.name) / "in.pdf"))
        fields = detect(source)
        # fill_and_rebundle takes slots keyed by 1-based page number, each carrying the
        # detected geometry with a `value` attached.
        filled = [dict(field, value="Sample") for field in fields[:1]]
        out = str(Path(self.tmp.name) / "out.pdf")

        pdfstudio.TracerRebundler(source).fill_and_rebundle({1: filled}, out)

        self.assertTrue(Path(out).exists())
        with fitz.open(out) as doc:
            self.assertEqual(doc.page_count, 1)
            text = doc[0].get_text()
        self.assertIn("Membership application", text,
                      "filling must not discard the form's own text")


if __name__ == "__main__":
    unittest.main()


class FillReportsWhatItDrew(unittest.TestCase):
    """The engine used to discard insert_textbox's return value.

    That return is the leftover vertical space, and PyMuPDF draws nothing at all when it
    is negative. Centring a character by cropping the top of its cell left 0.05 points too
    little on an ordinary 18-point cell, so every character silently failed to appear
    while the fill reported success.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _fill(self, cells: int = 6):
        import fitz

        source = character_cell_form(str(Path(self.tmp.name) / "in.pdf"), cells=cells)
        fields = [dict(f, value="7") for f in detect(source) if f.get("slot_type") == "cell"]
        out = str(Path(self.tmp.name) / "out.pdf")
        rebundler = pdfstudio.TracerRebundler(source)
        rebundler.fill_and_rebundle({1: fields}, out)
        with fitz.open(out) as doc:
            text = doc[0].get_text()
        return rebundler, fields, text

    def test_characters_actually_reach_the_page(self):
        _, fields, text = self._fill()
        self.assertEqual(text.count("7"), len(fields),
                         "every filled cell should contain its character")

    def test_nothing_is_reported_as_unrendered_when_it_all_fits(self):
        rebundler, _, _ = self._fill()
        self.assertEqual(rebundler.unrendered, [])

    def test_a_value_that_cannot_be_drawn_is_reported(self):
        """A cell too small for any legible size must be reported, not swallowed."""
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.draw_rect(fitz.Rect(100, 100, 103, 103))
        source = str(Path(self.tmp.name) / "tiny.pdf")
        doc.save(source)
        doc.close()

        slot = {"id": "tiny", "slot_type": "cell", "rect": [100, 100, 103, 103],
                "font_size": 40.0, "value": "7"}
        out = str(Path(self.tmp.name) / "tiny-out.pdf")
        rebundler = pdfstudio.TracerRebundler(source)
        rebundler.fill_and_rebundle({1: [slot]}, out)
        self.assertEqual(len(rebundler.unrendered), 1)
        self.assertEqual(rebundler.unrendered[0]["id"], "tiny")
