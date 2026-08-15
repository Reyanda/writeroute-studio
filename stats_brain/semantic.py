from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .models import ReviewContext, ReviewFinding
from .utils import deep_sanitize, present


NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.]\d+)?(?:%|\b)")


class StatisticalReasoningProvider(Protocol):
    def review(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return a structured semantic review response."""


class SemanticReviewHarness:
    """Guarded interface for a language model or expert rules service.

    The provider may identify semantic issues that deterministic rules cannot
    resolve, but it may not invent data, silently rewrite claims, or issue a
    statistical verdict without evidence and an epistemic classification.
    """

    def build_request(
        self,
        context: ReviewContext,
        reconstructed_problem: Mapping[str, Any],
        method_profiles: Sequence[Mapping[str, Any]],
        debate_notes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        return deep_sanitize(
            {
                "engine": "STATS-BRAIN",
                "operation": "semantic_statistical_review",
                "contract": {
                    "output_only_json": True,
                    "no_invented_numbers": True,
                    "no_invented_methods_or_analyses": True,
                    "quote_or_locate_every_finding": True,
                    "separate_error_from_debate": True,
                    "allowed_epistemic_status": [
                        "known_error",
                        "consensus_requirement",
                        "context_dependent",
                        "active_debate",
                        "not_identifiable",
                        "not_assessable",
                        "informational",
                    ],
                    "substantive_rewrite_authority": "none",
                },
                "reconstructed_problem": dict(reconstructed_problem),
                "manifest": context.manifest,
                "manuscript_text": context.manuscript_text,
                "method_profiles": list(method_profiles),
                "debate_notes": list(debate_notes),
                "required_response": {
                    "findings": [
                        {
                            "rule_id": "SB-SEM-001",
                            "title": "string",
                            "domain": "string",
                            "severity": "fatal|critical|major|minor|query|info",
                            "epistemic_status": "allowed value",
                            "evidence_excerpt": "exact manuscript excerpt or null",
                            "location": "section or manifest path",
                            "observed": "what is present",
                            "expected": "what is required",
                            "rationale": "technical explanation",
                            "repair": "specific author action without inventing results",
                            "source_ids": [],
                        }
                    ],
                    "not_assessable": [],
                },
            }
        )

    def validate_response(self, response: Mapping[str, Any], context: ReviewContext) -> tuple[list[ReviewFinding], list[dict[str, Any]]]:
        if not isinstance(response, Mapping):
            raise ValueError("Semantic provider response must be a mapping")
        findings_value = response.get("findings", [])
        if not isinstance(findings_value, list):
            raise ValueError("Semantic provider findings must be a list")
        findings: list[ReviewFinding] = []
        manuscript = context.manuscript_text
        manuscript_numbers = set(NUMBER_RE.findall(manuscript))
        for index, item in enumerate(findings_value):
            if not isinstance(item, Mapping):
                raise ValueError(f"Semantic finding {index} must be a mapping")
            excerpt = item.get("evidence_excerpt")
            if present(excerpt) and str(excerpt) not in manuscript:
                raise ValueError(f"Semantic finding {index} uses an excerpt not present in the manuscript")
            observed_numbers = set(NUMBER_RE.findall(str(item.get("observed", ""))))
            expected_numbers = set(NUMBER_RE.findall(str(item.get("expected", ""))))
            invented = (observed_numbers | expected_numbers) - manuscript_numbers
            if manuscript and invented:
                raise ValueError(f"Semantic finding {index} introduces unsupported numeric tokens: {sorted(invented)}")
            rule_id = str(item.get("rule_id", f"SB-SEM-{index + 1:03d}"))
            if not rule_id.startswith("SB-SEM-"):
                rule_id = f"SB-SEM-{index + 1:03d}"
            findings.append(
                ReviewFinding(
                    rule_id=rule_id,
                    title=str(item.get("title", "Semantic statistical issue")),
                    domain=str(item.get("domain", "semantic_review")),
                    severity=str(item.get("severity", "query")),
                    epistemic_status=str(item.get("epistemic_status", "not_assessable")),
                    location=item.get("location"),
                    observed=item.get("observed"),
                    expected=item.get("expected"),
                    rationale=str(item.get("rationale", "")),
                    repair=str(item.get("repair", "Request author clarification.")),
                    evidence_excerpt=str(excerpt) if present(excerpt) else None,
                    source_ids=[str(value) for value in item.get("source_ids", [])],
                    confidence=str(item.get("confidence", "medium")),
                    manual_review=True,
                    tags=["semantic_provider"],
                )
            )
        not_assessable = response.get("not_assessable", [])
        if not isinstance(not_assessable, list):
            raise ValueError("Semantic provider not_assessable must be a list")
        return findings, [dict(item) if isinstance(item, Mapping) else {"item": str(item)} for item in not_assessable]
