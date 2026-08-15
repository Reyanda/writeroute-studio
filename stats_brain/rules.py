from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .calculators import StatisticalCalculators
from .knowledge import load_yaml
from .models import ReviewContext, ReviewFinding
from .utils import as_list, contains_any, first_present, finite_number, get_path, normalize_key, present, unique_preserve


CAUSAL_WORDS = (
    "caused", "causes", "causal effect", "effect of", "impact of", "led to", "resulted in",
    "prevented", "increased the risk", "reduced the risk", "attributable to",
)
NULL_WORDS = ("no effect", "no association", "no difference", "equivalent", "the same")
P_VALUE_PROOF_WORDS = (
    "statistically significant therefore", "proved", "demonstrated conclusively", "confirmed that",
)


class RuleEngine:
    """Design-aware, estimand-first review rules.

    The engine intentionally distinguishes an observed error from a missing input.
    Missing information produces a query or not-assessable item unless the omission
    itself makes the scientific claim uninterpretable.
    """

    def __init__(self) -> None:
        self.designs = load_yaml("design_profiles").get("profiles", {})
        self.estimands = load_yaml("estimand_registry").get("estimands", {})
        method_registry = load_yaml("method_registry")
        self.methods = method_registry.get("methods", {})
        self.families = method_registry.get("families", {})
        self.sources = load_yaml("source_registry").get("sources", {})

    def run(self, context: ReviewContext, problem: dict[str, Any]) -> tuple[list[ReviewFinding], list[dict[str, Any]]]:
        findings: list[ReviewFinding] = []
        not_assessable: list[dict[str, Any]] = []
        findings.extend(self._core(context, problem))
        findings.extend(self._design_contract(context, problem))
        findings.extend(self._estimand_contract(context, problem))
        findings.extend(self._method_contracts(context, problem))
        findings.extend(self._causal(context, problem))
        findings.extend(self._target_trial(context, problem))
        findings.extend(self._survey_and_dhs(context, problem))
        findings.extend(self._prediction_ml(context, problem))
        findings.extend(self._continuous_and_joint(context, problem))
        findings.extend(self._intersectional(context, problem))
        findings.extend(self._survival_longitudinal(context, problem))
        findings.extend(self._missing_data(context, problem))
        findings.extend(self._bayesian(context, problem))
        findings.extend(self._classical_models(context, problem))
        findings.extend(self._trials(context, problem))
        findings.extend(self._meta_analysis(context, problem))
        findings.extend(self._spatial_network(context, problem))
        findings.extend(self._numeric_checks(context, problem))
        findings.extend(self._text_claim_checks(context, problem))
        findings.extend(self._interaction_findings(findings, context, problem))

        if not context.artifacts.get("raw_data") and not context.artifacts.get("model_objects"):
            not_assessable.extend(
                [
                    {
                        "domain": "computation",
                        "item": "model_fit_and_diagnostics",
                        "reason": "No raw data or executable model object was supplied.",
                        "required_for": "Independent verification of diagnostics, convergence, influence, and uncertainty.",
                    },
                    {
                        "domain": "reproducibility",
                        "item": "analysis_reexecution",
                        "reason": "No executable code and data bundle was supplied.",
                        "required_for": "Replication of estimates and tables.",
                    },
                ]
            )
        return self._deduplicate(findings), not_assessable

    def _f(
        self,
        rule_id: str,
        title: str,
        domain: str,
        severity: str,
        epistemic_status: str,
        *,
        observed: Any = None,
        expected: Any = None,
        rationale: str,
        repair: str,
        location: str | None = None,
        excerpt: str | None = None,
        source_ids: Sequence[str] = (),
        confidence: str = "high",
        manual_review: bool = False,
        interaction_of: Sequence[str] = (),
        tags: Sequence[str] = (),
    ) -> ReviewFinding:
        return ReviewFinding(
            rule_id=rule_id,
            title=title,
            domain=domain,
            severity=severity,
            epistemic_status=epistemic_status,
            observed=observed,
            expected=expected,
            rationale=rationale,
            repair=repair,
            location=location,
            evidence_excerpt=excerpt,
            source_ids=list(source_ids),
            confidence=confidence,
            manual_review=manual_review,
            interaction_of=list(interaction_of),
            tags=list(tags),
        )

    def _core(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        manifest = context.manifest
        findings: list[ReviewFinding] = []
        required = [
            ("SB-CORE-001", "Statistical task is undefined", problem.get("task"), "study.task", "critical"),
            ("SB-CORE-002", "Study design is undefined", problem.get("design"), "study.design", "critical"),
            ("SB-CORE-003", "Target population is undefined", problem.get("target_population"), "study.target_population", "major"),
            ("SB-CORE-004", "Outcome is undefined", problem.get("outcome"), "question.outcome", "critical"),
            (
                "SB-CORE-005",
                "Unit of analysis is undefined",
                first_present(manifest, "study.unit_of_analysis", "unit_of_analysis"),
                "study.unit_of_analysis",
                "major",
            ),
            (
                "SB-CORE-006",
                "Analysis method is not identified",
                problem.get("methods"),
                "analysis.methods",
                "major",
            ),
        ]
        for rule_id, title, value, path, severity in required:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "problem_definition",
                        severity,
                        "consensus_requirement",
                        observed=value,
                        expected=path,
                        rationale="A statistical method cannot be judged coherently until the scientific target and data structure are stated.",
                        repair=f"Declare {path} explicitly in the review manifest and manuscript.",
                        location=path,
                    )
                )
        sample_size = first_present(manifest, "study.sample_size", "sample_size")
        if not present(sample_size):
            findings.append(
                self._f(
                    "SB-CORE-007",
                    "Analysis denominator is not reported",
                    "reporting",
                    "major",
                    "consensus_requirement",
                    observed=None,
                    expected="Analysis sample size and reasons for exclusions",
                    rationale="Effect estimates and precision cannot be interpreted without the population actually analysed.",
                    repair="Report the eligible, included, analysed, and outcome-observed denominators for every primary analysis.",
                    location="study.sample_size",
                )
            )
        uncertainty = first_present(manifest, "analysis.uncertainty", "uncertainty")
        if not present(uncertainty):
            findings.append(
                self._f(
                    "SB-CORE-008",
                    "Uncertainty procedure is unspecified",
                    "statistical_inference",
                    "major",
                    "consensus_requirement",
                    observed=None,
                    expected="Variance estimator, interval procedure, resampling scheme, or posterior interval",
                    rationale="A point estimate without a valid uncertainty procedure does not communicate sampling or posterior uncertainty.",
                    repair="Name the uncertainty procedure and show that it respects clustering, weighting, repeated measures, model fitting, and any resampling or imputation.",
                    location="analysis.uncertainty",
                )
            )
        claims = as_list(first_present(manifest, "reporting.primary_claims", "claims", default=[]))
        if not claims and not context.manuscript_text:
            findings.append(
                self._f(
                    "SB-CORE-009",
                    "Primary inferential claim is unavailable",
                    "interpretation",
                    "query",
                    "not_assessable",
                    rationale="The reviewer needs the exact claim to test whether the interpretation matches the estimand and estimator.",
                    repair="Supply the exact primary Results and Conclusion sentences.",
                    location="reporting.primary_claims",
                )
            )
        prereg = first_present(manifest, "reproducibility.protocol", "reproducibility.preregistration")
        code = first_present(manifest, "reproducibility.code", "analysis.code")
        if context.mode in {"forensic", "replication"} and not present(code):
            findings.append(
                self._f(
                    "SB-CORE-010",
                    "Executable analysis code is missing",
                    "reproducibility",
                    "critical" if context.mode == "replication" else "major",
                    "consensus_requirement",
                    observed=code,
                    expected="Versioned executable code with environment information",
                    rationale="Forensic or replication review requires reconstruction of the exact analysis rather than prose-level inspection alone.",
                    repair="Provide scripts, dependency versions, random seeds, data provenance, and a single command that rebuilds the reported outputs.",
                    location="reproducibility.code",
                )
            )
        if context.mode == "protocol" and not present(prereg):
            findings.append(
                self._f(
                    "SB-CORE-011",
                    "Protocol or analysis plan is not identified",
                    "reproducibility",
                    "major",
                    "consensus_requirement",
                    rationale="Protocol review requires a stable record of planned estimands, analyses, outcomes, and deviations.",
                    repair="Provide the dated protocol or statistical analysis plan and define how amendments will be recorded.",
                    location="reproducibility.protocol",
                )
            )
        return findings

    def _design_contract(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        design_id = problem.get("design")
        task = problem.get("task")
        findings: list[ReviewFinding] = []
        if not design_id:
            return findings
        profile = self.designs.get(design_id)
        if profile is None:
            return [
                self._f(
                    "SB-DESIGN-001",
                    "Study design is not registered",
                    "study_design",
                    "major",
                    "not_assessable",
                    observed=design_id,
                    expected="A registered design profile or a custom design contract",
                    rationale="Unregistered designs can still be reviewed, but their design-specific failure modes cannot be assumed.",
                    repair="Map the design to an existing profile or add a custom profile with assignment, sampling, time, and analysis requirements.",
                    location="study.design",
                )
            ]
        compatible = set(profile.get("compatible_tasks", []))
        if task and compatible and task not in compatible:
            findings.append(
                self._f(
                    "SB-DESIGN-002",
                    "Declared task is not supported by the design without additional assumptions",
                    "study_design",
                    "critical" if task == "causal_effect" else "major",
                    "consensus_requirement",
                    observed={"task": task, "design": design_id},
                    expected=sorted(compatible),
                    rationale="A design does not acquire causal, predictive, or diagnostic validity merely because a sophisticated estimator is applied.",
                    repair="Reframe the task, strengthen the design and identification strategy, or state the additional assumptions needed to identify the target.",
                    location="study.task",
                )
            )
        missing = []
        for field in profile.get("required_manifest_fields", []):
            alternatives = {
                "outcomes": ("outcomes", "question.outcome", "estimand.outcome"),
                "outcome": ("outcome", "question.outcome", "estimand.outcome"),
                "exposure": ("exposure", "question.exposure", "question.intervention"),
                "analysis": ("analysis", "analysis.methods"),
                "sampling": ("sampling",),
                "target_population": ("target_population", "study.target_population", "estimand.target_population"),
                "time_zero": ("time_zero", "study.time_zero", "estimand.time_zero"),
            }.get(field, (field, f"study.{field}", f"analysis.{field}"))
            if not any(present(get_path(context.manifest, path)) for path in alternatives):
                missing.append(field)
        if missing:
            findings.append(
                self._f(
                    "SB-DESIGN-003",
                    "Design contract is incomplete",
                    "study_design",
                    "major",
                    "consensus_requirement",
                    observed=missing,
                    expected=profile.get("required_manifest_fields", []),
                    rationale=f"The registered contract for {profile.get('name', design_id)} requires these elements to judge selection, timing, and analysis.",
                    repair="Provide each missing design element or explain why it does not apply.",
                    location="study.design",
                    source_ids=[profile.get("reporting_guideline")] if profile.get("reporting_guideline") else [],
                )
            )
        declared_hazards = set(as_list(first_present(context.manifest, "bias.addressed_hazards", "analysis.addressed_hazards", default=[])))
        unaddressed = [hazard for hazard in profile.get("hard_hazards", []) if hazard not in declared_hazards]
        if unaddressed and context.exhaustive:
            findings.append(
                self._f(
                    "SB-DESIGN-004",
                    "Design-specific bias pathways are not adjudicated",
                    "bias",
                    "major",
                    "consensus_requirement",
                    observed=unaddressed,
                    expected="A design-specific assessment for each hard hazard",
                    rationale="These are characteristic ways in which this design can fail. Silence does not establish their absence.",
                    repair="For each listed hazard, state whether it is absent, controlled by design, addressed analytically, examined by sensitivity analysis, or unresolved.",
                    location="bias.addressed_hazards",
                )
            )
        return findings

    def _estimand_contract(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        task = problem.get("task")
        estimand_id = problem.get("estimand_id")
        findings: list[ReviewFinding] = []
        if task in {"causal_effect", "prediction", "decision"} and not estimand_id:
            findings.append(
                self._f(
                    "SB-EST-001",
                    "Target quantity is not named independently of the estimator",
                    "estimand",
                    "critical",
                    "consensus_requirement",
                    observed=None,
                    expected="A registered estimand or a complete custom estimand",
                    rationale="A method name such as regression, TMLE, random forest, or Cox model does not define the scientific quantity being estimated.",
                    repair="Define the target population, treatment or predictor strategy, outcome, time horizon, summary measure, and effect or performance scale before selecting the estimator.",
                    source_ids=["ICH_E9_R1"] if task == "causal_effect" else [],
                    location="estimand",
                )
            )
            return findings
        if not estimand_id:
            return findings
        profile = self.estimands.get(estimand_id)
        if profile is None:
            findings.append(
                self._f(
                    "SB-EST-002",
                    "Estimand is not registered",
                    "estimand",
                    "major",
                    "not_assessable",
                    observed=estimand_id,
                    expected="Registered estimand or custom definition",
                    rationale="A custom estimand may be valid, but its attributes and identification conditions must be explicit.",
                    repair="Add a custom estimand definition containing population, treatment conditions, outcome, time, summary measure, and intercurrent-event handling where relevant.",
                    location="estimand.id",
                )
            )
            return findings
        missing = []
        estimand_manifest = context.manifest.get("estimand", {}) if isinstance(context.manifest.get("estimand"), Mapping) else {}
        aliases = {
            "target_population": ("target_population",),
            "variable_or_case_definition": ("variable_or_case_definition", "outcome"),
            "time_or_period": ("time_or_period", "time_horizon"),
            "summary_measure": ("summary_measure", "effect_measure"),
            "sampling_target": ("sampling_target", "target_population"),
            "treatment_strategies": ("treatment_strategies", "intervention", "exposure"),
            "intervention_or_index_condition": ("treatment_strategies", "intervention", "exposure", "index_condition"),
            "comparator_or_reference": ("comparator", "reference", "reference_condition"),
            "outcome": ("outcome",),
            "time_horizon": ("time_horizon", "time_or_period"),
            "effect_scale": ("effect_scale", "summary_measure"),
        }
        for field in profile.get("required_definition", []):
            candidates = aliases.get(field, (field,))
            if not any(present(estimand_manifest.get(candidate)) for candidate in candidates):
                if field == "target_population" and present(problem.get("target_population")):
                    continue
                if field in {"outcome", "variable_or_case_definition"} and present(problem.get("outcome")):
                    continue
                if field in {"time_horizon", "time_or_period"} and present(problem.get("time_horizon")):
                    continue
                missing.append(field)
        if missing:
            findings.append(
                self._f(
                    "SB-EST-003",
                    "Estimand attributes are incomplete",
                    "estimand",
                    "critical" if task == "causal_effect" else "major",
                    "consensus_requirement",
                    observed=missing,
                    expected=profile.get("required_definition", []),
                    rationale=f"The estimand '{profile.get('name', estimand_id)}' is not fully defined, so estimator alignment and interpretation remain ambiguous.",
                    repair="Complete every missing estimand attribute before evaluating the analysis.",
                    location="estimand",
                    source_ids=["ICH_E9_R1"] if task == "causal_effect" else [],
                )
            )
        return findings

    def _method_contracts(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        task = problem.get("task")
        estimand_id = problem.get("estimand_id")
        diagnostics = set(normalize_key(str(item)) for item in as_list(first_present(context.manifest, "analysis.diagnostics", default=[])))
        for method_id in problem.get("methods", []):
            profile = self.methods.get(method_id)
            if profile is None:
                findings.append(
                    self._f(
                        "SB-METHOD-001",
                        "Analysis method is not registered",
                        "method_selection",
                        "major",
                        "not_assessable",
                        observed=method_id,
                        expected="Registered method or custom method contract",
                        rationale="The reviewer cannot infer assumptions from an unknown label.",
                        repair="Provide the exact algorithm, target parameter, fitting procedure, tuning, uncertainty method, diagnostics, and software implementation.",
                        location="analysis.methods",
                    )
                )
                continue
            compatible = set(profile.get("task_classes", []))
            if task and compatible and task not in compatible:
                findings.append(
                    self._f(
                        "SB-METHOD-002",
                        "Method is misaligned with the scientific task",
                        "method_selection",
                        "critical",
                        "known_error",
                        observed={"method": method_id, "task": task},
                        expected=sorted(compatible),
                        rationale="Estimation, prediction, description, and causal identification are different tasks. Performance in one does not validate another.",
                        repair="Select a method designed for the declared task or revise the task and interpretation.",
                        location="analysis.methods",
                    )
                )
            registered_estimands = set(profile.get("estimands", []))
            if estimand_id and registered_estimands and estimand_id not in registered_estimands:
                findings.append(
                    self._f(
                        "SB-METHOD-003",
                        "Estimator and estimand require explicit reconciliation",
                        "method_selection",
                        "major",
                        "context_dependent",
                        observed={"method": method_id, "estimand": estimand_id},
                        expected=sorted(registered_estimands),
                        rationale="The method profile does not directly list the declared estimand. A transformation, standardization step, or alternative estimator may be needed.",
                        repair="Show the estimating equation or mapping from the fitted model to the declared estimand, including standardization and uncertainty propagation.",
                        location="analysis.methods",
                    )
                )
            required = set(profile.get("key_checks", [])) or set(profile.get("minimum_review", []))
            missing = sorted(item for item in required if normalize_key(str(item)) not in diagnostics)
            if missing and context.exhaustive:
                findings.append(
                    self._f(
                        "SB-METHOD-004",
                        "Method-specific diagnostics are incompletely documented",
                        "diagnostics",
                        "major",
                        "consensus_requirement",
                        observed={"method": method_id, "missing": missing},
                        expected=sorted(required),
                        rationale="A fitted model is not validated merely by convergence or the production of an estimate.",
                        repair="Report each diagnostic, its result, the decision threshold if one was used, and the action taken when a diagnostic was unsatisfactory.",
                        location="analysis.diagnostics",
                    )
                )
        return findings

    def _causal(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        if problem.get("task") != "causal_effect":
            return []
        manifest = context.manifest
        findings: list[ReviewFinding] = []
        requirements = [
            ("SB-CAUSAL-001", "Intervention or exposure strategy is not well defined", problem.get("exposure_or_intervention"), "estimand.treatment_strategies", "fatal"),
            ("SB-CAUSAL-002", "Comparator strategy is undefined", problem.get("comparator"), "estimand.comparator", "critical"),
            ("SB-CAUSAL-003", "Causal time zero is undefined", problem.get("time_zero"), "estimand.time_zero", "critical"),
            ("SB-CAUSAL-004", "Causal outcome horizon is undefined", problem.get("time_horizon"), "estimand.time_horizon", "critical"),
            ("SB-CAUSAL-005", "Causal model is not supplied", first_present(manifest, "causal.dag", "causal.model", "causal.framework"), "causal.dag", "major"),
            ("SB-CAUSAL-006", "Conditional exchangeability is not defended", get_path(manifest, "causal.exchangeability"), "causal.exchangeability", "critical"),
            ("SB-CAUSAL-007", "Positivity is not assessed", get_path(manifest, "causal.positivity"), "causal.positivity", "critical"),
            ("SB-CAUSAL-008", "Consistency and treatment versions are not addressed", get_path(manifest, "causal.consistency"), "causal.consistency", "critical"),
            ("SB-CAUSAL-009", "Interference assumption is not addressed", get_path(manifest, "causal.interference"), "causal.interference", "major"),
            ("SB-CAUSAL-010", "Informative censoring is not addressed", first_present(manifest, "causal.censoring", "analysis.censoring"), "causal.censoring", "major"),
        ]
        for rule_id, title, value, path, severity in requirements:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "causal_identification",
                        severity,
                        "consensus_requirement",
                        observed=value,
                        expected=path,
                        rationale="Causal estimation requires a defined intervention and explicit identification assumptions. An estimator cannot repair an unidentified target.",
                        repair=f"State and defend {path}. When the assumption is untestable, provide design evidence and sensitivity analysis rather than claiming it was verified.",
                        location=path,
                        source_ids=["HERNAN_ROBINS_BOOK", "GREENLAND_ROBINS_1986"],
                    )
                )
        covariates = as_list(first_present(manifest, "analysis.covariates", default=[]))
        post = set(as_list(first_present(manifest, "causal.post_treatment_variables", default=[])))
        colliders = set(as_list(first_present(manifest, "causal.colliders", default=[])))
        adjusted = set(str(item) for item in covariates)
        if adjusted & post:
            findings.append(
                self._f(
                    "SB-CAUSAL-011",
                    "Post-treatment variables are included in the adjustment set",
                    "causal_identification",
                    "critical",
                    "known_error",
                    observed=sorted(adjusted & post),
                    expected="A covariate set justified by the causal graph for the declared total or direct effect",
                    rationale="Adjustment for descendants of treatment can block part of the effect or induce selection bias.",
                    repair="Remove post-treatment variables for a total-effect estimand or redefine and identify an appropriate direct-effect estimand.",
                    location="analysis.covariates",
                )
            )
        if adjusted & colliders:
            findings.append(
                self._f(
                    "SB-CAUSAL-012",
                    "Collider variables are adjusted for",
                    "causal_identification",
                    "critical",
                    "known_error",
                    observed=sorted(adjusted & colliders),
                    expected="No conditioning on a collider or its descendant unless a valid correction strategy is used",
                    rationale="Conditioning on a collider can create a noncausal association between its causes.",
                    repair="Revise the adjustment set using the causal graph and explain any unavoidable selection mechanism.",
                    location="analysis.covariates",
                )
            )
        if get_path(manifest, "causal.time_varying_confounding") is True:
            methods = set(problem.get("methods", []))
            valid = {
                "marginal_structural_model", "g_formula", "sequential_g_formula", "longitudinal_tmle",
                "structural_nested_mean_model", "g_estimation",
            }
            if not methods & valid:
                findings.append(
                    self._f(
                        "SB-CAUSAL-013",
                        "Time-varying confounding affected by prior treatment is handled by an ordinary adjustment model",
                        "causal_identification",
                        "critical",
                        "known_error",
                        observed=sorted(methods),
                        expected=sorted(valid),
                        rationale="Ordinary regression adjustment can block mediated effects and induce bias when time-varying confounders are themselves affected by prior treatment.",
                        repair="Use a longitudinal g-method or justify an alternative identification strategy for the declared estimand.",
                        location="analysis.methods",
                        source_ids=["ROBINS_GMETHODS", "HERNAN_ROBINS_BOOK"],
                    )
                )
        if not present(first_present(manifest, "analysis.sensitivity", "causal.sensitivity")):
            findings.append(
                self._f(
                    "SB-CAUSAL-014",
                    "No causal sensitivity analysis is specified",
                    "sensitivity_analysis",
                    "major",
                    "consensus_requirement",
                    rationale="Identification usually depends on untestable assumptions about unmeasured confounding, measurement, censoring, or treatment versions.",
                    repair="Tie each sensitivity analysis to a named assumption and report the range of conclusions under plausible departures.",
                    location="analysis.sensitivity",
                )
            )
        transport = first_present(manifest, "causal.transportability", "generalizability")
        if problem.get("sampling_target") and problem.get("target_population") and problem.get("sampling_target") != problem.get("target_population") and not present(transport):
            findings.append(
                self._f(
                    "SB-CAUSAL-015",
                    "Study sample and causal target population differ without a transport strategy",
                    "transportability",
                    "critical",
                    "consensus_requirement",
                    observed={"sample_target": problem.get("sampling_target"), "causal_target": problem.get("target_population")},
                    expected="Explicit sampling, selection, and effect-modifier assumptions for transport",
                    rationale="Internal causal identification does not by itself identify the effect in a different target population.",
                    repair="Define the target population, identify selection variables and effect modifiers, assess overlap, and use or justify a transport estimator.",
                    source_ids=["GENERALIZING_TRIALS_2017"],
                    location="causal.transportability",
                )
            )
        return findings

    def _target_trial(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        design = problem.get("design")
        methods = set(problem.get("methods", []))
        tte = design == "target_trial_emulation" or any("target_trial" in method for method in methods) or get_path(context.manifest, "causal.target_trial")
        if not tte:
            return []
        manifest = context.manifest
        protocol = first_present(manifest, "target_trial", "causal.target_trial", default={})
        protocol = protocol if isinstance(protocol, Mapping) else {}
        findings: list[ReviewFinding] = []
        components = {
            "eligibility_criteria": "Eligibility criteria",
            "treatment_strategies": "Treatment strategies",
            "assignment_procedure": "Assignment procedure",
            "time_zero": "Time zero",
            "follow_up": "Follow-up period",
            "outcome": "Outcome definition",
            "causal_contrast": "Causal contrast or estimand",
            "analysis_plan": "Analysis plan",
        }
        missing = [label for field, label in components.items() if not present(protocol.get(field))]
        if missing:
            findings.append(
                self._f(
                    "SB-TTE-001",
                    "Target trial protocol is incomplete",
                    "target_trial_emulation",
                    "critical",
                    "consensus_requirement",
                    observed=missing,
                    expected=list(components.values()),
                    rationale="A target trial emulation is defined by explicit protocol components, not by retrospective use of causal terminology.",
                    repair="Write the complete target trial protocol before specifying the observational emulation.",
                    source_ids=["TARGET_2025", "HERNAN_ROBINS_BOOK"],
                    location="target_trial",
                )
            )
        eligibility_time = protocol.get("eligibility_time") or protocol.get("time_zero")
        assignment_time = protocol.get("assignment_time") or protocol.get("time_zero")
        follow_start = protocol.get("follow_up_start") or protocol.get("time_zero")
        if present(eligibility_time) and present(assignment_time) and present(follow_start):
            if len({str(eligibility_time), str(assignment_time), str(follow_start)}) > 1:
                findings.append(
                    self._f(
                        "SB-TTE-002",
                        "Eligibility, assignment, and follow-up are not aligned at time zero",
                        "target_trial_emulation",
                        "fatal",
                        "known_error",
                        observed={"eligibility": eligibility_time, "assignment": assignment_time, "follow_up": follow_start},
                        expected="A common causal time zero unless a formally justified design specifies otherwise",
                        rationale="Misaligned time origins create immortal time, selection, or prevalent-user bias.",
                        repair="Realign eligibility, treatment assignment, and follow-up or redesign the emulation using cloning, censoring, weighting, or another valid strategy.",
                        source_ids=["TARGET_2025", "HERNAN_ROBINS_BOOK"],
                        location="target_trial.time_zero",
                    )
                )
        grace = protocol.get("grace_period")
        if present(grace) and not present(protocol.get("grace_period_strategy")):
            findings.append(
                self._f(
                    "SB-TTE-003",
                    "Treatment grace period lacks an assignment and censoring strategy",
                    "target_trial_emulation",
                    "critical",
                    "consensus_requirement",
                    observed=grace,
                    expected="A strategy that prevents immortal-time and treatment-confounder feedback bias",
                    rationale="Classifying treatment during a grace period without a formal strategy can give treated participants guaranteed survival time.",
                    repair="Specify cloning, censoring, weighting, sequential trials, or another justified procedure and its assumptions.",
                    location="target_trial.grace_period_strategy",
                )
            )
        if get_path(manifest, "analysis.baseline_covariates_measured_after_time_zero") is True:
            findings.append(
                self._f(
                    "SB-TTE-004",
                    "Baseline covariates use information recorded after causal time zero",
                    "target_trial_emulation",
                    "fatal",
                    "known_error",
                    rationale="Post-time-zero information can encode future treatment or outcome information and invalidate baseline exchangeability.",
                    repair="Restrict baseline covariates to information available at or before time zero, or redefine time zero.",
                    location="analysis.baseline_covariates",
                )
            )
        return findings

    def _survey_and_dhs(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        manifest = context.manifest
        design = str(problem.get("design") or "")
        methods = set(problem.get("methods", []))
        source = str(first_present(manifest, "study.data_source", "data_source", default="")).casefold()
        is_dhs = "dhs" in source or "demographic and health survey" in source or any(method.startswith("dhs_") for method in methods)
        is_survey = is_dhs or "survey" in design or any("survey" in method for method in methods) or get_path(manifest, "sampling.complex") is True
        if not is_survey:
            return []
        findings: list[ReviewFinding] = []
        sampling = manifest.get("sampling", {}) if isinstance(manifest.get("sampling"), Mapping) else {}
        required = [
            ("SB-SURVEY-001", "Sampling weight is missing", sampling.get("weight"), "sampling.weight", "critical"),
            ("SB-SURVEY-002", "Primary sampling unit is missing", sampling.get("psu"), "sampling.psu", "critical"),
            ("SB-SURVEY-003", "Stratification variable is missing", sampling.get("strata"), "sampling.strata", "critical"),
            ("SB-SURVEY-004", "Survey variance estimator is unspecified", sampling.get("variance_method"), "sampling.variance_method", "major"),
            ("SB-SURVEY-005", "Survey target population is undefined", sampling.get("target_population") or problem.get("sampling_target"), "sampling.target_population", "critical"),
        ]
        for rule_id, title, value, path, severity in required:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "complex_survey",
                        severity,
                        "consensus_requirement",
                        observed=value,
                        expected=path,
                        rationale="Point and variance estimation for a multistage probability sample must reflect the sampling design and target population.",
                        repair=f"Supply {path} and show how it entered estimation and variance calculation.",
                        source_ids=["DHS_GUIDE_8"] if is_dhs else [],
                        location=path,
                    )
                )
        if get_path(manifest, "analysis.unweighted") is True:
            findings.append(
                self._f(
                    "SB-SURVEY-006",
                    "Complex survey analysis is unweighted",
                    "complex_survey",
                    "critical",
                    "context_dependent",
                    observed=True,
                    expected="A design-based or model-based justification tied to the target estimand",
                    rationale="Ignoring unequal inclusion and nonresponse adjustments can change the target and bias population summaries. In some model-based analyses weights may be omitted, but that choice requires explicit justification and sensitivity analysis.",
                    repair="Define the target under weighted and unweighted analyses, justify the primary choice, and compare conclusions under plausible weighting strategies.",
                    location="analysis.unweighted",
                )
            )
        if get_path(manifest, "analysis.subpopulation_deleted") is True:
            findings.append(
                self._f(
                    "SB-SURVEY-007",
                    "Subpopulation analysis deletes out-of-domain observations before variance estimation",
                    "complex_survey",
                    "major",
                    "known_error",
                    rationale="Deleting observations can remove design information needed for correct domain variance estimation.",
                    repair="Use a survey-domain or subpopulation estimator that retains the full design information.",
                    location="analysis.subpopulation_deleted",
                )
            )
        if present(sampling.get("pooled_surveys")) and not present(sampling.get("pooled_target")):
            findings.append(
                self._f(
                    "SB-SURVEY-008",
                    "Pooled-survey weighting target is ambiguous",
                    "complex_survey",
                    "critical",
                    "consensus_requirement",
                    observed=sampling.get("pooled_surveys"),
                    expected="A defined population-time target and weight normalization scheme",
                    rationale="Raw survey weights from different surveys do not automatically target an interpretable pooled population.",
                    repair="Define whether the target is an average survey, pooled person-time population, country-specific trend, or another target, then rescale weights accordingly.",
                    location="sampling.pooled_target",
                )
            )
        if problem.get("task") == "causal_effect":
            if not present(sampling.get("treatment_weights")) and any(method in methods for method in {"survey_weighted_iptw", "survey_propensity_score_analysis", "survey_weighted_tmle"}):
                findings.append(
                    self._f(
                        "SB-SURVEY-009",
                        "Sampling weights and causal treatment weights are not distinguished",
                        "survey_causal_inference",
                        "critical",
                        "known_error",
                        observed=sampling.get("weight"),
                        expected="Separate definitions for sampling or selection weights and treatment or censoring weights",
                        rationale="Sampling weights recover a population target; treatment weights address exchangeability. They solve different problems and cannot be substituted for one another.",
                        repair="Define each weight, its numerator and denominator, stabilization, truncation, product or integration rule, target population, and variance procedure.",
                        location="sampling.treatment_weights",
                        source_ids=["COMPLEX_SURVEY_CAUSAL_2025", "DUGOFF_2014"],
                    )
                )
            if not present(sampling.get("combined_weight_diagnostics")):
                findings.append(
                    self._f(
                        "SB-SURVEY-010",
                        "Combined survey and causal weights lack diagnostics",
                        "survey_causal_inference",
                        "major",
                        "consensus_requirement",
                        rationale="The product of variable sampling and treatment weights can create severe variance inflation and practical positivity problems.",
                        repair="Report weight distributions, truncation rules, effective sample size, covariate balance, design effects, and sensitivity to alternative combination strategies.",
                        location="sampling.combined_weight_diagnostics",
                    )
                )
        if is_dhs:
            weight_name = str(sampling.get("weight", ""))
            scaled = sampling.get("weight_scaled")
            if weight_name.lower() in {"v005", "hv005", "mv005", "d005"} and scaled is not True:
                findings.append(
                    self._f(
                        "SB-DHS-001",
                        "DHS integer weight scaling is not documented",
                        "dhs",
                        "major",
                        "consensus_requirement",
                        observed={"weight": weight_name, "weight_scaled": scaled},
                        expected="Division by 1,000,000 or equivalent software handling, documented explicitly",
                        rationale="DHS sampling weights are stored as six-digit integers. Scale does not change normalized point estimates in many procedures, but it can affect totals and reproducibility.",
                        repair="Document the exact transformation and verify that totals, normalization, and combined weights target the intended population.",
                        location="sampling.weight_scaled",
                        source_ids=["DHS_GUIDE_8"],
                    )
                )
            if not present(first_present(manifest, "dhs.recode_file", "study.recode_file")):
                findings.append(
                    self._f(
                        "SB-DHS-002",
                        "DHS recode file is not identified",
                        "dhs",
                        "major",
                        "consensus_requirement",
                        rationale="IR, MR, PR, HR, KR, BR, CR, AR, and special files represent different populations and denominators.",
                        repair="Name the recode file and justify that its unit, eligibility, and denominator match the indicator or estimand.",
                        location="dhs.recode_file",
                        source_ids=["DHS_GUIDE_8"],
                    )
                )
            if not present(first_present(manifest, "dhs.denominator_definition", "study.denominator_definition")):
                findings.append(
                    self._f(
                        "SB-DHS-003",
                        "DHS indicator denominator is not reproduced from its definition",
                        "dhs",
                        "critical",
                        "consensus_requirement",
                        rationale="Many DHS indicators use restricted age, residence, birth-history, union, or service-eligibility denominators that cannot be inferred from the numerator variable alone.",
                        repair="State the exact denominator, universe restrictions, recall window, missing codes, and recode variables used.",
                        location="dhs.denominator_definition",
                        source_ids=["DHS_GUIDE_8"],
                    )
                )
            if "gps" in " ".join(methods).lower() or get_path(manifest, "dhs.uses_gps") is True:
                if not present(get_path(manifest, "dhs.displacement_strategy")):
                    findings.append(
                        self._f(
                            "SB-DHS-004",
                            "DHS cluster-coordinate displacement is ignored",
                            "dhs_spatial",
                            "critical",
                            "consensus_requirement",
                            rationale="Displaced cluster coordinates create location uncertainty and can bias fine-scale distance, exposure linkage, boundary assignment, and spatial causal analyses.",
                            repair="Use displacement-aware linkage or uncertainty propagation and avoid unsupported fine-resolution conclusions.",
                            location="dhs.displacement_strategy",
                        )
                    )
            if get_path(manifest, "dhs.multilevel") is True and not present(get_path(manifest, "dhs.multilevel_weight_strategy")):
                findings.append(
                    self._f(
                        "SB-DHS-005",
                        "Multilevel DHS analysis lacks a level-specific weighting strategy",
                        "dhs",
                        "major",
                        "active_debate",
                        rationale="Single-level DHS weights do not uniquely determine separate weights for all levels of a multilevel likelihood. Several approximations exist and may target different quantities.",
                        repair="State the level-specific weight construction, scaling convention, target, software implementation, and sensitivity to alternative strategies.",
                        location="dhs.multilevel_weight_strategy",
                    )
                )
        return findings

    def _prediction_ml(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        methods = set(problem.get("methods", []))
        families = {self.methods[m].get("family") for m in methods if m in self.methods}
        is_prediction = problem.get("task") == "prediction" or "prediction_machine_learning_and_AI" in families
        if not is_prediction:
            return []
        manifest = context.manifest
        prediction = manifest.get("prediction", {}) if isinstance(manifest.get("prediction"), Mapping) else {}
        findings: list[ReviewFinding] = []
        required = [
            ("SB-PRED-001", "Prediction index time is undefined", prediction.get("index_time"), "prediction.index_time", "critical"),
            ("SB-PRED-002", "Prediction horizon is undefined", prediction.get("horizon") or problem.get("time_horizon"), "prediction.horizon", "critical"),
            ("SB-PRED-003", "Intended use and decision context are undefined", prediction.get("intended_use"), "prediction.intended_use", "major"),
            ("SB-PRED-004", "Validation strategy is unspecified", prediction.get("validation"), "prediction.validation", "critical"),
            ("SB-PRED-005", "Calibration assessment is missing", prediction.get("calibration"), "prediction.calibration", "critical"),
            ("SB-PRED-006", "Discrimination assessment is missing", prediction.get("discrimination"), "prediction.discrimination", "major"),
            ("SB-PRED-007", "Feature availability at prediction time is not verified", prediction.get("feature_availability"), "prediction.feature_availability", "critical"),
        ]
        for rule_id, title, value, path, severity in required:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "prediction",
                        severity,
                        "consensus_requirement",
                        observed=value,
                        expected=path,
                        rationale="Prediction performance is meaningful only for a defined use, time point, population, and validation process.",
                        repair=f"Define and report {path} according to the model's intended use.",
                        location=path,
                        source_ids=["TRIPOD_AI_2024", "PROBAST_AI_2025"],
                    )
                )
        if prediction.get("split_unit") in {None, "row"} and get_path(manifest, "study.repeated_units") is True:
            findings.append(
                self._f(
                    "SB-PRED-008",
                    "Train-test splitting occurs below the independent unit",
                    "prediction",
                    "fatal",
                    "known_error",
                    observed=prediction.get("split_unit"),
                    expected="Split by participant, cluster, site, time, or other independent deployment unit",
                    rationale="Rows from the same person or cluster in training and test sets leak information and inflate performance.",
                    repair="Repeat the full modelling pipeline with resampling at the independent deployment unit.",
                    location="prediction.split_unit",
                )
            )
        if prediction.get("preprocessing_before_split") is True:
            findings.append(
                self._f(
                    "SB-PRED-009",
                    "Preprocessing uses information from validation or test data",
                    "prediction",
                    "fatal",
                    "known_error",
                    rationale="Imputation, scaling, feature selection, oversampling, and tuning before resampling leak outcome or distributional information.",
                    repair="Nest every data-adaptive preprocessing and tuning step inside each training fold.",
                    location="prediction.preprocessing_before_split",
                )
            )
        if prediction.get("test_set_reused") is True:
            findings.append(
                self._f(
                    "SB-PRED-010",
                    "The test set was reused for model selection or threshold choice",
                    "prediction",
                    "fatal",
                    "known_error",
                    rationale="Repeated feedback from the test set turns it into training information and invalidates its performance estimate.",
                    repair="Obtain a genuinely untouched test set or use nested resampling with a final external evaluation.",
                    location="prediction.test_set_reused",
                )
            )
        metrics = set(normalize_key(str(item)) for item in as_list(prediction.get("metrics")))
        if metrics and metrics <= {"auroc", "auc", "accuracy", "f1_score"}:
            findings.append(
                self._f(
                    "SB-PRED-011",
                    "Reported performance is limited to rank or classification metrics",
                    "prediction",
                    "major",
                    "consensus_requirement",
                    observed=sorted(metrics),
                    expected="Calibration, overall performance, uncertainty, and decision-analytic usefulness in addition to discrimination",
                    rationale="A high AUROC or accuracy does not establish calibrated risk estimates or clinical value.",
                    repair="Report calibration-in-the-large, calibration slope or curve, Brier or log score where appropriate, uncertainty, and decision consequences.",
                    source_ids=["TRIPOD_AI_2024", "PROBAST_AI_2025"],
                    location="prediction.metrics",
                )
            )
        if not present(prediction.get("fairness")):
            findings.append(
                self._f(
                    "SB-PRED-012",
                    "Performance heterogeneity and fairness are not assessed",
                    "prediction_fairness",
                    "major",
                    "consensus_requirement",
                    rationale="Aggregate performance can conceal systematic miscalibration or error disparities across clinically or socially relevant groups.",
                    repair="Prespecify groups, report group-specific calibration and error tradeoffs with uncertainty, assess sample support, and connect fairness criteria to the deployment decision.",
                    location="prediction.fairness",
                    source_ids=["TRIPOD_AI_2024", "PROBAST_AI_2025"],
                )
            )
        if problem.get("task") == "causal_effect" or get_path(manifest, "prediction.feature_importance_interpreted_causally") is True:
            findings.append(
                self._f(
                    "SB-PRED-013",
                    "Predictive feature importance is interpreted as a causal effect or mechanism",
                    "prediction",
                    "critical",
                    "known_error",
                    rationale="Predictive contribution depends on correlations, model class, feature encoding, and the prediction target. It does not identify an intervention effect.",
                    repair="Restrict the interpretation to prediction, or formulate and identify a separate causal estimand.",
                    location="prediction.feature_importance",
                )
            )
        if any(method in methods for method in {"causal_forest", "double_machine_learning", "tmle", "longitudinal_tmle"}):
            if not present(first_present(manifest, "analysis.cross_fitting", "prediction.cross_fitting")):
                findings.append(
                    self._f(
                        "SB-PRED-014",
                        "Causal machine-learning nuisance fitting lacks a cross-fitting decision",
                        "causal_machine_learning",
                        "major",
                        "context_dependent",
                        rationale="Sample splitting or cross-fitting can protect asymptotic inference with highly adaptive nuisance models, but its necessity and implementation depend on the estimator and function classes.",
                        repair="State whether cross-fitting was used, define folds at the independent unit, and justify the choice using estimator theory and problem-specific simulations where needed.",
                        location="analysis.cross_fitting",
                    )
                )
        return findings

    def _continuous_and_joint(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        manifest = context.manifest
        exposure_type = normalize_key(str(first_present(manifest, "question.exposure_type", "estimand.exposure_type", default="")))
        methods = set(problem.get("methods", []))
        is_continuous = exposure_type in {"continuous", "dose", "continuous_treatment"} or any(
            method in methods for method in {"generalized_propensity_score", "continuous_treatment_tmle", "longitudinal_continuous_treatment"}
        )
        is_joint = exposure_type in {"joint", "mixture", "multiple_exposures", "composition"} or any(
            method in methods for method in {"joint_treatment_effect", "joint_exposure_mixture_model", "g_computation_mixture", "quantile_g_computation_mixture"}
        )
        findings: list[ReviewFinding] = []
        if is_continuous:
            continuous = manifest.get("continuous_exposure", {}) if isinstance(manifest.get("continuous_exposure"), Mapping) else {}
            requirements = [
                ("SB-CONT-001", "Continuous intervention is not defined", continuous.get("intervention"), "continuous_exposure.intervention", "critical"),
                ("SB-CONT-002", "Dose contrast or dose-response target is undefined", continuous.get("contrast") or continuous.get("dose_response_target"), "continuous_exposure.contrast", "critical"),
                ("SB-CONT-003", "Observed support and continuous positivity are not assessed", continuous.get("support"), "continuous_exposure.support", "critical"),
                ("SB-CONT-004", "Exposure-response functional form is not assessed", continuous.get("functional_form"), "continuous_exposure.functional_form", "major"),
            ]
            for rule_id, title, value, path, severity in requirements:
                if not present(value):
                    findings.append(
                        self._f(
                            rule_id,
                            title,
                            "continuous_causal_inference",
                            severity,
                            "consensus_requirement",
                            rationale="A continuous exposure does not define a single treatment contrast. Identification depends on feasible shifts, observed density, and the scale of the intervention.",
                            repair=f"Define {path} and restrict interpretation to supported interventions.",
                            location=path,
                            source_ids=["IMAI_VANDYK_CONTINUOUS", "HIRANO_IMBENS_GPS"],
                        )
                    )
            if continuous.get("arbitrarily_categorized") is True:
                findings.append(
                    self._f(
                        "SB-CONT-005",
                        "Continuous exposure is categorized without a scientific intervention rationale",
                        "continuous_causal_inference",
                        "major",
                        "known_error",
                        rationale="Arbitrary categorization discards information, creates threshold artifacts, and changes the estimand.",
                        repair="Model the exposure continuously with flexible functions or define policy-relevant categories and contrasts prospectively.",
                        location="continuous_exposure.arbitrarily_categorized",
                    )
                )
            if continuous.get("extrapolates_beyond_support") is True:
                findings.append(
                    self._f(
                        "SB-CONT-006",
                        "Dose-response estimates extrapolate beyond observed support",
                        "continuous_causal_inference",
                        "fatal",
                        "known_error",
                        rationale="No statistical model can recover causal effects at unsupported doses without strong, unverifiable extrapolation assumptions.",
                        repair="Restrict the estimand to supported contrasts, use stochastic or incremental interventions, or state that the target is not identified.",
                        location="continuous_exposure.support",
                    )
                )
        if is_joint:
            joint = manifest.get("joint_intervention", {}) if isinstance(manifest.get("joint_intervention"), Mapping) else {}
            required = [
                ("SB-JOINT-001", "Joint intervention is not specified", joint.get("strategies"), "joint_intervention.strategies", "critical"),
                ("SB-JOINT-002", "Joint exposure support is not evaluated", joint.get("support"), "joint_intervention.support", "critical"),
                ("SB-JOINT-003", "Component, joint, and interaction estimands are not separated", joint.get("estimands"), "joint_intervention.estimands", "critical"),
                ("SB-JOINT-004", "Co-exposure dependence is not represented", joint.get("dependence"), "joint_intervention.dependence", "major"),
            ]
            for rule_id, title, value, path, severity in required:
                if not present(value):
                    findings.append(
                        self._f(
                            rule_id,
                            title,
                            "joint_causal_inference",
                            severity,
                            "consensus_requirement",
                            rationale="Multiple exposures create a joint intervention space. Marginal component coefficients do not automatically identify joint or mixture effects.",
                            repair=f"Define {path}, including feasible exposure combinations and the scale on which interaction is evaluated.",
                            location=path,
                        )
                    )
            if joint.get("mixture_importance_interpreted_as_causal") is True:
                findings.append(
                    self._f(
                        "SB-JOINT-005",
                        "Mixture variable importance is interpreted as a component causal effect",
                        "joint_causal_inference",
                        "critical",
                        "known_error",
                        rationale="Importance weights can depend on scaling, correlation, regularization, and model form, and may not correspond to an intervention contrast.",
                        repair="Define component and joint causal estimands explicitly and use estimators that identify those targets under stated assumptions.",
                        location="joint_intervention.interpretation",
                    )
                )
        return findings

    def _intersectional(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        manifest = context.manifest
        intersection = manifest.get("intersectionality", {}) if isinstance(manifest.get("intersectionality"), Mapping) else {}
        methods = set(problem.get("methods", []))
        active = bool(intersection) or any("intersection" in method or "maihda" in method for method in methods)
        if not active:
            return []
        findings: list[ReviewFinding] = []
        if not present(intersection.get("theory")):
            findings.append(
                self._f(
                    "SB-INTX-001",
                    "Intersectional categories lack a substantive theory",
                    "intersectionality",
                    "major",
                    "consensus_requirement",
                    rationale="Intersectionality is not equivalent to adding many demographic interactions. Categories represent social relations and structures whose meaning must be specified.",
                    repair="Explain why the selected dimensions and intersections matter in this setting, how they are produced, and which mechanisms or institutions are implicated.",
                    location="intersectionality.theory",
                )
            )
        if not present(intersection.get("task")):
            findings.append(
                self._f(
                    "SB-INTX-002",
                    "Descriptive, predictive, and causal intersectional aims are conflated",
                    "intersectionality",
                    "critical",
                    "consensus_requirement",
                    rationale="Describing outcome heterogeneity, measuring discriminatory accuracy, estimating effect modification, and estimating joint causal effects require different targets and assumptions.",
                    repair="Classify the intersectional task and define its estimand independently of the model.",
                    location="intersectionality.task",
                )
            )
        strata = first_present(manifest, "intersectionality.strata_counts", default=[])
        if not present(strata):
            findings.append(
                self._f(
                    "SB-INTX-003",
                    "Support within intersectional strata is not reported",
                    "intersectionality",
                    "major",
                    "consensus_requirement",
                    rationale="Sparse intersections can produce unstable estimates, disclosure risk, and practical positivity violations.",
                    repair="Report counts, outcome events, missingness, weights, and effective sample sizes by intersection, then use partial pooling or redefine the target where needed.",
                    location="intersectionality.strata_counts",
                )
            )
        if intersection.get("separate_stratified_models") is True and not present(intersection.get("partial_pooling")):
            findings.append(
                self._f(
                    "SB-INTX-004",
                    "Many separate stratum models are fit without shrinkage or multiplicity control",
                    "intersectionality",
                    "major",
                    "context_dependent",
                    rationale="Independent estimates in sparse cells can be noisy and invite post hoc ranking. Multilevel models can stabilize description, but their shrinkage target and assumptions must be reported.",
                    repair="Use a prespecified interaction model, hierarchical partial pooling, or a transparent multiplicity strategy and report uncertainty for contrasts.",
                    location="intersectionality.partial_pooling",
                    source_ids=["MAIHDA_TUTORIAL_2024"],
                )
            )
        if present(intersection.get("interaction")) and not present(intersection.get("interaction_scale")):
            findings.append(
                self._f(
                    "SB-INTX-005",
                    "Interaction scale is unspecified",
                    "intersectionality",
                    "critical",
                    "consensus_requirement",
                    rationale="Additive and multiplicative interactions answer different questions and can lead to different conclusions.",
                    repair="Define the scale, contrast, reference, and uncertainty for every interaction claim. For public health impact, consider additive contrasts explicitly.",
                    location="intersectionality.interaction_scale",
                )
            )
        if problem.get("task") == "causal_effect" and not present(intersection.get("intervention_mapping")):
            findings.append(
                self._f(
                    "SB-INTX-006",
                    "Intersectional causal contrast lacks an intervention mapping",
                    "intersectional_causal_inference",
                    "critical",
                    "active_debate",
                    rationale="Social identities are not simple manipulable treatments. Causal questions should usually target modifiable policies, mechanisms, or discriminatory processes while preserving the structural meaning of the categories.",
                    repair="Define the intervention or decomposition target, clarify what is and is not being intervened on, and state the assumptions required for interpretation.",
                    location="intersectionality.intervention_mapping",
                )
            )
        if intersection.get("individual_deficit_interpretation") is True:
            findings.append(
                self._f(
                    "SB-INTX-007",
                    "Structural disparity is interpreted as an individual group deficit",
                    "intersectionality",
                    "critical",
                    "known_error",
                    rationale="Group labels can proxy exposure to institutions, resources, discrimination, history, and place. They are not biological explanations by default.",
                    repair="Interpret contrasts in relation to measured and unmeasured structures, avoid essentialist language, and identify actionable mechanisms.",
                    location="intersectionality.interpretation",
                )
            )
        return findings

    def _survival_longitudinal(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        methods = set(problem.get("methods", []))
        families = {self.methods[m].get("family") for m in methods if m in self.methods}
        data_structures = set(problem.get("data_structures", []))
        active = "survival_event_history_and_longitudinal" in families or bool(data_structures & {"time_to_event", "longitudinal", "panel", "recurrent_events"})
        if not active:
            return []
        manifest = context.manifest
        time = manifest.get("time", {}) if isinstance(manifest.get("time"), Mapping) else {}
        findings: list[ReviewFinding] = []
        required = [
            ("SB-TIME-001", "Time origin is undefined", time.get("origin") or problem.get("time_zero"), "time.origin", "critical"),
            ("SB-TIME-002", "Event definition is incomplete", time.get("event"), "time.event", "critical"),
            ("SB-TIME-003", "Censoring process is unspecified", time.get("censoring"), "time.censoring", "critical"),
            ("SB-TIME-004", "Competing events are not addressed", time.get("competing_events"), "time.competing_events", "major"),
        ]
        for rule_id, title, value, path, severity in required:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "time_to_event",
                        severity,
                        "consensus_requirement",
                        rationale="Risk sets, censoring, and effect measures are defined relative to time origin and event processes.",
                        repair=f"Define {path} and connect it to the estimand and estimator.",
                        location=path,
                    )
                )
        if "cox_proportional_hazards" in methods and not present(time.get("proportional_hazards")):
            findings.append(
                self._f(
                    "SB-TIME-005",
                    "Proportional hazards are not assessed",
                    "time_to_event",
                    "major",
                    "consensus_requirement",
                    rationale="With nonproportional hazards, a single Cox coefficient is a weighting-dependent summary and may not represent a stable treatment effect.",
                    repair="Assess proportional hazards, show time-varying effects, and report absolute risks or alternative estimands at clinically relevant horizons.",
                    location="time.proportional_hazards",
                )
            )
        if get_path(manifest, "reporting.hazard_ratio_called_risk_ratio") is True:
            findings.append(
                self._f(
                    "SB-TIME-006",
                    "Hazard ratio is interpreted as a risk ratio",
                    "time_to_event",
                    "critical",
                    "known_error",
                    rationale="Hazards are instantaneous rates conditional on survival. A hazard ratio is not a cumulative risk ratio or a constant relative risk.",
                    repair="Use hazard language and report cumulative incidence, survival probability, risk difference, or restricted mean survival time when those are the scientific targets.",
                    location="reporting.primary_claims",
                )
            )
        if time.get("immortal_time_possible") is True and not present(time.get("immortal_time_strategy")):
            findings.append(
                self._f(
                    "SB-TIME-007",
                    "Immortal time is not controlled",
                    "time_to_event",
                    "fatal",
                    "known_error",
                    rationale="Exposure classification that requires future survival assigns guaranteed event-free time to an exposure group.",
                    repair="Align eligibility, exposure assignment, and follow-up or use a valid time-varying or target-trial strategy.",
                    location="time.immortal_time_strategy",
                )
            )
        if time.get("recurrent_events") is True and not present(time.get("terminal_event_strategy")):
            findings.append(
                self._f(
                    "SB-TIME-008",
                    "Recurrent events are analysed without accounting for terminal events",
                    "recurrent_events",
                    "major",
                    "context_dependent",
                    rationale="Death or another terminal event can inform the recurrent-event process and alter the interpretation of event rates.",
                    repair="Define the recurrent-event estimand and evaluate joint frailty, multi-state, while-alive, or other methods appropriate to the scientific question.",
                    location="time.terminal_event_strategy",
                )
            )
        return findings

    def _missing_data(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        manifest = context.manifest
        missing = first_present(manifest, "analysis.missing_data", "missing_data", default={})
        missing = missing if isinstance(missing, Mapping) else {"method": missing}
        findings: list[ReviewFinding] = []
        if not present(missing):
            return [
                self._f(
                    "SB-MISS-001",
                    "Missing-data process is not described",
                    "missing_data",
                    "major",
                    "consensus_requirement",
                    rationale="Missingness can change the target population and bias estimates even when the missing fraction is modest.",
                    repair="Report missingness by variable, analysis group, time, and outcome status, then define the assumptions and method used.",
                    location="analysis.missing_data",
                    source_ids=["TARMOS"],
                )
            ]
        if not present(missing.get("amounts")):
            findings.append(
                self._f(
                    "SB-MISS-002",
                    "Variable-level missingness is not quantified",
                    "missing_data",
                    "major",
                    "consensus_requirement",
                    rationale="A single complete-case percentage conceals which variables and groups drive selection.",
                    repair="Provide counts and percentages for each variable, time point, group, and analysis denominator.",
                    location="analysis.missing_data.amounts",
                )
            )
        if not present(missing.get("assumptions")):
            findings.append(
                self._f(
                    "SB-MISS-003",
                    "Missingness assumptions are unstated",
                    "missing_data",
                    "critical",
                    "consensus_requirement",
                    rationale="Complete-case analysis, likelihood, weighting, and imputation each rely on assumptions about selection and data generation.",
                    repair="State the assumption required for the target estimate and justify it using temporal and substantive knowledge.",
                    location="analysis.missing_data.assumptions",
                )
            )
        method = normalize_key(str(missing.get("method", "")))
        if method in {"complete_case", "complete_case_analysis"} and not present(missing.get("complete_case_justification")):
            findings.append(
                self._f(
                    "SB-MISS-004",
                    "Complete-case analysis lacks a target-specific justification",
                    "missing_data",
                    "critical",
                    "context_dependent",
                    rationale="Complete-case analysis is unbiased only under conditions that depend on the model and missingness process, not simply when missingness is below a percentage threshold.",
                    repair="State the conditions under which complete-case analysis identifies the target and compare with a principled alternative.",
                    location="analysis.missing_data.complete_case_justification",
                )
            )
        if "imputation" in method or missing.get("multiple_imputation") is True:
            mi_required = [
                ("SB-MISS-005", "Number of imputations is not reported", missing.get("m"), "analysis.missing_data.m"),
                ("SB-MISS-006", "Imputation model variables are not reported", missing.get("imputation_variables"), "analysis.missing_data.imputation_variables"),
                ("SB-MISS-007", "Imputation and analysis models are not shown to be compatible", missing.get("compatibility"), "analysis.missing_data.compatibility"),
                ("SB-MISS-008", "Pooling procedure is not reported", missing.get("pooling"), "analysis.missing_data.pooling"),
            ]
            for rule_id, title, value, path in mi_required:
                if not present(value):
                    findings.append(
                        self._f(
                            rule_id,
                            title,
                            "missing_data",
                            "major",
                            "consensus_requirement",
                            rationale="Multiple imputation requires transparent construction and pooling to preserve relations, uncertainty, and the analysis design.",
                            repair=f"Report {path}, including interactions, nonlinear terms, clustering, survey design, and outcome information where relevant.",
                            location=path,
                            source_ids=["MI_RUBIN_1987", "TARMOS"],
                        )
                    )
        if not present(missing.get("sensitivity")) and missing.get("material") is not False:
            findings.append(
                self._f(
                    "SB-MISS-009",
                    "No sensitivity analysis addresses departures from the primary missingness assumption",
                    "missing_data",
                    "major",
                    "consensus_requirement",
                    rationale="MAR and related assumptions are not verified by observed data alone.",
                    repair="Use delta adjustment, pattern mixture, selection, tipping-point, or another analysis tied to plausible departures.",
                    location="analysis.missing_data.sensitivity",
                )
            )
        return findings

    def _bayesian(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        methods = set(problem.get("methods", []))
        families = {self.methods[m].get("family") for m in methods if m in self.methods}
        if "bayesian_methods" not in families and not get_path(context.manifest, "analysis.bayesian"):
            return []
        bayes = first_present(context.manifest, "analysis.bayesian", "bayesian", default={})
        bayes = bayes if isinstance(bayes, Mapping) else {}
        findings: list[ReviewFinding] = []
        required = [
            ("SB-BAYES-001", "Likelihood is not specified", bayes.get("likelihood"), "analysis.bayesian.likelihood", "critical"),
            ("SB-BAYES-002", "Prior distributions are not specified", bayes.get("priors"), "analysis.bayesian.priors", "critical"),
            ("SB-BAYES-003", "Posterior target is not specified", bayes.get("posterior_target"), "analysis.bayesian.posterior_target", "critical"),
            ("SB-BAYES-004", "Convergence diagnostics are not reported", bayes.get("convergence"), "analysis.bayesian.convergence", "critical"),
            ("SB-BAYES-005", "Posterior predictive assessment is missing", bayes.get("posterior_predictive"), "analysis.bayesian.posterior_predictive", "major"),
            ("SB-BAYES-006", "Prior sensitivity is missing", bayes.get("prior_sensitivity"), "analysis.bayesian.prior_sensitivity", "major"),
        ]
        for rule_id, title, value, path, severity in required:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "bayesian_inference",
                        severity,
                        "consensus_requirement",
                        rationale="Bayesian results are conditional on the full probability model and computation, not only the posterior summary.",
                        repair=f"Report {path} with enough detail to reproduce the posterior.",
                        location=path,
                    )
                )
        convergence = bayes.get("convergence", {}) if isinstance(bayes.get("convergence"), Mapping) else {}
        if finite_number(convergence.get("max_rhat")) and float(convergence["max_rhat"]) > 1.01:
            findings.append(
                self._f(
                    "SB-BAYES-007",
                    "Potential scale reduction indicates incomplete convergence",
                    "bayesian_inference",
                    "critical",
                    "known_error",
                    observed=convergence.get("max_rhat"),
                    expected="R-hat close to 1, evaluated with effective sample size and trace diagnostics",
                    rationale="Unmixed chains can yield unreliable posterior summaries.",
                    repair="Improve parameterization, sampling, warmup, or model specification and rerun until convergence is satisfactory.",
                    location="analysis.bayesian.convergence.max_rhat",
                )
            )
        if finite_number(convergence.get("divergences")) and float(convergence["divergences"]) > 0:
            findings.append(
                self._f(
                    "SB-BAYES-008",
                    "Hamiltonian Monte Carlo divergences remain",
                    "bayesian_inference",
                    "critical",
                    "known_error",
                    observed=convergence.get("divergences"),
                    expected=0,
                    rationale="Divergences can indicate that the sampler failed to explore important posterior geometry.",
                    repair="Reparameterize, inspect posterior geometry, strengthen or revise priors, and do not rely on a higher adapt_delta alone without diagnosis.",
                    location="analysis.bayesian.convergence.divergences",
                )
            )
        if get_path(context.manifest, "reporting.credible_interval_called_confidence_interval") is True:
            findings.append(
                self._f(
                    "SB-BAYES-009",
                    "Bayesian credible interval is described as a frequentist confidence interval",
                    "bayesian_inference",
                    "major",
                    "known_error",
                    rationale="The two intervals have different probability statements and depend on different inferential frameworks.",
                    repair="Use posterior probability language and state the prior and model conditioning the interval.",
                    location="reporting.primary_claims",
                )
            )
        return findings

    def _classical_models(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        methods = set(problem.get("methods", []))
        findings: list[ReviewFinding] = []
        analysis = context.manifest.get("analysis", {}) if isinstance(context.manifest.get("analysis"), Mapping) else {}
        if "logistic_regression" in methods:
            if get_path(context.manifest, "reporting.odds_ratio_called_risk_ratio") is True:
                findings.append(
                    self._f(
                        "SB-REG-001",
                        "Odds ratio is interpreted as a risk ratio",
                        "regression",
                        "critical",
                        "known_error",
                        rationale="Odds and risks differ, sometimes substantially, and adjusted odds ratios are noncollapsible.",
                        repair="Use odds-ratio language or standardize predicted probabilities to report marginal risks, risk ratios, or risk differences.",
                        location="reporting.primary_claims",
                    )
                )
            if not present(first_present(context.manifest, "analysis.diagnostics.separation", "analysis.separation")):
                findings.append(
                    self._f(
                        "SB-REG-002",
                        "Logistic separation is not assessed",
                        "regression",
                        "major",
                        "consensus_requirement",
                        rationale="Complete or quasi-complete separation can produce infinite or unstable maximum-likelihood estimates.",
                        repair="Check separation and use penalized or Bayesian methods when it occurs.",
                        location="analysis.diagnostics.separation",
                    )
                )
        continuous_covariates = as_list(analysis.get("continuous_covariates"))
        if continuous_covariates and not present(analysis.get("functional_form")):
            findings.append(
                self._f(
                    "SB-REG-003",
                    "Continuous covariate functional forms are not evaluated",
                    "regression",
                    "major",
                    "consensus_requirement",
                    observed=continuous_covariates,
                    expected="Flexible prespecified functions or diagnostics on the model scale",
                    rationale="Assuming linearity on the model scale can bias effects and predictions, while arbitrary categorization loses information.",
                    repair="Use splines, fractional polynomials, transformations, or substantive functions and report the selected form without data-driven cutpoints.",
                    location="analysis.functional_form",
                )
            )
        if analysis.get("robust_standard_errors_as_bias_fix") is True:
            findings.append(
                self._f(
                    "SB-REG-004",
                    "Robust standard errors are treated as a remedy for model bias",
                    "regression",
                    "critical",
                    "known_error",
                    rationale="Sandwich variance estimators can alter standard errors under some misspecification, but they do not correct confounding, wrong functional form, selection, measurement error, or a mismatched estimand.",
                    repair="Diagnose and address the source of bias separately, then use an uncertainty estimator appropriate to the design.",
                    location="analysis.uncertainty",
                )
            )
        if analysis.get("stepwise_selection") is True:
            findings.append(
                self._f(
                    "SB-REG-005",
                    "Stepwise variable selection is used for inferential claims",
                    "regression",
                    "major",
                    "known_error",
                    rationale="Stepwise procedures create unstable models, biased coefficients, invalid standard errors, and hidden multiplicity.",
                    repair="Prespecify confounders using substantive knowledge, use shrinkage for prediction, or account for model-selection uncertainty.",
                    location="analysis.stepwise_selection",
                )
            )
        if analysis.get("change_in_estimate_confounder_selection") is True:
            findings.append(
                self._f(
                    "SB-REG-006",
                    "Confounders are selected by a change-in-estimate threshold",
                    "causal_identification",
                    "major",
                    "known_error",
                    rationale="Data-dependent coefficient change does not identify confounders and can select colliders, mediators, or noisy proxies.",
                    repair="Use a causal model and temporal knowledge to define the adjustment set before outcome modelling.",
                    location="analysis.covariate_selection",
                )
            )
        return findings

    def _trials(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        design = str(problem.get("design") or "")
        if "trial" not in design or design == "target_trial_emulation":
            return []
        trial = context.manifest.get("trial", {}) if isinstance(context.manifest.get("trial"), Mapping) else {}
        findings: list[ReviewFinding] = []
        required = [
            ("SB-TRIAL-001", "Randomization unit and method are not reported", trial.get("randomization"), "trial.randomization", "critical"),
            ("SB-TRIAL-002", "Allocation concealment is not reported", trial.get("allocation_concealment"), "trial.allocation_concealment", "critical"),
            ("SB-TRIAL-003", "Trial estimand and intercurrent-event strategies are incomplete", context.manifest.get("estimand"), "estimand", "critical"),
            ("SB-TRIAL-004", "Sample-size assumptions are not reported", trial.get("sample_size"), "trial.sample_size", "major"),
            ("SB-TRIAL-005", "Multiplicity strategy is not reported", trial.get("multiplicity"), "trial.multiplicity", "major"),
        ]
        for rule_id, title, value, path, severity in required:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "randomized_trials",
                        severity,
                        "consensus_requirement",
                        rationale="Randomization protects inference only when assignment, analysis population, outcomes, intercurrent events, and multiplicity are handled coherently.",
                        repair=f"Report {path} and align it with the primary estimand and analysis.",
                        location=path,
                        source_ids=["CONSORT_2025", "ICH_E9_R1"],
                    )
                )
        if trial.get("post_randomization_exclusions") is True and not present(trial.get("estimand_justification")):
            findings.append(
                self._f(
                    "SB-TRIAL-006",
                    "Post-randomization exclusions threaten the randomized comparison",
                    "randomized_trials",
                    "critical",
                    "known_error",
                    rationale="Excluding participants based on post-assignment events can break exchangeability and change the estimand.",
                    repair="Analyse according to the prespecified estimand, account for intercurrent events explicitly, and report all exclusions by arm.",
                    location="trial.post_randomization_exclusions",
                )
            )
        if trial.get("noninferiority") is True and not present(trial.get("noninferiority_margin_rationale")):
            findings.append(
                self._f(
                    "SB-TRIAL-007",
                    "Noninferiority margin lacks clinical and historical justification",
                    "randomized_trials",
                    "critical",
                    "consensus_requirement",
                    rationale="A noninferiority conclusion depends directly on the margin and assay-sensitivity assumptions.",
                    repair="Justify the margin using preserved effect and clinical acceptability, and report both intention-to-treat and per-protocol analyses with their estimands.",
                    location="trial.noninferiority_margin_rationale",
                )
            )
        return findings

    def _meta_analysis(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        design = str(problem.get("design") or "")
        methods = set(problem.get("methods", []))
        families = {self.methods[m].get("family") for m in methods if m in self.methods}
        if "meta" not in design and "evidence_synthesis_meta_analysis_and_decision" not in families:
            return []
        meta = context.manifest.get("meta_analysis", {}) if isinstance(context.manifest.get("meta_analysis"), Mapping) else {}
        findings: list[ReviewFinding] = []
        required = [
            ("SB-META-001", "Synthesis estimand and effect scale are undefined", meta.get("effect_measure"), "meta_analysis.effect_measure", "critical"),
            ("SB-META-002", "Dependence among effects is not addressed", meta.get("dependence"), "meta_analysis.dependence", "critical"),
            ("SB-META-003", "Clinical and methodological heterogeneity are not characterized", meta.get("heterogeneity"), "meta_analysis.heterogeneity", "major"),
            ("SB-META-004", "Risk of bias is not integrated into synthesis", meta.get("risk_of_bias"), "meta_analysis.risk_of_bias", "critical"),
        ]
        for rule_id, title, value, path, severity in required:
            if not present(value):
                findings.append(
                    self._f(
                        rule_id,
                        title,
                        "meta_analysis",
                        severity,
                        "consensus_requirement",
                        rationale="A pooled number is interpretable only for compatible targets, effect measures, dependencies, and study biases.",
                        repair=f"Define and report {path} before interpreting the pooled estimate.",
                        location=path,
                        source_ids=["PRISMA_2020"],
                    )
                )
        if meta.get("random_effects") is True and not present(meta.get("prediction_interval")):
            findings.append(
                self._f(
                    "SB-META-005",
                    "Random-effects synthesis omits a prediction interval",
                    "meta_analysis",
                    "major",
                    "context_dependent",
                    rationale="The confidence interval for the mean effect does not show the range of effects expected in a new setting.",
                    repair="Report a prediction interval when the number and distribution of studies support it, and interpret it alongside tau-squared and context.",
                    location="meta_analysis.prediction_interval",
                )
            )
        if meta.get("publication_bias_test_proves_absence") is True:
            findings.append(
                self._f(
                    "SB-META-006",
                    "A funnel-plot or regression test is treated as proof that publication bias is absent",
                    "meta_analysis",
                    "critical",
                    "known_error",
                    rationale="Small-study-effect tests have limited power and alternative explanations. A nonsignificant test does not establish absence of missing evidence.",
                    repair="Describe the test as one diagnostic, examine protocols and registries, and assess robustness to plausible missing evidence.",
                    location="meta_analysis.publication_bias",
                )
            )
        if meta.get("meta_regression_individual_interpretation") is True:
            findings.append(
                self._f(
                    "SB-META-007",
                    "Study-level meta-regression is interpreted as an individual-level effect modifier",
                    "meta_analysis",
                    "critical",
                    "known_error",
                    rationale="Across-study associations can differ from within-study individual interactions and are vulnerable to ecological bias.",
                    repair="Restrict interpretation to study-level heterogeneity or use individual-participant data with a valid interaction estimand.",
                    location="meta_analysis.meta_regression",
                )
            )
        return findings

    def _spatial_network(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        methods = set(problem.get("methods", []))
        families = {self.methods[m].get("family") for m in methods if m in self.methods}
        spatial = context.manifest.get("spatial", {}) if isinstance(context.manifest.get("spatial"), Mapping) else {}
        network = context.manifest.get("network", {}) if isinstance(context.manifest.get("network"), Mapping) else {}
        if "spatial_spatiotemporal_network_and_ecological" not in families and not spatial and not network:
            return []
        findings: list[ReviewFinding] = []
        if spatial:
            required = [
                ("SB-SPAT-001", "Spatial support is undefined", spatial.get("support"), "spatial.support"),
                ("SB-SPAT-002", "Spatial dependence is not assessed", spatial.get("dependence"), "spatial.dependence"),
                ("SB-SPAT-003", "Scale and boundary sensitivity are not assessed", spatial.get("scale_sensitivity"), "spatial.scale_sensitivity"),
                ("SB-SPAT-004", "Location uncertainty is not propagated", spatial.get("location_uncertainty"), "spatial.location_uncertainty"),
            ]
            for rule_id, title, value, path in required:
                if not present(value):
                    findings.append(
                        self._f(
                            rule_id,
                            title,
                            "spatial_statistics",
                            "major",
                            "consensus_requirement",
                            rationale="Spatial results depend on geographic support, neighborhood definition, boundaries, alignment, and dependence.",
                            repair=f"Define and examine {path} using sensitivity analyses appropriate to the data source.",
                            location=path,
                        )
                    )
            if spatial.get("area_result_interpreted_individually") is True:
                findings.append(
                    self._f(
                        "SB-SPAT-005",
                        "Area-level association is interpreted as an individual-level effect",
                        "spatial_statistics",
                        "critical",
                        "known_error",
                        rationale="Relationships between area aggregates need not hold for individuals within those areas.",
                        repair="Restrict the claim to the area level or use linked multilevel data and an estimand matching the intended level.",
                        location="spatial.interpretation",
                    )
                )
        if network or get_path(context.manifest, "causal.interference") not in {None, False, "none"}:
            if not present(network.get("exposure_mapping")):
                findings.append(
                    self._f(
                        "SB-NET-001",
                        "Interference exposure mapping is undefined",
                        "network_causal_inference",
                        "critical",
                        "consensus_requirement",
                        rationale="With interference, potential outcomes depend on own treatment and some function of others' treatments. The mapping is part of the estimand.",
                        repair="Define neighborhoods, exposure mapping, direct and spillover contrasts, and partial-interference or network assumptions.",
                        location="network.exposure_mapping",
                        source_ids=["HUDGENS_HALLORAN_2008"],
                    )
                )
        return findings

    def _numeric_checks(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        effects = as_list(context.artifacts.get("reported_effects"))
        for index, effect in enumerate(effects):
            if not isinstance(effect, Mapping):
                continue
            location = f"artifacts.reported_effects[{index}]"
            estimate = effect.get("estimate")
            lower = effect.get("lower")
            upper = effect.get("upper")
            p_value = effect.get("p_value")
            null = effect.get("null_value", 1.0 if effect.get("scale") in {"ratio", "log_ratio", "odds_ratio", "risk_ratio", "hazard_ratio"} else 0.0)
            if finite_number(lower) and finite_number(upper) and float(lower) > float(upper):
                findings.append(
                    self._f(
                        "SB-NUM-001",
                        "Confidence interval bounds are reversed",
                        "numeric_consistency",
                        "fatal",
                        "known_error",
                        observed={"lower": lower, "upper": upper},
                        expected="lower <= upper",
                        rationale="The reported interval is numerically invalid.",
                        repair="Recompute the interval from the analysis object and trace the value through every table and manuscript location.",
                        location=location,
                    )
                )
            if all(finite_number(value) for value in (estimate, lower, upper)) and not float(lower) <= float(estimate) <= float(upper):
                findings.append(
                    self._f(
                        "SB-NUM-002",
                        "Point estimate lies outside its reported interval",
                        "numeric_consistency",
                        "fatal",
                        "known_error",
                        observed={"estimate": estimate, "lower": lower, "upper": upper},
                        expected="lower <= estimate <= upper",
                        rationale="A conventional confidence or credible interval centered on this estimate should contain the estimate.",
                        repair="Verify scale transformations, exponentiation, copying, and interval construction.",
                        location=location,
                    )
                )
            if finite_number(p_value) and not 0 <= float(p_value) <= 1:
                findings.append(
                    self._f(
                        "SB-NUM-003",
                        "P value is outside the valid range",
                        "numeric_consistency",
                        "fatal",
                        "known_error",
                        observed=p_value,
                        expected="0 <= p <= 1",
                        rationale="A probability cannot lie outside zero and one.",
                        repair="Correct the reported value and inspect the export or formatting pipeline.",
                        location=location,
                    )
                )
            if all(finite_number(value) for value in (estimate, lower, upper, p_value, null)):
                log_scale = bool(effect.get("log_scale") or effect.get("scale") in {"ratio", "odds_ratio", "risk_ratio", "hazard_ratio"})
                check = StatisticalCalculators.confidence_p_consistency(
                    float(estimate), float(lower), float(upper), float(p_value), null_value=float(null), log_scale=log_scale
                )
                if not check.get("consistent"):
                    findings.append(
                        self._f(
                            "SB-NUM-004",
                            "Reported interval and P value are internally inconsistent",
                            "numeric_consistency",
                            "critical",
                            "known_error",
                            observed=check,
                            expected="Agreement up to rounding under the same two-sided Wald procedure",
                            rationale="Discordance can indicate copying errors, mismatched models, one-sided versus two-sided testing, transformation errors, or a non-Wald interval.",
                            repair="State the exact test and interval procedure. If they are intended to differ, explain why; otherwise regenerate both from the same model object.",
                            location=location,
                        )
                    )
        percentages = as_list(context.artifacts.get("percentages"))
        for index, item in enumerate(percentages):
            if not isinstance(item, Mapping):
                continue
            if all(finite_number(item.get(key)) for key in ("count", "denominator", "reported_percent")):
                expected = StatisticalCalculators.percentage(float(item["count"]), float(item["denominator"]))
                tolerance = float(item.get("tolerance", 0.15))
                if abs(expected - float(item["reported_percent"])) > tolerance:
                    findings.append(
                        self._f(
                            "SB-NUM-005",
                            "Reported percentage does not match its numerator and denominator",
                            "numeric_consistency",
                            "critical",
                            "known_error",
                            observed=dict(item),
                            expected=expected,
                            rationale="The discrepancy exceeds ordinary rounding.",
                            repair="Recompute the percentage and confirm that the denominator corresponds to the displayed row and missing-data rule.",
                            location=f"artifacts.percentages[{index}]",
                        )
                    )
        weights = context.artifacts.get("survey_weights")
        if isinstance(weights, Sequence) and not isinstance(weights, (str, bytes)) and weights:
            try:
                ess = StatisticalCalculators.kish_effective_sample_size([float(value) for value in weights])
                n = len(weights)
                if ess < 0.25 * n:
                    findings.append(
                        self._f(
                            "SB-NUM-006",
                            "Highly variable weights sharply reduce effective sample size",
                            "complex_survey",
                            "major",
                            "consensus_requirement",
                            observed={"n": n, "kish_effective_n": ess, "fraction": ess / n},
                            expected="Weight diagnostics and sensitivity to stabilization or trimming",
                            rationale="Extreme weights can dominate estimates, inflate variance, and reveal limited support.",
                            repair="Inspect the source of extreme weights, report the full distribution, justify any trimming, and show sensitivity of balance and estimates.",
                            location="artifacts.survey_weights",
                        )
                    )
            except ValueError:
                findings.append(
                    self._f(
                        "SB-NUM-007",
                        "Survey weights contain invalid values",
                        "complex_survey",
                        "fatal",
                        "known_error",
                        rationale="Weights must be finite and nonnegative for the implemented diagnostics.",
                        repair="Correct missing, negative, or infinite weights and verify their construction.",
                        location="artifacts.survey_weights",
                    )
                )
        return findings

    def _text_claim_checks(self, context: ReviewContext, problem: dict[str, Any]) -> list[ReviewFinding]:
        text = context.manuscript_text
        if not text:
            return []
        lowered = text.casefold()
        findings: list[ReviewFinding] = []
        if any(word in lowered for word in CAUSAL_WORDS) and problem.get("task") != "causal_effect":
            excerpt = self._excerpt(text, CAUSAL_WORDS)
            findings.append(
                self._f(
                    "SB-CLAIM-001",
                    "Causal language exceeds the declared task",
                    "interpretation",
                    "critical",
                    "known_error",
                    observed=problem.get("task"),
                    expected="Associational or descriptive language unless a causal estimand is identified",
                    rationale="Causal verbs imply a counterfactual intervention contrast that cannot be established by association alone.",
                    repair="Use association language or supply a complete causal design, estimand, assumptions, estimator, diagnostics, and sensitivity analysis.",
                    excerpt=excerpt,
                    location="manuscript",
                    confidence="medium",
                )
            )
        if any(word in lowered for word in NULL_WORDS) and re.search(r"p\s*[=>]\s*0?[.]0?5", lowered):
            findings.append(
                self._f(
                    "SB-CLAIM-002",
                    "A nonsignificant result is interpreted as evidence of no effect",
                    "interpretation",
                    "critical",
                    "known_error",
                    rationale="Failure to reject a point null does not establish equivalence or exclude meaningful effects.",
                    repair="Report the estimate and interval relative to a prespecified meaningful-effect range, or use an equivalence design.",
                    excerpt=self._excerpt(text, NULL_WORDS),
                    location="manuscript",
                )
            )
        if any(word in lowered for word in P_VALUE_PROOF_WORDS):
            findings.append(
                self._f(
                    "SB-CLAIM-003",
                    "Statistical significance is treated as proof",
                    "interpretation",
                    "major",
                    "known_error",
                    rationale="A P value does not measure effect size, practical importance, absence of bias, or the probability that a hypothesis is true.",
                    repair="Lead with the estimate, uncertainty, design limitations, and the strength of the identification argument.",
                    excerpt=self._excerpt(text, P_VALUE_PROOF_WORDS),
                    source_ids=["ASA_PVALUES_2016", "ASA_PVALUES_2019"],
                    location="manuscript",
                )
            )
        if "95% confidence" in lowered and "95% probability" in lowered:
            findings.append(
                self._f(
                    "SB-CLAIM-004",
                    "Frequentist confidence interval is given a posterior-probability interpretation",
                    "interpretation",
                    "major",
                    "known_error",
                    rationale="A realized frequentist interval is not, without a Bayesian model, a 95 percent probability statement about a fixed parameter.",
                    repair="Use a coverage or compatibility interpretation, or report a Bayesian credible interval under an explicit model and prior.",
                    location="manuscript",
                )
            )
        return findings

    def _interaction_findings(
        self,
        findings: list[ReviewFinding],
        context: ReviewContext,
        problem: dict[str, Any],
    ) -> list[ReviewFinding]:
        ids = {finding.rule_id for finding in findings}
        output: list[ReviewFinding] = []
        if {"SB-CAUSAL-003", "SB-TIME-007"} <= ids or {"SB-TTE-002", "SB-TIME-007"} <= ids:
            output.append(
                self._f(
                    "SB-INT-001",
                    "Time-zero defects jointly create a high-risk causal design failure",
                    "interaction",
                    "fatal",
                    "known_error",
                    rationale="Undefined or misaligned time zero combined with immortal-time risk can reverse or manufacture treatment effects.",
                    repair="Rebuild the cohort and treatment assignment from an explicit target trial protocol before fitting any outcome model.",
                    interaction_of=sorted(ids & {"SB-CAUSAL-003", "SB-TTE-002", "SB-TIME-007"}),
                    tags=["release_blocker"],
                )
            )
        if "SB-SURVEY-009" in ids and "SB-NUM-006" in ids:
            output.append(
                self._f(
                    "SB-INT-002",
                    "Ambiguous weight construction and low effective sample size threaten both target and precision",
                    "interaction",
                    "critical",
                    "consensus_requirement",
                    rationale="When sampling and treatment weights are not separated and the resulting weights are extreme, neither the population target nor the uncertainty is secure.",
                    repair="Reconstruct all weight components, define the target, examine support, and repeat the analysis under justified alternatives.",
                    interaction_of=["SB-SURVEY-009", "SB-NUM-006"],
                )
            )
        if "SB-PRED-009" in ids and "SB-PRED-010" in ids:
            output.append(
                self._f(
                    "SB-INT-003",
                    "Multiple information leaks invalidate the reported prediction performance",
                    "interaction",
                    "fatal",
                    "known_error",
                    rationale="Pre-split preprocessing and test-set reuse allow validation information to shape the model repeatedly.",
                    repair="Repeat the entire pipeline using nested resampling and a final untouched evaluation set.",
                    interaction_of=["SB-PRED-009", "SB-PRED-010"],
                )
            )
        if "SB-INTX-003" in ids and "SB-CAUSAL-007" in ids:
            output.append(
                self._f(
                    "SB-INT-004",
                    "Sparse intersectional strata create a practical positivity threat",
                    "interaction",
                    "critical",
                    "consensus_requirement",
                    rationale="Causal contrasts within sparsely supported intersections may require extrapolation even when the overall sample is large.",
                    repair="Inspect joint treatment support within every target stratum, redefine unsupported contrasts, and distinguish shrinkage for description from causal identification.",
                    interaction_of=["SB-INTX-003", "SB-CAUSAL-007"],
                )
            )
        return output

    @staticmethod
    def _excerpt(text: str, phrases: Sequence[str], radius: int = 120) -> str | None:
        lowered = text.casefold()
        positions = [(lowered.find(phrase.casefold()), phrase) for phrase in phrases]
        positions = [(position, phrase) for position, phrase in positions if position >= 0]
        if not positions:
            return None
        position, phrase = min(positions)
        start = max(0, position - radius)
        end = min(len(text), position + len(phrase) + radius)
        return text[start:end].strip()

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
