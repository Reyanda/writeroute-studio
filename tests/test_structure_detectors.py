"""Regressions for the structural detectors added after the project audited its own site.

The gap they close: the shipped catalogue named these patterns but implemented them as
lists of literal clichés, so it could only catch a phrase someone had already written
down. The project's own landing page exhibited all three and came back near-clean.

Every detector here is rate-based. One negative definition or one short closing line is
ordinary writing; only a habit is worth reporting.
"""
from __future__ import annotations

import unittest

from writeroute import audit_text

OBSERVATIONAL = "This was a cross-sectional survey. "


def patterns(text: str, genre: str = "general") -> set[str]:
    return {f.pattern_id for f in audit_text(text, genre=genre).findings}


class NegativeDefinition(unittest.TestCase):
    """The shipped binary_contrast pattern needed a verb ("this is not X, it's Y"), so the
    bare noun-phrase form on the project's own landing page escaped it."""

    HABIT = (
        "Named editorial findings, not an AI score.\n\n"
        "The control row is a false-positive count, not a score. It measures the tool.\n\n"
        "Passive voice in a methods section is the register working, not a defect.\n\n"
        "What follows is a description of the corpus and how it was assembled."
    )

    def test_repeated_negative_definitions_are_reported(self):
        self.assertIn("negative_definition_habit", patterns(self.HABIT))

    def test_a_single_use_is_not_reported(self):
        once = ("The estimate is an association, not a causal effect. "
                "The design cannot support anything stronger, and the discussion says so "
                "at some length before turning to the limitations of the cohort.")
        self.assertNotIn("negative_definition_habit", patterns(once))

    def test_rather_than_is_ordinary_comparison(self):
        """26 hits across the benchmark corpus were sentences like "suggestive rather than
        conclusive", which is how careful writers hedge. Counting them was the error."""
        text = ("The evidence is suggestive rather than conclusive. "
                "The design is observational rather than randomised. "
                "The claim is descriptive rather than causal.")
        self.assertNotIn("negative_definition_habit", patterns(text, "scientific"))


class HollowCloser(unittest.TestCase):
    """A literal kicker list cannot catch an aphorism the writer invents fresh each time,
    so this measures the structure instead: a short closing line assembled from words the
    paragraph has already used."""

    APHORISTIC = (
        "The engine reads a document and lists the problems it finds in the text. "
        "Each finding quotes the sentence it came from so the reader can judge it. "
        "A finding is only a finding.\n\n"
        "Preservation compares every candidate edit against the original document text. "
        "Any edit that alters a number or a modal is rejected before it is applied. "
        "An edit that alters meaning is not an edit.\n\n"
        "The corpus was assembled from a research archive of published and unpublished "
        "documents. Every class was measured separately so the control could be read on "
        "its own. A control is a control."
    )

    def test_repeated_restating_closers_are_reported(self):
        self.assertIn("hollow_closer", patterns(self.APHORISTIC))

    def test_plain_methods_prose_is_left_alone(self):
        methods = (
            "The cohort enrolled 240 children across four districts between March and "
            "September. Follow-up continued for twelve months after discharge. Mortality "
            "was 8.7 percent at one year.\n\n"
            "Anthropometry was measured by two trained nurses using standard equipment. "
            "Weight was recorded to the nearest 100 grams and height to the nearest "
            "millimetre. Measurements were repeated where the pair disagreed.\n\n"
            "Analysis used a Cox model with district as a random effect. The proportional "
            "hazards assumption was checked with Schoenfeld residuals. No violation was "
            "detected at the five percent level."
        )
        self.assertNotIn("hollow_closer", patterns(methods, "scientific"))

    def test_table_rows_are_not_closing_lines(self):
        """A journal supplement produced "The domain | Variables within domains" as a
        closer. A cell is not a sentence."""
        text = ("Individual variables in each domain are shown below. "
                "The table lists them in order.\n\n"
                "Domain | Variables\nAnthropometry | Weight\nAnthropometry | MUAC\n")
        self.assertNotIn("hollow_closer", patterns(text, "scientific"))


class PunctuatedTricolon(unittest.TestCase):
    """adjective_tricolon required comma separation, so "Clear. Precise. Human." — the
    project's own tagline — did not match, and staccato_run needs four sentences."""

    def test_a_three_word_tagline_is_reported(self):
        text = ("The tool does a number of things for people who write for a living.\n\n"
                "Clear. Precise. Human.\n\n"
                "It exports to several formats and leaves the source text alone.")
        self.assertIn("punctuated_tricolon", patterns(text))

    def test_table_fragments_are_not_taglines(self):
        """Three of these fired on journal supplements: "ab - abstract", "ti - title" and
        similar are search-field keys, not slogans. A tagline is punctuated."""
        text = ("Search fields used in the strategy are listed below.\n\n"
                "ab - abstract\nti - title\n* - truncation\n\n"
                "The strategy was peer reviewed against PRESS.")
        self.assertNotIn("punctuated_tricolon", patterns(text, "scientific"))

    def test_two_short_sentences_are_not_a_tricolon(self):
        text = ("The result was clear enough to act on without further analysis.\n\n"
                "It worked. Twice.\n\n"
                "The team moved on to the second district in the following quarter.")
        self.assertNotIn("punctuated_tricolon", patterns(text))


class NoRegressionOnCleanProse(unittest.TestCase):
    def test_none_of_the_new_detectors_fire_on_ordinary_writing(self):
        text = (
            "Children admitted with complicated severe acute malnutrition were followed "
            "for one year after discharge. The primary outcome was death from any cause.\n\n"
            "Of 240 children enrolled, 21 died during follow-up and 34 were readmitted at "
            "least once. Median time to readmission was 47 days.\n\n"
            "These figures are consistent with the two African cohorts published since "
            "2020, though the confidence intervals are wide and the sites differ in their "
            "referral patterns."
        )
        found = patterns(text, "scientific")
        for pid in ("negative_definition_habit", "hollow_closer", "punctuated_tricolon"):
            self.assertNotIn(pid, found)


if __name__ == "__main__":
    unittest.main()
