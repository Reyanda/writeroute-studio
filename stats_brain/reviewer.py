from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from .debates import DebateResolver
from .knowledge import registry_counts
from .models import ReviewContext, ReviewFinding, ReviewGate, ReviewReport, SEVERITY_ORDER
from .ontology import MethodRouter, ProblemReconstructor
from .rules import RuleEngine
from .semantic import SemanticReviewHarness, StatisticalReasoningProvider
from .utils import as_list, deep_sanitize


DOMAIN_DIMENSIONS: dict[str, str] = {
    "problem_definition": "problem_and_estimand",
    "estimand": "problem_and_estimand",
    "study_design": "design_and_sampling",
    "complex_survey": "design_and_sampling",
    "dhs": "design_and_sampling",
    "dhs_spatial": "design_and_sampling",
    "causal_identification": "identification",
    "target_trial_emulation": "identification",
    "survey_causal_inference": "identification",
    "continuous_causal_inference": "identification",
    "joint_causal_inference": "identification",
    "intersectional_causal_inference": "identification",
    "method_selection": "estimation",
    "regression": "estimation",
    "prediction": "estimation",
    "causal_machine_learning": "estimation",
    "bayesian_inference": "estimation",
    "time_to_event": "estimation",
    "recurrent_events": "estimation",
    "meta_analysis": "estimation",
    "spatial_statistics": "estimation",
    "network_causal_inference": "identification",
    "diagnostics": "diagnostics_and_sensitivity",
    "sensitivity_analysis": "diagnostics_and_sensitivity",
    "missing_data": "diagnostics_and_sensitivity",
    "numeric_consistency": "numeric_integrity",
    "interpretation": "interpretation_and_reporting",
    "reporting": "interpretation_and_reporting",
    "prediction_fairness": "equity_and_transportability",
    "intersectionality": "equity_and_transportability",
    "transportability": "equity_and_transportability",
    "reproducibility": "reproducibility",
    "randomized_trials": "design_and_sampling",
    "interaction": "cross_domain_coherence",
}

PENALTIES = {"fatal": 45, "critical": 25, "major": 12, "minor": 5, "query": 3, "info": 0}


class StatsBrainReviewer:
    def __init__(self, provider: StatisticalReasoningProvider | None = None) -> None:
        self.reconstructor = ProblemReconstructor()
        self.router = MethodRouter()
        self.rules = RuleEngine()
        self.debates = DebateResolver()
        self.provider = provider
        self.semantic = SemanticReviewHarness()

    def review(self, value: ReviewContext | Mapping[str, Any]) -> ReviewReport:
        context = value if isinstance(value, ReviewContext) else ReviewContext.from_mapping(value)
        problem = self.reconstructor.reconstruct(context.manifest, context.manuscript_text)
        method_profiles = self.router.profiles(problem.get("methods", []))
        findings, not_assessable = self.rules.run(context, problem)
        debate_notes = self.debates.relevant(problem, context.manifest)
        if self.provider is not None:
            request = self.semantic.build_request(context, problem, method_profiles, debate_notes)
            response = self.provider.review(request)
            semantic_findings, semantic_not_assessable = self.semantic.validate_response(response, context)
            findings.extend(semantic_findings)
            not_assessable.extend(semantic_not_assessable)
        report = ReviewReport(
            source_name=context.source_name,
            reconstructed_problem=deep_sanitize(problem),
            findings=self._deduplicate(findings),
            debate_notes=deep_sanitize(debate_notes),
            not_assessable=deep_sanitize(not_assessable),
            metadata={
                "mode": context.mode,
                "exhaustive": context.exhaustive,
                "registry_counts": registry_counts(),
                "method_profiles": deep_sanitize(method_profiles),
                "method_families": self.router.families_for(problem.get("methods", [])),
                "score_contract": "Revision-priority scores, not probabilities of validity or authorship.",
                "semantic_provider_used": self.provider is not None,
            },
        )
        report.dimension_scores = self._scores(report.findings)
        report.gates = self._gates(report.findings, problem)
        return report

    def _scores(self, findings: list[ReviewFinding]) -> dict[str, int]:
        dimensions = {
            "problem_and_estimand",
            "design_and_sampling",
            "identification",
            "estimation",
            "diagnostics_and_sensitivity",
            "numeric_integrity",
            "interpretation_and_reporting",
            "equity_and_transportability",
            "reproducibility",
            "cross_domain_coherence",
        }
        penalties: dict[str, float] = defaultdict(float)
        repeats: dict[tuple[str, str], int] = defaultdict(int)
        for finding in findings:
            dimension = DOMAIN_DIMENSIONS.get(finding.domain, "cross_domain_coherence")
            key = (dimension, finding.rule_id)
            repeats[key] += 1
            diminishing = 1 / (repeats[key] ** 0.5)
            penalties[dimension] += PENALTIES[finding.severity] * diminishing
        return {dimension: max(0, int(round(100 - penalties.get(dimension, 0)))) for dimension in sorted(dimensions)}

    def _gates(self, findings: list[ReviewFinding], problem: dict[str, Any]) -> list[ReviewGate]:
        definitions = [
            (
                "G1",
                "Scientific target defined",
                {"problem_definition", "estimand"},
                {"fatal", "critical"},
                "The task, target population, outcome, and target quantity must be interpretable independently of the estimator.",
            ),
            (
                "G2",
                "Design and sampling coherent",
                {"study_design", "complex_survey", "dhs", "randomized_trials"},
                {"fatal", "critical"},
                "Selection, assignment, timing, units, and survey design must support the declared target.",
            ),
            (
                "G3",
                "Identification adequate",
                {
                    "causal_identification", "target_trial_emulation", "survey_causal_inference",
                    "continuous_causal_inference", "joint_causal_inference", "intersectional_causal_inference",
                    "network_causal_inference", "transportability",
                },
                {"fatal", "critical"},
                "The target must be identified under explicit, defensible assumptions.",
            ),
            (
                "G4",
                "Estimator aligned",
                {"method_selection", "regression", "prediction", "bayesian_inference", "time_to_event", "meta_analysis", "spatial_statistics"},
                {"fatal", "critical"},
                "The estimator and fitted model must target the declared quantity and respect the data structure.",
            ),
            (
                "G5",
                "Uncertainty and diagnostics adequate",
                {"diagnostics", "sensitivity_analysis", "missing_data", "numeric_consistency"},
                {"fatal", "critical"},
                "Precision, diagnostics, missingness, and sensitivity analyses must support the reported uncertainty.",
            ),
            (
                "G6",
                "Interpretation bounded",
                {"interpretation", "reporting", "prediction_fairness", "intersectionality"},
                {"fatal", "critical"},
                "Claims must not exceed the design, estimand, data support, or uncertainty.",
            ),
            (
                "G7",
                "Cross-domain contradictions absent",
                {"interaction"},
                {"fatal", "critical"},
                "No combination of individually plausible choices may create a larger incoherence.",
            ),
            (
                "G8",
                "Replication package sufficient",
                {"reproducibility"},
                {"fatal", "critical"},
                "The supplied materials must be sufficient for the selected review mode.",
            ),
        ]
        gates: list[ReviewGate] = []
        for gate_id, name, domains, severities, rationale in definitions:
            blockers = [f.rule_id for f in findings if f.domain in domains and f.severity in severities and f.status == "open"]
            gates.append(ReviewGate(gate_id, name, not blockers, rationale, sorted(set(blockers))))
        if problem.get("task") != "causal_effect":
            causal_gate = next(g for g in gates if g.gate_id == "G3")
            if not causal_gate.blocking_rule_ids:
                causal_gate.rationale = "No causal-effect claim was declared; causal identification gate is not applicable beyond any detected causal wording."
        return gates

    @staticmethod
    def _deduplicate(findings: list[ReviewFinding]) -> list[ReviewFinding]:
        seen: set[tuple[str, str | None, str]] = set()
        output: list[ReviewFinding] = []
        for finding in findings:
            marker = (finding.rule_id, finding.location, repr(finding.observed))
            if marker in seen:
                continue
            seen.add(marker)
            output.append(finding)
        return output
