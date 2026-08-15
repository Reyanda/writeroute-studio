from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .knowledge import load_yaml
from .utils import as_list, first_present, normalize_key, present, unique_preserve


TASK_PATTERNS: dict[str, tuple[str, ...]] = {
    "causal_effect": (
        "causal effect", "effect of", "impact of", "would have", "counterfactual",
        "target trial", "intervention", "caused", "causal inference",
    ),
    "prediction": (
        "predict", "prediction", "prognostic", "diagnostic model", "risk model",
        "classifier", "calibration", "area under the curve", "auroc",
    ),
    "description": (
        "prevalence", "incidence", "distribution", "descriptive", "burden",
        "population mean", "population proportion", "trend",
    ),
    "association": (
        "associated with", "association between", "correlat", "relationship between",
    ),
    "measurement": (
        "reliability", "validity", "measurement error", "agreement", "diagnostic accuracy",
    ),
    "decision": (
        "decision curve", "net benefit", "policy value", "cost effectiveness", "utility",
    ),
    "discovery": (
        "feature discovery", "cluster discovery", "latent class", "unsupervised",
    ),
}

DESIGN_ALIASES: dict[str, tuple[str, ...]] = {
    "randomized_parallel_trial": ("randomized controlled trial", "randomised controlled trial", "parallel trial"),
    "cluster_randomized_trial": ("cluster randomized", "cluster randomised"),
    "cross_sectional": ("cross-sectional", "cross sectional"),
    "repeated_cross_sectional": ("repeated cross-sectional", "serial cross-sectional"),
    "prospective_cohort": ("prospective cohort",),
    "retrospective_cohort": ("retrospective cohort",),
    "case_control": ("case-control", "case control"),
    "nested_case_control": ("nested case-control",),
    "case_cohort": ("case-cohort",),
    "target_trial_emulation": ("target trial emulation", "emulated target trial"),
    "difference_in_differences": ("difference-in-differences", "difference in differences"),
    "regression_discontinuity": ("regression discontinuity",),
    "instrumental_variable_study": ("instrumental variable", "mendelian randomization", "mendelian randomisation"),
    "interrupted_time_series": ("interrupted time series",),
    "diagnostic_accuracy": ("diagnostic accuracy",),
    "systematic_review_meta_analysis": ("systematic review", "meta-analysis", "meta analysis"),
    "complex_household_survey": ("demographic and health survey", "dhs survey", "complex household survey"),
}


@dataclass(slots=True)
class Inference:
    value: Any
    confidence: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "confidence": self.confidence, "source": self.source}


class ProblemReconstructor:
    """Reconstruct the statistical problem before any method judgement."""

    def __init__(self) -> None:
        self.methods = load_yaml("method_registry").get("methods", {})
        self.designs = load_yaml("design_profiles").get("profiles", {})
        self.estimands = load_yaml("estimand_registry").get("estimands", {})
        self._method_aliases = self._build_method_aliases()

    def _build_method_aliases(self) -> list[tuple[str, str]]:
        aliases: list[tuple[str, str]] = []
        special = {
            "tmle": "tmle",
            "targeted maximum likelihood estimation": "tmle",
            "targeted minimum loss estimation": "tmle",
            "inverse probability weighting": "inverse_probability_weighting",
            "inverse probability of treatment weighting": "inverse_probability_weighting",
            "g-computation": "parametric_g_computation",
            "g computation": "parametric_g_computation",
            "g-formula": "g_formula",
            "super learner": "super_learner",
            "causal forest": "causal_forest",
            "random forest": "random_forest",
            "cox proportional hazards": "cox_proportional_hazards",
            "logistic regression": "logistic_regression",
            "linear regression": "linear_regression",
            "difference-in-differences": "difference_in_differences",
            "difference in differences": "difference_in_differences",
            "marginal structural model": "marginal_structural_model",
            "generalized propensity score": "generalized_propensity_score",
            "generalised propensity score": "generalized_propensity_score",
            "maihda": "maihda_descriptive",
        }
        for phrase, key in special.items():
            if key in self.methods:
                aliases.append((phrase, key))
        for key, profile in self.methods.items():
            name = str(profile.get("name", "")).strip().lower()
            if len(name) >= 12 and name not in {phrase for phrase, _ in aliases}:
                aliases.append((name, key))
        aliases.sort(key=lambda item: len(item[0]), reverse=True)
        return aliases

    def reconstruct(self, manifest: Mapping[str, Any], manuscript_text: str = "") -> dict[str, Any]:
        text = manuscript_text.casefold()
        task = self._task(manifest, text)
        design = self._design(manifest, text)
        methods = self._methods(manifest, text)
        estimand = self._estimand(manifest)
        target_population = first_present(
            manifest,
            "estimand.target_population",
            "study.target_population",
            "target_population",
        )
        outcome = first_present(manifest, "estimand.outcome", "question.outcome", "study.outcome", "outcome")
        exposure = first_present(
            manifest,
            "estimand.treatment_strategies",
            "question.intervention",
            "question.exposure",
            "study.exposure",
            "exposure",
        )
        comparator = first_present(manifest, "estimand.comparator", "question.comparator", "comparator")
        time_zero = first_present(manifest, "estimand.time_zero", "question.time_zero", "study.time_zero", "time_zero")
        horizon = first_present(
            manifest,
            "estimand.time_horizon",
            "question.time_horizon",
            "prediction.horizon",
            "follow_up",
        )
        sampling_target = first_present(manifest, "sampling.target_population", "study.sampling_target", "sampling_target")
        data_structures = unique_preserve(
            [normalize_key(str(item)) for item in as_list(first_present(manifest, "study.data_structure", "data_structure", default=[]))]
        )
        if not data_structures and design.value in self.designs:
            data_structures = as_list(self.designs[design.value].get("data_structures", []))
        return {
            "task": task.value,
            "task_inference": task.to_dict(),
            "design": design.value,
            "design_inference": design.to_dict(),
            "methods": methods.value,
            "methods_inference": methods.to_dict(),
            "estimand_id": estimand.value,
            "estimand_inference": estimand.to_dict(),
            "target_population": target_population,
            "sampling_target": sampling_target,
            "exposure_or_intervention": exposure,
            "comparator": comparator,
            "outcome": outcome,
            "time_zero": time_zero,
            "time_horizon": horizon,
            "data_structures": data_structures,
        }

    def _task(self, manifest: Mapping[str, Any], text: str) -> Inference:
        declared = first_present(manifest, "study.task", "question.task", "task")
        if present(declared):
            return Inference(normalize_key(str(declared)), "high", "manifest")
        scores = {task: sum(phrase in text for phrase in phrases) for task, phrases in TASK_PATTERNS.items()}
        winner = max(scores, key=scores.get) if scores else None
        if winner and scores[winner] > 0:
            confidence = "medium" if scores[winner] > 1 else "low"
            return Inference(winner, confidence, "manuscript_heuristic")
        return Inference(None, "none", "unresolved")

    def _design(self, manifest: Mapping[str, Any], text: str) -> Inference:
        declared = first_present(manifest, "study.design", "design")
        if present(declared):
            key = normalize_key(str(declared))
            if key in self.designs:
                return Inference(key, "high", "manifest")
            for candidate, profile in self.designs.items():
                if normalize_key(str(profile.get("name", ""))) == key:
                    return Inference(candidate, "high", "manifest_name")
            return Inference(key, "high", "manifest_unregistered")
        for key, aliases in DESIGN_ALIASES.items():
            if any(alias in text for alias in aliases):
                return Inference(key, "medium", "manuscript_heuristic")
        return Inference(None, "none", "unresolved")

    def _methods(self, manifest: Mapping[str, Any], text: str) -> Inference:
        declared = first_present(manifest, "analysis.methods", "methods", default=[])
        values = []
        for item in as_list(declared):
            if isinstance(item, Mapping):
                item = item.get("id") or item.get("name")
            if not present(item):
                continue
            key = normalize_key(str(item))
            if key in self.methods:
                values.append(key)
                continue
            matched = next(
                (
                    candidate
                    for candidate, profile in self.methods.items()
                    if normalize_key(str(profile.get("name", ""))) == key
                ),
                key,
            )
            values.append(matched)
        if values:
            return Inference(unique_preserve(values), "high", "manifest")
        detected: list[str] = []
        for phrase, key in self._method_aliases:
            if phrase in text:
                detected.append(key)
            if len(detected) >= 20:
                break
        if detected:
            return Inference(unique_preserve(detected), "low", "manuscript_heuristic")
        return Inference([], "none", "unresolved")

    def _estimand(self, manifest: Mapping[str, Any]) -> Inference:
        declared = first_present(manifest, "estimand.id", "estimand.name", "estimand_id")
        if present(declared):
            key = normalize_key(str(declared))
            if key in self.estimands:
                return Inference(key, "high", "manifest")
            for candidate, profile in self.estimands.items():
                if normalize_key(str(profile.get("name", ""))) == key:
                    return Inference(candidate, "high", "manifest_name")
            return Inference(key, "high", "manifest_unregistered")
        return Inference(None, "none", "unresolved")


class MethodRouter:
    def __init__(self) -> None:
        self.registry = load_yaml("method_registry")
        self.methods = self.registry.get("methods", {})
        self.families = self.registry.get("families", {})

    @staticmethod
    def coverage_level(profile: Mapping[str, Any]) -> str:
        enriched_fields = ("definition", "assumptions", "key_checks", "source_ids")
        if all(profile.get(field) for field in enriched_fields):
            return "enriched"
        if profile.get("definition") and (profile.get("key_checks") or profile.get("minimum_review")):
            return "method_specific_contract"
        if profile.get("minimum_review") and profile.get("common_hazards"):
            return "family_contract"
        return "routed"

    def profiles(self, method_ids: list[str]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for method_id in method_ids:
            profile = self.methods.get(method_id)
            if profile is None:
                output.append({"id": method_id, "registered": False, "coverage_level": "unregistered"})
                continue
            output.append(
                {
                    "id": method_id,
                    "registered": True,
                    "coverage_level": self.coverage_level(profile),
                    **profile,
                }
            )
        return output

    def families_for(self, method_ids: list[str]) -> list[str]:
        return unique_preserve(
            profile.get("family")
            for profile in self.profiles(method_ids)
            if profile.get("registered") and profile.get("family")
        )
