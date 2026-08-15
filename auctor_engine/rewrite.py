from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Mapping

from .models import FactLedger, Issue, RevisionProposal, Severity
from .provider import WritingProvider
from .text_engine import (
    apply_exact_revisions,
    compare_fact_ledgers,
    contains_em_dash,
    extract_fact_ledger,
    plan_em_dash_revisions,
    sanitize_prohibited_dashes,
)


@dataclass(frozen=True)
class PhraseRule:
    pattern: re.Pattern[str]
    replacement: str | Callable[[re.Match[str]], str]
    code: str
    reason: str
    confidence: float = 0.98
    change_class: str = "copyedit"


PHRASE_RULES: tuple[PhraseRule, ...] = (
    PhraseRule(
        re.compile(r"\bIt is important to note that[ \t]+", re.IGNORECASE),
        "",
        "AWE-META-001",
        "Remove meta-language and state the scientific point directly.",
    ),
    PhraseRule(
        re.compile(r"\bIt is worth noting that[ \t]+", re.IGNORECASE),
        "",
        "AWE-META-001",
        "Remove meta-language and state the scientific point directly.",
    ),
    PhraseRule(
        re.compile(r"\bmay potentially\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "may" + match.group("tail"),
        "AWE-EPI-001",
        "Use one calibrated hedge rather than two overlapping hedges.",
    ),
    PhraseRule(
        re.compile(r"\bcould possibly\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "could" + match.group("tail"),
        "AWE-EPI-001",
        "Use one calibrated modal rather than two overlapping modals.",
    ),
    PhraseRule(
        re.compile(r"\bin order to\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "to" + match.group("tail"),
        "AWE-ECON-001",
        "Use the shorter construction where meaning is unchanged.",
    ),
    PhraseRule(
        re.compile(r"\bdue to the fact that\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "because" + match.group("tail"),
        "AWE-ECON-001",
        "Replace a nominal phrase with a direct conjunction.",
    ),
    PhraseRule(
        re.compile(r"\bhas the ability to\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "can" + match.group("tail"),
        "AWE-ECON-001",
        "Use a direct modal where meaning is unchanged.",
    ),
    PhraseRule(
        re.compile(r"\butili[sz]ed\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "used" + match.group("tail"),
        "AWE-LEX-001",
        "Prefer the precise common verb unless a technical distinction requires otherwise.",
    ),
    PhraseRule(
        re.compile(r"\butili[sz]es\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "uses" + match.group("tail"),
        "AWE-LEX-001",
        "Prefer the precise common verb unless a technical distinction requires otherwise.",
    ),
    PhraseRule(
        re.compile(r"\butili[sz]ing\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "using" + match.group("tail"),
        "AWE-LEX-001",
        "Prefer the precise common verb unless a technical distinction requires otherwise.",
    ),
    PhraseRule(
        re.compile(r"\butili[sz]e\b(?P<tail>[ \t]*)", re.IGNORECASE),
        lambda match: "use" + match.group("tail"),
        "AWE-LEX-001",
        "Prefer the precise common verb unless a technical distinction requires otherwise.",
    ),
    PhraseRule(
        re.compile(r"\ba total of[ \t]+(?=\d)", re.IGNORECASE),
        "",
        "AWE-ECON-002",
        "A number already expresses total count unless contrast requires the phrase.",
    ),
)



class SafeCopyeditor:
    """Deterministic editor for meaning-preserving revisions only."""

    def plan(self, text: str, *, paragraph_index: int | None = None, section: str = "other") -> list[RevisionProposal]:
        proposals = plan_em_dash_revisions(text, paragraph_index=paragraph_index, section=section)
        for rule in PHRASE_RULES:
            for match in rule.pattern.finditer(text):
                target = match.group(0)
                replacement = rule.replacement(match) if callable(rule.replacement) else rule.replacement
                if target[:1].isupper() and replacement:
                    replacement = replacement[:1].upper() + replacement[1:]
                proposals.append(
                    RevisionProposal(
                        target=target,
                        replacement=replacement,
                        reason=rule.reason,
                        commentary=rule.reason,
                        code=rule.code,
                        paragraph_index=paragraph_index,
                        section=section,
                        change_class=rule.change_class,
                        confidence=rule.confidence,
                        priority=10,
                        metadata={"start": match.start(), "end": match.end()},
                    )
                )
        return proposals

    def revise(
        self,
        text: str,
        *,
        paragraph_index: int | None = None,
        section: str = "other",
        change_classes: set[str] | None = None,
    ):
        proposals = self.plan(text, paragraph_index=paragraph_index, section=section)
        if change_classes is not None:
            proposals = [proposal for proposal in proposals if proposal.change_class in change_classes]
        return apply_exact_revisions(text, proposals)


PROVIDER_RESPONSE_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "required": ["substantive", "qc", "commentary"],
    "additionalProperties": False,
    "properties": {
        "substantive": {"type": "string"},
        "qc": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["code", "severity", "message", "action"],
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string"},
                    "severity": {"type": "string"},
                    "message": {"type": "string"},
                    "action": {"type": "string"},
                },
            },
        },
        "commentary": {"type": "array", "items": {"type": "string"}},
    },
}


DEVELOPMENTAL_RESPONSE_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "required": ["qc", "commentary"],
    "additionalProperties": False,
    "properties": {
        "qc": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["code", "severity", "message", "action"],
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string"},
                    "severity": {"type": "string"},
                    "message": {"type": "string"},
                    "action": {"type": "string"},
                },
            },
        },
        "commentary": {"type": "array", "items": {"type": "string"}},
    },
}


class ProviderDevelopmentalReviewer:
    """Schema-bound argument and evidence review that cannot rewrite prose."""

    def __init__(self, provider: WritingProvider):
        self.provider = provider

    def review(
        self,
        text: str,
        *,
        section: str,
        writing_contract: Mapping[str, object],
        immutable_phrases: tuple[str, ...] = (),
    ) -> tuple[list[Issue], list[str]]:
        ledger = extract_fact_ledger(text, immutable_phrases)
        payload = {
            "task": "Review the passage developmentally without rewriting it.",
            "section": section,
            "writing_contract": dict(writing_contract),
            "immutable_fact_ledger": ledger.to_dict(),
            "source_text": text,
            "rules": {
                "do_not_return_substantive_prose": True,
                "do_not_invent_missing_evidence": True,
                "qc_contains_only_verifiable_defects": True,
                "commentary_contains_only_editorial_reasoning_or_author_queries": True,
                "zero_em_dash_in_all_channels": True,
            },
        }
        response = self.provider.complete_json(
            system=self._system_instruction(),
            user=json.dumps(payload, ensure_ascii=False, indent=2),
            schema=DEVELOPMENTAL_RESPONSE_SCHEMA,
        )
        if not isinstance(response, Mapping):
            return [self._schema_issue("The developmental provider did not return a JSON object.")], []

        issues: list[Issue] = []
        allowed_keys = {"qc", "commentary"}
        unexpected = sorted(str(key) for key in response if key not in allowed_keys)
        if unexpected:
            issues.append(
                Issue(
                    code="AWE-CHANNEL-DEV-001",
                    title="Developmental provider attempted to use a forbidden channel",
                    severity=Severity.CRITICAL.value,
                    message=f"Unexpected response fields: {unexpected}.",
                    action="Ignore all provider prose and return only QC and commentary from a schema-valid response.",
                    source="provider",
                )
            )

        raw_qc = response.get("qc")
        raw_commentary = response.get("commentary")
        schema_errors: list[str] = []
        if not isinstance(raw_qc, list):
            schema_errors.append("qc must be an array")
        if not isinstance(raw_commentary, list) or any(not isinstance(item, str) for item in raw_commentary or []):
            schema_errors.append("commentary must be an array of strings")
        if schema_errors:
            issues.append(self._schema_issue("; ".join(schema_errors)))
            return issues, []

        allowed_severities = {item.value for item in Severity}
        for item in raw_qc:
            if not isinstance(item, Mapping):
                issues.append(self._schema_issue("Each developmental qc item must be an object."))
                continue
            severity = str(item.get("severity", Severity.MEDIUM.value)).lower()
            if severity not in allowed_severities:
                severity = Severity.MEDIUM.value
            issues.append(
                Issue(
                    code=str(item.get("code", "AWE-DEVELOPMENTAL-QC")),
                    title="Developmental quality-control finding",
                    severity=severity,
                    message=sanitize_prohibited_dashes(str(item.get("message", ""))),
                    action=sanitize_prohibited_dashes(str(item.get("action", ""))),
                    source="provider",
                )
            )
        commentary = [sanitize_prohibited_dashes(item) for item in raw_commentary if item.strip()]
        return issues, commentary

    @staticmethod
    def _schema_issue(message: str) -> Issue:
        return Issue(
            code="AWE-PROVIDER-DEV-001",
            title="Developmental provider response violated the channel schema",
            severity=Severity.CRITICAL.value,
            message=message,
            action="Reject the provider response and preserve the manuscript passage unchanged.",
            source="provider",
        )

    @staticmethod
    def _system_instruction() -> str:
        return (
            "You are the developmental reviewer inside Auctor Academic Writing Engine. "
            "Do not rewrite or return manuscript prose. Diagnose missing argument, evidence, section function, inference, "
            "scope, and reporting information. Put objective release findings in qc. Put editorial reasoning and precise "
            "author queries in commentary. Do not invent data, methods, references, mechanisms, or analyses. Use no em dash "
            "characters in any channel. Return only schema-valid JSON."
        )


class ProviderRewriter:
    """Constrained substantive rewriter with deterministic validation."""

    def __init__(self, provider: WritingProvider):
        self.provider = provider

    def rewrite(
        self,
        text: str,
        *,
        section: str,
        section_contract: Mapping[str, object],
        immutable_phrases: tuple[str, ...] = (),
    ) -> tuple[str, list[Issue], list[str]]:
        ledger = extract_fact_ledger(text, immutable_phrases)
        system = self._system_instruction()
        payload = {
            "task": "Revise the passage as publication-ready academic prose.",
            "section": section,
            "section_contract": dict(section_contract),
            "immutable_fact_ledger": ledger.to_dict(),
            "source_text": text,
            "rules": {
                "do_not_invent": True,
                "do_not_delete_citations_or_numbers": True,
                "zero_em_dash": True,
                "substantive_channel_contains_only_manuscript_prose": True,
                "qc_channel_contains_only_verifiable_defects": True,
                "commentary_channel_contains_only_editorial_reasoning": True,
            },
        }
        response = self.provider.complete_json(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, indent=2),
            schema=PROVIDER_RESPONSE_SCHEMA,
        )
        issues: list[Issue] = []
        if not isinstance(response, Mapping):
            issues.append(
                Issue(
                    code="AWE-PROVIDER-002",
                    title="Provider response violated the channel schema",
                    severity=Severity.CRITICAL.value,
                    message="The provider did not return a JSON object.",
                    action="Reject the provider response and preserve the source passage.",
                )
            )
            return text, issues, []

        substantive_value = response.get("substantive")
        qc_value = response.get("qc")
        commentary_value = response.get("commentary")
        schema_errors: list[str] = []
        if not isinstance(substantive_value, str):
            schema_errors.append("substantive must be a string")
        if not isinstance(qc_value, list):
            schema_errors.append("qc must be an array")
        if not isinstance(commentary_value, list) or any(not isinstance(item, str) for item in commentary_value or []):
            schema_errors.append("commentary must be an array of strings")
        if schema_errors:
            issues.append(
                Issue(
                    code="AWE-PROVIDER-002",
                    title="Provider response violated the channel schema",
                    severity=Severity.CRITICAL.value,
                    message="; ".join(schema_errors),
                    action="Reject the provider response and preserve the source passage.",
                )
            )
            return text, issues, []

        substantive = substantive_value
        commentary = [sanitize_prohibited_dashes(item) for item in commentary_value if item.strip()]
        allowed_severities = {item.value for item in Severity}
        for item in qc_value:
            if not isinstance(item, Mapping):
                issues.append(
                    Issue(
                        code="AWE-PROVIDER-002",
                        title="Provider QC item violated the channel schema",
                        severity=Severity.CRITICAL.value,
                        message="Each qc item must be a JSON object.",
                        action="Reject the provider response and preserve the source passage.",
                    )
                )
                continue
            severity = str(item.get("severity", Severity.MEDIUM.value)).lower()
            if severity not in allowed_severities:
                severity = Severity.MEDIUM.value
            issues.append(
                Issue(
                    code=str(item.get("code", "AWE-PROVIDER-QC")),
                    title="Provider quality-control finding",
                    severity=severity,
                    message=sanitize_prohibited_dashes(str(item.get("message", ""))),
                    action=sanitize_prohibited_dashes(str(item.get("action", ""))),
                    source="provider",
                )
            )

        if not substantive.strip():
            issues.append(
                Issue(
                    code="AWE-PROVIDER-001",
                    title="Provider returned no substantive prose",
                    severity=Severity.CRITICAL.value,
                    message="The substantive channel was empty.",
                    action="Reject the provider response and preserve the source passage.",
                )
            )
            return text, issues, commentary

        if contains_em_dash(substantive):
            issues.append(
                Issue(
                    code="AWE-STYLE-001",
                    title="Prohibited em dash in substantive prose",
                    severity=Severity.CRITICAL.value,
                    message="The provider response contains a prohibited em dash character.",
                    action="Reject the response or repair the punctuation before factual validation and application.",
                )
            )

        revised_ledger = extract_fact_ledger(substantive, immutable_phrases)
        issues.extend(compare_fact_ledgers(ledger, revised_ledger))
        leakage_markers = ("QC:", "COMMENTARY:", "As an AI", "I revised", "editorial note")
        leaked = [marker for marker in leakage_markers if marker.lower() in substantive.lower()]
        if leaked:
            issues.append(
                Issue(
                    code="AWE-CHANNEL-001",
                    title="Non-substantive language leaked into manuscript prose",
                    severity=Severity.CRITICAL.value,
                    message=f"Detected channel leakage markers: {leaked}.",
                    action="Reject the response and keep QC or commentary outside the manuscript body.",
                )
            )

        if any(issue.severity == Severity.CRITICAL.value for issue in issues):
            return text, issues, commentary
        return substantive, issues, commentary

    @staticmethod
    def _system_instruction() -> str:
        return (
            "You are the substantive writer inside Auctor Academic Writing Engine. "
            "Produce exact scientific prose, not a performance of academic tone. "
            "Preserve every number, citation, named entity, effect measure, direction, limitation, and scope condition unless "
            "the request explicitly authorizes a factual change. Do not invent data, references, mechanisms, analyses, or claims. "
            "The substantive field must contain only manuscript prose. Put objective defects in qc. Put editorial reasoning in "
            "commentary. Use no em dash characters. Prefer concrete subjects, exact verbs, bounded claims, and section-specific "
            "information. Return only schema-valid JSON."
        )
