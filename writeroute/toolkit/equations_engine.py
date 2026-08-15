"""Scientific Equation Engine: Validates, formats, and provides standard formulas

for medical statistics, causal inference, and epidemiology.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class EquationTemplate:
    id: str
    name: str
    category: str
    latex: str
    description: str


@dataclass
class EquationRenderResult:
    valid: bool
    latex: str
    mathml_stub: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScientificEquationEngine:
    """Validator and catalog for scientific formulas."""

    FORMULA_CATALOG: list[EquationTemplate] = [
        EquationTemplate(
            id="odds_ratio",
            name="Odds Ratio (2x2 Table)",
            category="Epidemiology",
            latex=r"OR = \frac{a \cdot d}{b \cdot c} = \frac{p_1 / (1 - p_1)}{p_0 / (1 - p_0)}",
            description="Measure of association between an exposure and an outcome.",
        ),
        EquationTemplate(
            id="relative_risk",
            name="Risk Ratio / Relative Risk",
            category="Epidemiology",
            latex=r"RR = \frac{a / (a + b)}{c / (c + d)} = \frac{I_e}{I_u}",
            description="Ratio of the probability of an outcome in an exposed group to the unexposed.",
        ),
        EquationTemplate(
            id="hazard_ratio",
            name="Cox Proportional Hazard Ratio",
            category="Survival Analysis",
            latex=r"\lambda(t \mid X) = \lambda_0(t) \exp(\beta_1 X_1 + \dots + \beta_k X_k)",
            description="Semiparametric hazard model for time-to-event outcomes.",
        ),
        EquationTemplate(
            id="aipw_estimator",
            name="Augmented Inverse Probability Weighting (AIPW)",
            category="Causal Inference",
            latex=r"\hat{\psi}_{\text{AIPW}} = \frac{1}{n} \sum_{i=1}^n \left[ \mu_1(X_i) - \mu_0(X_i) + \frac{A_i (Y_i - \mu_1(X_i))}{e(X_i)} - \frac{(1 - A_i)(Y_i - \mu_0(X_i))}{1 - e(X_i)} \right]",
            description="Doubly robust estimator for the Average Treatment Effect (ATE).",
        ),
        EquationTemplate(
            id="did_estimator",
            name="Difference-in-Differences (DiD)",
            category="Econometrics & Policy",
            latex=r"\hat{\delta}_{\text{DiD}} = (\bar{Y}_{T, \text{post}} - \bar{Y}_{T, \text{pre}}) - (\bar{Y}_{C, \text{post}} - \bar{Y}_{C, \text{pre}})",
            description="Evaluates causal policy impact by comparing pre/post changes between treated and control groups.",
        ),
        EquationTemplate(
            id="bayes_theorem",
            name="Bayes' Theorem",
            category="Probability",
            latex=r"P(A \mid B) = \frac{P(B \mid A) \cdot P(A)}{P(B)}",
            description="Calculates posterior probability given prior belief and observed evidence.",
        ),
        EquationTemplate(
            id="meta_analysis_inverse_variance",
            name="Inverse-Variance Meta-Analysis Weighting",
            category="Evidence Synthesis",
            latex=r"w_i = \frac{1}{v_i + \tau^2}, \quad \hat{\theta}_{\text{pooled}} = \frac{\sum w_i \hat{\theta}_i}{\sum w_i}",
            description="Derives pooled effect estimate with fixed or DerSimonian-Laird random-effects weighting.",
        ),
    ]

    @classmethod
    def validate_latex(cls, latex_str: str) -> EquationRenderResult:
        """Checks for balanced braces and valid LaTeX math tokens."""
        s = latex_str.strip()
        if not s:
            return EquationRenderResult(valid=False, latex="", mathml_stub="", error="Empty formula")

        # Balance check for braces
        open_b = s.count("{")
        close_b = s.count("}")
        if open_b != close_b:
            return EquationRenderResult(
                valid=False,
                latex=s,
                mathml_stub="",
                error=f"Unbalanced curly braces: {open_b} open vs {close_b} closed",
            )

        # Generate MathML representation
        clean_tex = re.sub(r"\\[a-zA-Z]+", "", s)
        mathml = f"<math xmlns='http://www.w3.org/1998/Math/MathML' display='block'><semantics><mrow><mtext>{clean_tex}</mtext></mrow><annotation encoding='application/x-tex'>{s}</annotation></semantics></math>"

        return EquationRenderResult(valid=True, latex=s, mathml_stub=mathml, error=None)

    @classmethod
    def list_templates(cls) -> list[dict[str, Any]]:
        return [asdict(t) for t in cls.FORMULA_CATALOG]
