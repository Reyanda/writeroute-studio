from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
from scipy import stats


@dataclass(slots=True)
class EffectEstimate:
    measure: str
    estimate: float
    lower: float
    upper: float
    standard_error: float
    p_value: float
    null_value: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "measure": self.measure,
            "estimate": self.estimate,
            "lower": self.lower,
            "upper": self.upper,
            "standard_error": self.standard_error,
            "p_value": self.p_value,
            "null_value": self.null_value,
        }


class StatisticalCalculators:
    """Small deterministic calculations used for consistency review.

    These functions are not a replacement for a full analysis package. They are
    intentionally narrow, transparent, and suitable for independent checks.
    """

    @staticmethod
    def p_from_z(z_value: float, two_sided: bool = True) -> float:
        tail = stats.norm.sf(abs(float(z_value)))
        return float(2 * tail if two_sided else tail)

    @staticmethod
    def z_from_two_sided_p(p_value: float) -> float:
        p = float(p_value)
        if not 0 < p <= 1:
            raise ValueError("p_value must be in (0, 1]")
        return float(stats.norm.isf(p / 2))

    @staticmethod
    def ci_from_estimate_se(
        estimate: float,
        standard_error: float,
        confidence_level: float = 0.95,
        *,
        log_scale: bool = False,
    ) -> tuple[float, float]:
        alpha = 1 - confidence_level
        z = float(stats.norm.ppf(1 - alpha / 2))
        if log_scale:
            if estimate <= 0:
                raise ValueError("estimate must be positive on a log scale")
            center = math.log(estimate)
            return math.exp(center - z * standard_error), math.exp(center + z * standard_error)
        return estimate - z * standard_error, estimate + z * standard_error

    @staticmethod
    def se_from_ci(
        lower: float,
        upper: float,
        confidence_level: float = 0.95,
        *,
        log_scale: bool = False,
    ) -> float:
        if upper <= lower:
            raise ValueError("upper must exceed lower")
        alpha = 1 - confidence_level
        z = float(stats.norm.ppf(1 - alpha / 2))
        if log_scale:
            if lower <= 0:
                raise ValueError("lower must be positive on a log scale")
            return (math.log(upper) - math.log(lower)) / (2 * z)
        return (upper - lower) / (2 * z)

    @staticmethod
    def odds_ratio(a: int, b: int, c: int, d: int, confidence_level: float = 0.95) -> EffectEstimate:
        cells = [float(a), float(b), float(c), float(d)]
        if any(cell < 0 for cell in cells):
            raise ValueError("cell counts must be nonnegative")
        if any(cell == 0 for cell in cells):
            cells = [cell + 0.5 for cell in cells]
        a1, b1, c1, d1 = cells
        estimate = (a1 * d1) / (b1 * c1)
        se = math.sqrt(sum(1 / cell for cell in cells))
        lower, upper = StatisticalCalculators.ci_from_estimate_se(
            estimate, se, confidence_level, log_scale=True
        )
        z = math.log(estimate) / se
        return EffectEstimate("odds_ratio", estimate, lower, upper, se, StatisticalCalculators.p_from_z(z), 1.0)

    @staticmethod
    def risk_ratio(a: int, b: int, c: int, d: int, confidence_level: float = 0.95) -> EffectEstimate:
        cells = [float(a), float(b), float(c), float(d)]
        if any(cell < 0 for cell in cells):
            raise ValueError("cell counts must be nonnegative")
        if a + b <= 0 or c + d <= 0:
            raise ValueError("group totals must be positive")
        if a == 0 or c == 0:
            cells = [cell + 0.5 for cell in cells]
        a1, b1, c1, d1 = cells
        risk1 = a1 / (a1 + b1)
        risk0 = c1 / (c1 + d1)
        estimate = risk1 / risk0
        se = math.sqrt(1 / a1 - 1 / (a1 + b1) + 1 / c1 - 1 / (c1 + d1))
        lower, upper = StatisticalCalculators.ci_from_estimate_se(
            estimate, se, confidence_level, log_scale=True
        )
        z = math.log(estimate) / se
        return EffectEstimate("risk_ratio", estimate, lower, upper, se, StatisticalCalculators.p_from_z(z), 1.0)

    @staticmethod
    def risk_difference(a: int, b: int, c: int, d: int, confidence_level: float = 0.95) -> EffectEstimate:
        n1, n0 = a + b, c + d
        if n1 <= 0 or n0 <= 0:
            raise ValueError("group totals must be positive")
        p1, p0 = a / n1, c / n0
        estimate = p1 - p0
        se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        lower, upper = StatisticalCalculators.ci_from_estimate_se(estimate, se, confidence_level)
        z = estimate / se if se else math.inf
        return EffectEstimate("risk_difference", estimate, lower, upper, se, StatisticalCalculators.p_from_z(z), 0.0)

    @staticmethod
    def standardized_mean_difference(
        mean_treated: float,
        mean_control: float,
        sd_treated: float,
        sd_control: float,
        n_treated: int | None = None,
        n_control: int | None = None,
    ) -> float:
        if sd_treated < 0 or sd_control < 0:
            raise ValueError("standard deviations must be nonnegative")
        if n_treated and n_control and n_treated > 1 and n_control > 1:
            pooled = math.sqrt(
                ((n_treated - 1) * sd_treated**2 + (n_control - 1) * sd_control**2)
                / (n_treated + n_control - 2)
            )
        else:
            pooled = math.sqrt((sd_treated**2 + sd_control**2) / 2)
        if pooled == 0:
            return 0.0 if mean_treated == mean_control else math.inf
        return (mean_treated - mean_control) / pooled

    @staticmethod
    def kish_effective_sample_size(weights: Sequence[float]) -> float:
        array = np.asarray(weights, dtype=float)
        if array.size == 0 or np.any(~np.isfinite(array)) or np.any(array < 0):
            raise ValueError("weights must be finite, nonnegative, and nonempty")
        denominator = float(np.sum(array**2))
        if denominator == 0:
            raise ValueError("at least one weight must be positive")
        return float(np.sum(array) ** 2 / denominator)

    @staticmethod
    def weight_design_effect(weights: Sequence[float]) -> float:
        array = np.asarray(weights, dtype=float)
        mean = float(np.mean(array))
        if mean == 0:
            raise ValueError("mean weight must be positive")
        cv = float(np.std(array, ddof=1) / mean) if array.size > 1 else 0.0
        return 1 + cv**2

    @staticmethod
    def cluster_design_effect(mean_cluster_size: float, intraclass_correlation: float) -> float:
        if mean_cluster_size < 1:
            raise ValueError("mean_cluster_size must be at least 1")
        if not -1 <= intraclass_correlation <= 1:
            raise ValueError("intraclass_correlation must be between -1 and 1")
        return 1 + (mean_cluster_size - 1) * intraclass_correlation

    @staticmethod
    def e_value(risk_ratio: float, confidence_bound: float | None = None) -> dict[str, float]:
        rr = float(risk_ratio)
        if rr <= 0:
            raise ValueError("risk_ratio must be positive")
        rr_for_calc = rr if rr >= 1 else 1 / rr
        estimate_e = rr_for_calc + math.sqrt(rr_for_calc * (rr_for_calc - 1))
        output = {"estimate": float(estimate_e)}
        if confidence_bound is not None:
            bound = float(confidence_bound)
            if bound <= 0:
                raise ValueError("confidence_bound must be positive")
            bound_calc = bound if rr >= 1 else 1 / bound
            if (rr >= 1 and bound < 1) or (rr < 1 and bound > 1):
                output["confidence_bound"] = 1.0
            else:
                output["confidence_bound"] = float(bound_calc + math.sqrt(bound_calc * (bound_calc - 1)))
        return output

    @staticmethod
    def benjamini_hochberg(p_values: Iterable[float]) -> list[float]:
        p = np.asarray(list(p_values), dtype=float)
        if p.size == 0:
            return []
        if np.any((p < 0) | (p > 1) | ~np.isfinite(p)):
            raise ValueError("p values must be finite and between 0 and 1")
        order = np.argsort(p)
        ranked = p[order]
        adjusted = ranked * p.size / np.arange(1, p.size + 1)
        adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
        result = np.empty_like(adjusted)
        result[order] = np.clip(adjusted, 0, 1)
        return [float(value) for value in result]

    @staticmethod
    def percentage(count: float, denominator: float) -> float:
        if denominator <= 0:
            raise ValueError("denominator must be positive")
        return 100 * count / denominator

    @staticmethod
    def confidence_p_consistency(
        estimate: float,
        lower: float,
        upper: float,
        p_value: float,
        *,
        null_value: float = 0.0,
        confidence_level: float = 0.95,
        log_scale: bool = False,
        tolerance: float = 0.02,
    ) -> dict[str, Any]:
        if not 0 <= p_value <= 1:
            return {"consistent": False, "reason": "p_value_out_of_range"}
        try:
            se = StatisticalCalculators.se_from_ci(lower, upper, confidence_level, log_scale=log_scale)
            if log_scale:
                if estimate <= 0 or null_value <= 0:
                    return {"consistent": False, "reason": "nonpositive_log_scale_value"}
                z = (math.log(estimate) - math.log(null_value)) / se
            else:
                z = (estimate - null_value) / se
            implied = StatisticalCalculators.p_from_z(z)
        except (ValueError, ZeroDivisionError, OverflowError):
            return {"consistent": False, "reason": "invalid_interval"}
        alpha = 1 - confidence_level
        interval_excludes_null = (lower > null_value) or (upper < null_value)
        threshold_discordance = (p_value < alpha and not interval_excludes_null) or (p_value >= alpha and interval_excludes_null)
        numeric_discordance = abs(implied - p_value) > max(tolerance, tolerance * max(p_value, 0.01))
        return {
            "consistent": not threshold_discordance and not numeric_discordance,
            "threshold_discordance": threshold_discordance,
            "implied_p_value": implied,
            "reported_p_value": p_value,
            "standard_error_from_interval": se,
        }
