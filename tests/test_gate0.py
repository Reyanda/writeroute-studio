"""Regressions for the four defects the PhD-corpus benchmark found in WriteRoute 2.0.0.

Each test pins the observed failure, not a paraphrase of it. The numbers in the
docstrings are the measured before-values from that benchmark run.
"""
from __future__ import annotations

import time
import unittest

from writeroute import audit_text, repair_text
from writeroute.audit import UNREADABLE_COVERAGE, _find_bold_labels
from writeroute.model import MASK_CHAR, build_document


def fenced(lines: int) -> str:
    return ("# Plan\n\nThe method is described below.\n\n```bash\n"
            + "echo step\n" * lines + "```\n\nThat is all.\n")


class FencedCodeBacktracking(unittest.TestCase):
    """Before: 8 KB took 21.4 s through POST /api/audit; 16 KB did not finish."""

    def test_large_fenced_block_audits_in_reasonable_time(self):
        text = fenced(4000)
        self.assertGreater(len(text), 40_000)
        start = time.monotonic()
        audit_text(text, genre="technical")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 5.0, f"audit took {elapsed:.1f}s on a 40 KB fenced block")

    def test_cost_stays_close_to_linear(self):
        def timed(n: int) -> float:
            start = time.monotonic()
            audit_text(fenced(n), genre="technical")
            return time.monotonic() - start

        small = max(timed(500), 0.001)
        large = timed(4000)
        # Eight times the input must not cost more than forty times the work. The old
        # form went from 0.44 s to no completion over the same range.
        self.assertLess(large / small, 40.0, f"scaling ratio {large / small:.1f}")

    def test_bold_label_scan_is_line_wise(self):
        text = ("**Alpha**: one\n"
                "not a label\n"
                "  - **Beta**. two\n"
                "**Gamma:** three\n"
                "trailing **Delta** mid-line\n")
        hits = _find_bold_labels(text)
        # Alpha, the bulleted Beta and Gamma are label lines. The bare line and the
        # mid-line bold are not: the pattern is anchored to the start of a line.
        self.assertEqual(len(hits), 3)
        for hit in hits:
            self.assertLessEqual(hit.end(), len(text))
            self.assertTrue(text[hit.start():hit.end()].lstrip().startswith(("*", "-")))

    def test_bold_label_still_detected_in_prose(self):
        text = "\n".join(f"**Point {i}**: something short here." for i in range(6))
        report = audit_text(text, genre="professional-report")
        self.assertIn("bold_label_listicle", {f.pattern_id for f in report.findings})


class MaskCharacter(unittest.TestCase):
    """Before: protected spans were blanked to spaces, creating whitespace runs that
    every whitespace-quantifying pattern had to divide."""

    def test_mask_preserves_offsets_and_newlines(self):
        text = "Intro line.\n\n```\nsecret = 1\nsecret = 2\n```\n\nOutro line.\n"
        document = build_document(text)
        masked = document.masked_text
        self.assertEqual(len(masked), len(text))
        self.assertEqual(masked.count("\n"), text.count("\n"))
        self.assertNotIn("secret", masked)

    def test_mask_is_not_whitespace(self):
        document = build_document("a\n\n```\nxxxxxxxx\n```\n\nb\n")
        masked = document.masked_text
        self.assertIn(MASK_CHAR, masked)
        self.assertFalse(MASK_CHAR.isspace())


class TabularGuard(unittest.TestCase):
    """Before: 20 of 84 sampled hard findings were table cells read as sentences —
    "Improved WASH | 0.39 | 0.15" flagged as a causal claim."""

    TABLE = (
        "Household exposures\n\n"
        "Improved WASH | 0.39 | 0.15 | 0.38\n"
        "Improved | 67 (10) | 0.84 (0.65-1.09) | 0.21\n"
        "Type of toilet (not improved) | 125 (19) | 0.85 | 0.23\n"
    )

    def test_table_rows_are_not_audited_as_prose(self):
        report = audit_text(self.TABLE, genre="scientific")
        self.assertEqual(report.findings, [], [f.pattern_id for f in report.findings])

    def test_tab_delimited_rows_are_also_protected(self):
        text = "Results\n\nImproved WASH\t0.39\t0.15\nReduced air-entry\t1 (0.7)\t2 (1.9)\n"
        report = audit_text(text, genre="scientific")
        self.assertEqual(report.findings, [])

    def test_prose_causal_claim_is_still_flagged(self):
        report = audit_text("Improved water sources improved survival in this cohort.",
                            genre="scientific")
        self.assertTrue(report.findings, "the guard must not silence real prose")

    def test_two_column_rows_need_a_run(self):
        """Five of the surviving control-class false positives were two-column rows.
        A run of them is a table; one on its own is prose with a pipe in it."""
        run = ("Household-level exposures | Type of toilet (improved, not improved)\n"
               "SIRS | Change in nutritional status at discharge was improved\n")
        self.assertEqual(audit_text(run, genre="scientific").findings, [])

        lone = "The A | B ratio improved survival in the cohort.\n"
        self.assertTrue(audit_text(lone, genre="scientific").findings)

    def test_single_pipe_or_dash_is_not_a_table(self):
        text = ("The trial — which enrolled 240 children — reported no difference. "
                "The estimate improved survival in every subgroup we examined.")
        document = build_document(text)
        self.assertEqual([s for s in document.protected if s.kind == "table_row"], [])


class ProtectedCoverageGuard(unittest.TestCase):
    """Before: span merging covered 87,973 of 88,283 characters of one corpus document
    and the audit still returned a gradeable verdict."""

    def test_coverage_is_reported(self):
        report = audit_text("Some prose here about the method used.\n", genre="technical")
        self.assertIn("protectedCoverage", report.metrics)
        self.assertLess(report.metrics["protectedCoverage"], UNREADABLE_COVERAGE)

    def test_mostly_protected_document_is_not_assessable(self):
        text = "Intro.\n\n```\n" + "opaque payload line\n" * 200 + "```\n"
        report = audit_text(text, genre="technical")
        self.assertGreaterEqual(report.metrics["protectedCoverage"], UNREADABLE_COVERAGE)
        self.assertEqual(report.status, "not_assessable")
        self.assertFalse(report.clean, "an unreadable document is not a clean one")

    def test_repair_refuses_to_mutate_an_unassessable_document(self):
        text = "Intro.\n\n```\n" + "opaque payload line\n" * 200 + "```\n"
        result = repair_text(text, genre="technical")
        self.assertFalse(result["changed"])
        self.assertEqual(result["finalText"], text)
        self.assertIn("too little prose", result["reason"].lower())
        self.assertEqual(result["auditBefore"]["status"], "not_assessable")


class GenreIsNotGuessedSilently(unittest.TestCase):
    """Before: genre defaulted to "auto" and inference agreed with the correct profile
    on none of the author's own submission documents."""

    SAMPLE = "The cohort was followed for twelve months and the estimate was reported.\n"

    def test_explicit_genre_is_not_marked_assumed(self):
        report = audit_text(self.SAMPLE, genre="scientific")
        self.assertFalse(report.metrics["genreAssumed"])
        self.assertEqual(report.genre, "scientific")

    def test_auto_is_marked_assumed(self):
        report = audit_text(self.SAMPLE, genre="auto")
        self.assertTrue(report.metrics["genreAssumed"])

    def test_omitted_genre_is_marked_assumed(self):
        report = audit_text(self.SAMPLE)
        self.assertTrue(report.metrics["genreAssumed"])


if __name__ == "__main__":
    unittest.main()
