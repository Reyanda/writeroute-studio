from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

from .models import Issue, Severity, TextAnchor
from .negative_engine import PatternEngine
from .text_engine import EM_DASH_RE, NUMBER_RE, split_sentences

HEDGE_STACK_RE = re.compile(
    r"\b(?:may|might|could|possibly|potentially|perhaps|seems?|appears?|suggests?)\b(?:[^.!?]{0,65}\b(?:may|might|could|possibly|potentially|perhaps|seems?|appears?|suggests?)\b){1,}",
    re.IGNORECASE,
)
META_RE = re.compile(
    r"\b(?:it is (?:important|worthwhile|essential|crucial) to note that|this paper will|this article will|"
    r"the following section (?:discusses|explores)|as an ai language model|i have revised)\b",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"(?:\[(?:citation needed|insert citation|ref|reference)\]|\b(?:TBD|TODO|TK)\b|\?\?\?)",
    re.IGNORECASE,
)
GENERIC_IMPACT_RE = re.compile(
    r"\b(?:significant implications for (?:policymakers|stakeholders)|important contribution to the literature|"
    r"holistic approach|multifaceted challenge|rapidly evolving (?:landscape|environment))\b",
    re.IGNORECASE,
)
UNCERTAINTY_RE = re.compile(
    r"\b(?:95\s*%?\s*(?:CI|CrI)|confidence interval|credible interval|standard error|SE\s*[=:]|"
    r"IQR|interquartile range|standard deviation|SD\s*[=:]|p\s*[<=>])\b",
    re.IGNORECASE,
)
RESULT_CLAIM_RE = re.compile(
    r"\b(?:increased|decreased|higher|lower|improved|reduced|associated|association|risk|effect|difference|mortality|prevalence)\b",
    re.IGNORECASE,
)
VAGUE_REFERENT_RE = re.compile(r"\b(?:these findings|these results|this issue|this approach|various factors|stakeholders)\b", re.I)


class AcademicCritic:
    """Composite critic for style, reasoning, reporting, and integrity."""

    def __init__(self, use_negative_engine: bool = True):
        self.negative: PatternEngine | None = None
        if use_negative_engine:
            lookup = files("auctor_engine.data").joinpath("negative_pattern_lookup.yaml")
            with lookup.open("r", encoding="utf-8") as handle:
                import yaml

                data = yaml.safe_load(handle)
            self.negative = PatternEngine(data)

    def audit(self, text: str, *, section: str = "other", metadata: Mapping[str, Any] | None = None) -> list[Issue]:
        issues: list[Issue] = []
        issues.extend(self._core_checks(text, section))
        if self.negative is not None:
            synthetic = f"## {section.title()}\n\n{text}" if section != "other" else text
            report = self.negative.analyze(synthetic, metadata=metadata or {})
            offset_adjustment = synthetic.find(text)
            for finding in report.get("findings", []):
                start = max(0, int(finding.get("start", 0)) - offset_adjustment)
                end = max(start, int(finding.get("end", start)) - offset_adjustment)
                issues.append(
                    Issue(
                        code=str(finding.get("rule_id", "SCI-UNKNOWN")),
                        title=str(finding.get("name", "Scientific writing finding")),
                        severity=str(finding.get("severity", Severity.MEDIUM.value)),
                        message=str(finding.get("why", "")),
                        evidence=str(finding.get("match", finding.get("excerpt", ""))),
                        action=str(finding.get("revision", "Revise the passage.")),
                        anchor=TextAnchor(start=start, end=end, section=section, quote=str(finding.get("match", ""))),
                        confidence=float(finding.get("confidence", 0.7)),
                        source="scientific_negative_engine",
                        auto_fixable=False,
                        metadata={"evidence_tier": finding.get("evidence_tier")},
                    )
                )
        return self._deduplicate(issues)

    def _core_checks(self, text: str, section: str) -> list[Issue]:
        issues: list[Issue] = []
        for match in EM_DASH_RE.finditer(text):
            issues.append(
                Issue(
                    code="AWE-STYLE-001",
                    title="Prohibited em dash",
                    severity=Severity.HIGH.value,
                    message="The manuscript profile permits no em dash characters.",
                    evidence=match.group(0),
                    action="Replace the mark with a colon, semicolon, comma, or parentheses according to the logical relation.",
                    anchor=TextAnchor(start=match.start(), end=match.end(), section=section, quote=match.group(0)),
                    auto_fixable=True,
                )
            )
        for regex, code, title, action in (
            (META_RE, "AWE-META-001", "Meta-language in manuscript prose", "State the scientific proposition directly."),
            (HEDGE_STACK_RE, "AWE-EPI-001", "Stacked hedging", "Use one calibrated hedge and attach uncertainty to an estimate, assumption, or bias."),
            (PLACEHOLDER_RE, "AWE-INT-001", "Drafting placeholder", "Resolve the placeholder before submission."),
            (GENERIC_IMPACT_RE, "AWE-SPEC-001", "Generic academic framing", "Replace generic significance language with a population, estimate, mechanism, or named decision."),
        ):
            for match in regex.finditer(text):
                issues.append(
                    Issue(
                        code=code,
                        title=title,
                        severity=Severity.HIGH.value if code == "AWE-INT-001" else Severity.MEDIUM.value,
                        message=title,
                        evidence=match.group(0),
                        action=action,
                        anchor=TextAnchor(start=match.start(), end=match.end(), section=section, quote=match.group(0)),
                        auto_fixable=code in {"AWE-META-001", "AWE-EPI-001"},
                    )
                )

        sentences = split_sentences(text)
        if section == "results":
            for sentence in sentences:
                if RESULT_CLAIM_RE.search(sentence) and not NUMBER_RE.search(sentence):
                    issues.append(
                        Issue(
                            code="AWE-RES-001",
                            title="Result claim without magnitude",
                            severity=Severity.HIGH.value,
                            message="The Results sentence states a direction or effect without a numeric anchor.",
                            evidence=sentence,
                            action="Report the estimate, comparator, denominator where relevant, and uncertainty.",
                            anchor=TextAnchor(section=section, quote=sentence),
                        )
                    )
                if NUMBER_RE.search(sentence) and RESULT_CLAIM_RE.search(sentence) and not UNCERTAINTY_RE.search(sentence):
                    issues.append(
                        Issue(
                            code="AWE-RES-002",
                            title="Effect estimate without uncertainty",
                            severity=Severity.MEDIUM.value,
                            message="The sentence reports an effect or difference without an uncertainty measure.",
                            evidence=sentence,
                            action="Add the confidence interval, credible interval, standard error, or prespecified uncertainty measure.",
                            anchor=TextAnchor(section=section, quote=sentence),
                        )
                    )
        if section in {"discussion", "conclusion", "abstract"}:
            for sentence in sentences:
                if VAGUE_REFERENT_RE.search(sentence) and not NUMBER_RE.search(sentence):
                    issues.append(
                        Issue(
                            code="AWE-SPEC-002",
                            title="Vague referent without empirical anchor",
                            severity=Severity.MEDIUM.value,
                            message="The sentence refers to findings, results, an approach, or stakeholders without naming the relevant evidence or actor.",
                            evidence=sentence,
                            action="Name the result, population, mechanism, decision-maker, or action.",
                            anchor=TextAnchor(section=section, quote=sentence),
                        )
                    )

        for sentence in sentences:
            word_count = len(re.findall(r"\b\w+\b", sentence))
            if word_count > 45:
                issues.append(
                    Issue(
                        code="AWE-SENT-001",
                        title="Overloaded sentence",
                        severity=Severity.MEDIUM.value,
                        message=f"The sentence contains {word_count} words and likely carries more than one reasoning step.",
                        evidence=sentence,
                        action="Separate the claim, evidence, interpretation, and boundary where they perform distinct functions.",
                        anchor=TextAnchor(section=section, quote=sentence),
                    )
                )
        return issues

    @staticmethod
    def _deduplicate(issues: list[Issue]) -> list[Issue]:
        seen: set[tuple[str, str, int | None, int | None]] = set()
        output: list[Issue] = []
        for issue in issues:
            key = (issue.code, issue.evidence, issue.anchor.start, issue.anchor.end)
            if key in seen:
                continue
            seen.add(key)
            output.append(issue)
        return output
