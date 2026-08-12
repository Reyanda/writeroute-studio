from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from writeroute.audit import audit_text
from writeroute.contracts import WritingBrief, compile_draft_contract, compile_revision_contract
from writeroute.genres import get_genre, infer_genre, load_genres
from writeroute.evidence import verify_draft_evidence
from writeroute.integrity import verify_integrity
from writeroute.patterns import load_patterns
from writeroute.route import draft_with_callback, repair_text, rewrite_with_callback, suggest_text, verify_text
from writeroute.voice import build_voice_profile, save_voice_profile, voice_distance


class TestCatalogue(unittest.TestCase):
    def test_patterns_unique_and_substantial(self):
        patterns = load_patterns()
        self.assertGreaterEqual(len(patterns), 35)
        self.assertEqual(len({p.id for p in patterns}), len(patterns))

    def test_genres(self):
        genres = load_genres()
        self.assertGreaterEqual(len(genres), 10)
        self.assertIn("scientific", genres)
        self.assertIn("legal", genres)

    def test_genre_inference(self):
        result = infer_genre("# Methods\nWe estimated RR = 1.4 (95% CI 1.1–1.8) in a cohort of participants.")
        self.assertEqual(result["genre"], "scientific")


class TestAudit(unittest.TestCase):
    def test_no_authorship_claim(self):
        report = audit_text("Here’s the thing: the board approved the budget.")
        payload = report.to_dict()
        self.assertIsNone(payload["authorshipClaim"])
        self.assertTrue(any(f["patternId"] == "throat_clearing" for f in payload["findings"]))

    def test_clean_professional_prose(self):
        text = (
            "The committee approved the revised budget on 12 August 2026. "
            "Finance will release EUR 2.4 million after the audit closes. "
            "The programme manager will report expenditure each month."
        )
        report = audit_text(text, "professional-report")
        self.assertTrue(report.clean, report.to_dict())
        self.assertEqual(report.editorial_burden, 0.0)

    def test_clean_scientific_prose(self):
        text = (
            "In a cross-sectional study of 500 children, wasting was associated with mortality "
            "(RR = 1.42, 95% CI 1.10–1.83). Residual confounding and reverse causation remain possible."
        )
        self.assertTrue(audit_text(text, "scientific").clean)

    def test_quotes_and_code_are_masked(self):
        text = (
            "The guide lists “it is important to note that” as an example. "
            "The test fixture contains `studies show` and must remain exact."
        )
        self.assertTrue(audit_text(text, "technical").clean)

    def test_literal_domain_guards(self):
        text = (
            "The model used robust standard errors. The bank reported a leverage ratio of 4.2. "
            "The wiring harness carried current, while the panel converted harnessed energy into heat."
        )
        report = audit_text(text, "technical")
        ids = {f.pattern_id for f in report.findings}
        self.assertNotIn("qc_assurance", ids)
        self.assertNotIn("corporate_jargon", ids)

    def test_reported_voice_downgraded(self):
        text = "The report said the launch marks a pivotal moment for the company."
        report = audit_text(text, "professional-report")
        finding = next(f for f in report.findings if f.pattern_id == "importance_puffery")
        self.assertTrue(finding.reported_voice)
        self.assertEqual(finding.severity, "review")

    def test_substantive_causal_overreach(self):
        text = (
            "This cross-sectional survey included 800 households. Higher income caused lower food insecurity."
        )
        report = audit_text(text, "scientific")
        ids = {f.pattern_id for f in report.findings}
        self.assertIn("causal_overreach_observational", ids)

    def test_significance_without_effect(self):
        report = audit_text("Mortality was significantly lower in the intervention group.", "scientific")
        self.assertIn("statistical_significance_without_estimate", {f.pattern_id for f in report.findings})

    def test_actionable_policy_recommendation_is_not_flagged(self):
        text = (
            "The Ministry of Health should publish district stock data by 30 September 2026 because "
            "facilities cannot correct shortages they cannot see. The dashboard should report stock-out "
            "days and order fulfilment each month."
        )
        self.assertTrue(audit_text(text, "policy-brief").clean)

    def test_single_logical_transition_is_not_slop(self):
        text = "In addition, the second dataset contained complete dates and site codes."
        self.assertNotIn("empty_transition", {f.pattern_id for f in audit_text(text, "scientific").findings})

    def test_repeated_connective_scaffolding_is_flagged(self):
        text = (
            "Moreover, the first dataset contained no dates and could not support a temporal analysis.\n\n"
            "Furthermore, the second dataset lacked identifiers.\n\n"
            "Additionally, the third dataset included timestamps but no site codes, so records could not be linked.\n\n"
            "In addition, the fourth dataset contained complete dates, site codes, and outcome fields."
        )
        self.assertIn("empty_transition", {f.pattern_id for f in audit_text(text, "scientific").findings})

    def test_duplicate_conclusion_tie_does_not_compare_paragraph_objects(self):
        paragraph = (
            "The protocol defines request headers, response fields, validation rules, "
            "timeout behaviour, and recovery procedures."
        )
        text = f"{paragraph}\n\n{paragraph}\n\n{paragraph}"
        report = audit_text(text, "technical")
        self.assertTrue(any(f.pattern_id == "duplicate_conclusion" for f in report.findings))


class TestIntegrity(unittest.TestCase):
    SOURCE = (
        "The Contractor must submit 17 records by 30 September 2026 under Section 12(b). "
        "Only verified records may be uploaded to `records.csv`; the analysis suggests an association "
        "with lower risk (RR = 0.82, 95% CI 0.70–0.96)."
    )

    def assertRejected(self, candidate: str, category: str):
        report = verify_integrity(self.SOURCE, candidate, "legal")
        self.assertFalse(report.passes, report.to_dict())
        self.assertIn(category, {v.category for v in report.violations})

    def test_identity(self):
        self.assertTrue(verify_integrity(self.SOURCE, self.SOURCE, "legal").passes)

    def test_number(self):
        self.assertRejected(self.SOURCE.replace("17", "7"), "number")

    def test_date(self):
        self.assertRejected(self.SOURCE.replace("30 September", "29 September"), "number")

    def test_modal(self):
        self.assertRejected(self.SOURCE.replace("must submit", "should submit"), "modal_force")

    def test_scope(self):
        self.assertRejected(self.SOURCE.replace("Only verified", "Verified"), "scope")

    def test_code(self):
        self.assertRejected(self.SOURCE.replace("records.csv", "record.csv"), "inline_code")

    def test_direction(self):
        self.assertRejected(self.SOURCE.replace("lower risk", "higher risk"), "direction")

    def test_causal_strength(self):
        candidate = self.SOURCE.replace("suggests an association with", "caused")
        self.assertRejected(candidate, "causal_strength")

    def test_safe_compression(self):
        original = "The system has the ability to export 17 records."
        candidate = "The system can export 17 records."
        self.assertTrue(verify_integrity(original, candidate, "technical").passes)


class TestSuggestionsAndRepair(unittest.TestCase):
    def test_exact_and_frame_are_separate(self):
        text = "It is important to note that studies show the framework drives meaningful impact."
        payload = suggest_text(text, "general")
        by_id = {f["patternId"]: f for f in payload["findings"]}
        self.assertTrue(any(c["safeToApply"] for c in by_id["importance_meta"]["candidates"]))
        self.assertTrue(any(c["requiresAuthorInput"] for c in by_id["vague_attribution"]["candidates"]))
        self.assertFalse(any(c["safeToApply"] for c in by_id["vague_attribution"]["candidates"]))

    def test_repair_removes_only_safe_slop(self):
        text = (
            "Certainly! Here is a polished version that ensures clarity. "
            "In today's fast-paced world, it is important to note that studies show the programme drives meaningful impact. "
            "The future isn't coming. It's already here."
        )
        result = repair_text(text, "general")
        self.assertTrue(result["changed"])
        final = result["finalText"]
        self.assertNotIn("Certainly", final)
        self.assertNotIn("fast-paced world", final)
        self.assertNotIn("important to note", final)
        self.assertNotIn("future isn't coming", final)
        self.assertIn("Studies show", final)  # requires a source, so no fabricated repair
        self.assertIn("meaningful impact", final)  # requires substance, so no invented metric

    def test_clean_is_byte_exact(self):
        text = "The board approved the budget on Tuesday. Finance will release the funds on Friday.\n"
        result = repair_text(text, "professional-report")
        self.assertFalse(result["changed"])
        self.assertEqual(result["finalText"], text)
        self.assertTrue(result["byteExactNoOp"])

    def test_source_text_mode_never_mutates(self):
        source = "In order to preserve the record, the Clerk must retain all 17 pages."
        result = repair_text(source, "legal", source_text=True)
        self.assertFalse(result["changed"])
        self.assertTrue(result["byteExactNoOp"])
        self.assertEqual(result["finalText"], source)
        self.assertTrue(result["sourceTextMode"])

    def test_source_text_suggestions_are_never_auto_applicable(self):
        source = "In order to preserve the record, the Clerk must retain all 17 pages."
        result = suggest_text(source, "legal", source_text=True)
        candidates = [candidate for finding in result["findings"] for candidate in finding["candidates"]]
        self.assertTrue(candidates)
        self.assertFalse(any(candidate["safeToApply"] for candidate in candidates))

    def test_verify_rejects_fluent_damage(self):
        source = "The clinic must report all 17 deaths."
        candidate = "The clinic should report some deaths."
        result = verify_text(source, candidate, "professional-report")
        self.assertFalse(result["passes"])

    def test_bounded_kicker_exception_is_exact_and_verifiable(self):
        source = "The board approved the system. The future isn't coming. It's already here."
        repair = repair_text(source, "general")
        self.assertTrue(repair["contextualExemption"], repair)
        self.assertEqual(repair["finalText"], "The board approved the system.")
        applied = next(item for item in repair["applied"] if item["patternId"] == "fake_profound_kicker")
        self.assertEqual(set(applied["contextualOverrideCategories"]), {"length", "negation"})
        verification = verify_text(source, repair["finalText"], "general")
        self.assertTrue(verification["passes"], verification)
        self.assertTrue(verification["contextualExemption"])

    def test_arbitrary_negation_deletion_cannot_claim_exception(self):
        source = "The system is not safe. The board delayed deployment."
        candidate = "The board delayed deployment."
        result = verify_text(source, candidate, "general")
        self.assertFalse(result["passes"], result)
        self.assertFalse(result["contextualExemption"])

    def test_rhetorical_formula_with_factual_anchor_is_not_auto_deleted(self):
        source = "The board approved the system. The future isn't coming in 2027. It's already here."
        # The formula no longer matches exactly; the number must remain protected in any case.
        repair = repair_text(source, "general")
        self.assertIn("2027", repair["finalText"])

    def test_rewrite_tournament_accepts_bounded_deterministic_baseline(self):
        source = "The board approved the system. The future isn't coming. It's already here."
        result = rewrite_with_callback(source, lambda contract, text: source, "general", candidates=1)
        self.assertTrue(result["changed"], result)
        self.assertEqual(result["finalText"], "The board approved the system.")
        self.assertTrue(any(attempt["accepted"] for attempt in result["attempts"]))

    def test_rewrite_tournament_rejects_modal_damage(self):
        source = "Here is a polished version. The clinic must report all 17 deaths."
        damaging = "The clinic should report some deaths."
        result = rewrite_with_callback(source, lambda contract, text: damaging, "professional-report", candidates=1)
        self.assertTrue(result["changed"], result)
        self.assertIn("must report all 17 deaths", result["finalText"])
        self.assertTrue(any("preservation" in attempt["reason"] for attempt in result["attempts"] if not attempt["accepted"]))


class TestEvidenceBoundDrafting(unittest.TestCase):
    def setUp(self):
        self.brief = WritingBrief.create(
            genre="policy-brief",
            audience="district health directors",
            purpose="secure approval for a monthly stock-out reporting rule",
            reader_action="approve the rule and assign district pharmacy managers",
            evidence=["27 of 35 facilities reported a stock-out"],
        )

    def test_evidence_gate_rejects_new_quantitative_anchor(self):
        report = verify_draft_evidence(
            self.brief,
            "The Ministry of Health should publish the dashboard because 42 facilities reported a stock-out.",
        )
        self.assertFalse(report.passes)
        self.assertIn("42", {item.value for item in report.violations})

    def test_evidence_gate_ignores_numbered_list_markers(self):
        report = verify_draft_evidence(
            self.brief,
            "1. Publish the monthly report.\n2. Verify reports from 27 of 35 facilities.",
        )
        self.assertTrue(report.passes, report.to_dict())

    def test_draft_tournament_rejects_invention_and_accepts_supported_prose(self):
        candidates = iter([
            "The Ministry of Health should publish the dashboard because 42 facilities reported a stock-out in 2026.",
            ("The Ministry of Health should publish monthly district stock-out data because "
             "27 of 35 facilities reported a stock-out. District pharmacy managers should "
             "verify each submission before publication."),
        ])

        def callback(contract: str, source: str) -> str:
            self.assertIn("EVIDENCE BOUNDARY", contract)
            return next(candidates)

        result = draft_with_callback(self.brief, callback, candidates=2)
        self.assertTrue(result["accepted"], result)
        self.assertIn("27 of 35", result["finalText"])
        self.assertFalse(result["attempts"][0]["accepted"])
        self.assertIn("unsupported factual anchors", result["attempts"][0]["reason"])
        self.assertTrue(result["attempts"][1]["evidenceBoundary"]["passes"])


class TestVoiceAndContracts(unittest.TestCase):
    SAMPLE_A = (
        "We estimated the effect directly. The result was uncertain, so we reported the interval and stopped there. "
        "Long explanations rarely improve a weak estimate."
    )
    SAMPLE_B = (
        "We checked the records twice. Some values remained missing, and we said so. "
        "The report gives the decision first and the method second."
    )

    def test_voice_profile_roundtrip(self):
        profile = build_voice_profile([self.SAMPLE_A, self.SAMPLE_B], name="test")
        self.assertEqual(profile.sample_count, 2)
        with tempfile.TemporaryDirectory() as td:
            path = save_voice_profile(profile, Path(td) / "voice.json")
            self.assertTrue(path.exists())
            distance = voice_distance(profile, self.SAMPLE_A)
            self.assertLess(distance.score, 50)

    def test_long_inline_voice_sample_is_not_treated_as_a_path(self):
        sample = ("We report the estimate and its interval. " * 80).strip()
        profile = build_voice_profile(sample, name="inline")
        self.assertEqual(profile.sample_count, 1)
        self.assertGreater(profile.word_count, 300)

    def test_author_mode_contract(self):
        genre = get_genre("policy-brief")
        brief = WritingBrief.create(
            genre="policy-brief", audience="district health directors",
            purpose="secure approval for a stock reporting rule",
            evidence=["27 of 35 facilities reported a stock-out"],
        )
        contract = compile_draft_contract(brief, genre)
        self.assertIn("Write as the author", contract)
        self.assertIn("do not invent", contract.lower())
        self.assertIn("Return only the final", contract)

    def test_revision_contract_contains_findings_and_invariants(self):
        text = "It is important to note that the board approved 17 grants."
        report = audit_text(text, "professional-report")
        contract = compile_revision_contract(text, report, get_genre("professional-report"))
        self.assertIn("PRESERVATION-FIRST", contract)
        self.assertIn("17", contract)
        self.assertIn("Interpretive metadiscourse", contract)


if __name__ == "__main__":
    unittest.main(verbosity=2)
