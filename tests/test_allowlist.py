"""Regressions for the domain allow-list.

The false-positive cases are the sentences the 74-document benchmark actually produced,
not invented examples. The true-positive cases exist because an allow-list is only useful
if it is narrow: every one of them uses a word the allow-list watches, in a sentence where
it really is a claim.
"""
from __future__ import annotations

import unittest

from writeroute import audit_text
from writeroute.allowlist import find_exemption, load_entries

# Every document below is declared observational, which is what puts the strictest branch
# of the causal check in play.
OBSERVATIONAL = "This was a cross-sectional survey. "

FALSE_POSITIVES = {
    "jmp_source_list": "Improved water sources included piped water, boreholes and protected springs.",
    "jmp_both_categories": "WASH facilities in the household were categorised into improved and unimproved.",
    "jmp_parenthetical": "Household-level exposures: type of toilet (improved, not improved).",
    "jmp_vip_latrine": "Unimproved sanitation defined as no access to a ventilated improved pit latrine.",
    "jmp_safe_water": "Unsafe drinking water was defined as no access to piped water or protected wells.",
    "prisma_boilerplate": "Describe methods used to assess risk of bias due to missing results in a synthesis.",
    "protocol_condition": "Repeat bolus in second hour if improved.",
    "descriptive_measure": "In severely malnourished children VH is consistently reduced: mean VH was 218 um (SD 43).",
    "reported_estimate": "Mortality was reduced in the exposed group (RR 0.84, 95% CI 0.65-1.09).",
    "epi_risk_phrasing": "Children with disabilities are at increased risk of SAM.",
}

TRUE_POSITIVES = {
    "label_then_claim": "Improved water sources improved survival in this cohort.",
    "caused": "The programme caused a fall in mortality.",
    "prevented": "Zinc supplementation prevented stunting in the intervention arm.",
    "harmless": "Safe drinking water is harmless for all infants.",
    "reduced_no_estimate": "The intervention reduced mortality across every site we examined.",
    "eliminated": "The reform eliminated stock-outs in every district.",
}


def claim_findings(sentence: str):
    report = audit_text(OBSERVATIONAL + sentence, genre="scientific")
    return [f for f in report.findings if f.category == "claim_support"]


class FieldStandardTermsAreExcused(unittest.TestCase):
    def test_every_benchmark_false_positive_is_suppressed(self):
        for name, sentence in FALSE_POSITIVES.items():
            with self.subTest(name):
                found = claim_findings(sentence)
                self.assertEqual(found, [], [f.pattern_id + ":" + f.original for f in found])

    def test_suppression_is_reported_not_silent(self):
        report = audit_text(OBSERVATIONAL + FALSE_POSITIVES["jmp_source_list"],
                            genre="scientific")
        rows = report.metrics["allowListExemptions"]
        self.assertTrue(rows, "an excused finding must appear in the report")
        self.assertEqual(rows[0]["allowListEntry"], "jmp-service-level-label")
        self.assertTrue(rows[0]["reason"], "an exemption without a reason is not auditable")
        self.assertEqual(report.metrics["allowListExemptionCount"], sum(r["count"] for r in rows))


class RealClaimsStillFire(unittest.TestCase):
    def test_every_true_positive_survives(self):
        for name, sentence in TRUE_POSITIVES.items():
            with self.subTest(name):
                self.assertTrue(claim_findings(sentence),
                                f"{name!r} is a real claim and must not be excused")

    def test_label_and_claim_in_one_sentence(self):
        """The case that caught sentence-wide matching.

        "Improved water sources improved survival" holds a JMP label and a causal claim.
        The label is excused, the claim is not, and the scan must continue past the
        excused occurrence to reach it.
        """
        found = claim_findings(TRUE_POSITIVES["label_then_claim"])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].original, "improved")
        report = audit_text(OBSERVATIONAL + TRUE_POSITIVES["label_then_claim"],
                            genre="scientific")
        self.assertTrue(report.metrics["allowListExemptions"],
                        "the label occurrence should still be recorded as excused")


class EntryDiscipline(unittest.TestCase):
    def test_every_entry_carries_a_reason_and_targets_named_patterns(self):
        entries = load_entries()
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(entry.id):
                self.assertTrue(entry.reason.strip())
                self.assertTrue(entry.applies_to)
                self.assertTrue(entry.context)
                self.assertIn(entry.scope, {"span", "sentence"})

    def test_span_scoped_entries_require_overlap(self):
        entry = next(e for e in load_entries() if e.id == "jmp-service-level-label")
        sentence = "Improved water sources improved survival in this cohort."
        self.assertTrue(entry.matches("Improved", sentence, 0))
        self.assertFalse(entry.matches("improved", sentence, sentence.index("improved survival")))

    def test_unknown_pattern_is_never_excused(self):
        self.assertIsNone(
            find_exemption("some_unrelated_pattern", "scientific", "improved",
                           "Improved water sources included piped water.", 0))

    def test_measurement_pattern_matches_a_percentage(self):
        """A trailing \\b after the unit alternation silently broke every non-word unit:
        '%' followed by a space is not a word boundary, so '95% CI' never matched."""
        entry = next(e for e in load_entries() if e.id == "descriptive-measurement")
        sentence = "Mortality was reduced in the exposed group (RR 0.84, 95% CI 0.65-1.09)."
        self.assertTrue(entry.matches("reduced", sentence, sentence.index("reduced")))


if __name__ == "__main__":
    unittest.main()
