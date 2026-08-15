from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from .models import EvidenceMapEntry, EvidencePacket, Issue, Severity, TextAnchor
from .provider import WritingProvider
from .text_engine import contains_em_dash, extract_fact_ledger, sanitize_prohibited_dashes, split_sentences


DRAFT_RESPONSE_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "required": ["substantive", "evidence_map", "qc", "commentary"],
    "additionalProperties": False,
    "properties": {
        "substantive": {"type": "string"},
        "evidence_map": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["sentence_index", "evidence_ids", "claim_type"],
                "additionalProperties": False,
                "properties": {
                    "sentence_index": {"type": "integer", "minimum": 0},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "claim_type": {"type": "string"},
                },
            },
        },
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


class EvidenceDrafter:
    """Draft manuscript prose from a closed evidence packet.

    Every factual sentence must map to one or more evidence item identifiers.
    Candidate facts may be a subset of the packet, but they may not exceed it.
    """

    def __init__(self, provider: WritingProvider | None = None):
        self.provider = provider

    def prepare_request(
        self,
        packet: EvidencePacket,
        *,
        writing_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._validate_packet(packet)
        payload = {
            "task": "Draft one publication-ready manuscript section from the closed evidence packet.",
            "section": packet.section,
            "objective": packet.objective,
            "audience": packet.audience,
            "target_journal": packet.target_journal,
            "allowed_claim_types": packet.allowed_claim_types,
            "constraints": packet.constraints,
            "writing_contract": dict(writing_contract),
            "evidence_items": [item.to_dict() for item in packet.items],
            "rules": {
                "closed_world_evidence": True,
                "do_not_invent": True,
                "do_not_import_background_knowledge": True,
                "sentence_indices_are_zero_based": True,
                "map_every_factual_sentence_to_evidence_ids": True,
                "substantive_contains_only_manuscript_prose": True,
                "qc_contains_only_verifiable_findings": True,
                "commentary_contains_only_editorial_reasoning": True,
                "zero_em_dash": True,
            },
        }
        return {
            "system": self._system_instruction(),
            "user": json.dumps(payload, ensure_ascii=False, indent=2),
            "schema": DRAFT_RESPONSE_SCHEMA,
        }

    def draft(
        self,
        packet: EvidencePacket,
        *,
        writing_contract: Mapping[str, Any],
    ) -> tuple[str, list[Issue], list[str], list[EvidenceMapEntry]]:
        if self.provider is None:
            raise ValueError("A WritingProvider is required for draft generation.")
        request = self.prepare_request(packet, writing_contract=writing_contract)
        response = self.provider.complete_json(
            system=str(request["system"]),
            user=str(request["user"]),
            schema=request["schema"],
        )
        return self.validate_response(packet, response, writing_contract=writing_contract)

    def validate_response(
        self,
        packet: EvidencePacket,
        response: Mapping[str, Any] | object,
        *,
        writing_contract: Mapping[str, Any],
    ) -> tuple[str, list[Issue], list[str], list[EvidenceMapEntry]]:
        del writing_contract
        self._validate_packet(packet)
        issues: list[Issue] = []
        if not isinstance(response, Mapping):
            return "", [self._schema_issue("The draft provider did not return a JSON object.")], [], []

        substantive = response.get("substantive")
        raw_map = response.get("evidence_map")
        raw_qc = response.get("qc")
        raw_commentary = response.get("commentary")
        schema_errors: list[str] = []
        if not isinstance(substantive, str):
            schema_errors.append("substantive must be a string")
        if not isinstance(raw_map, list):
            schema_errors.append("evidence_map must be an array")
        if not isinstance(raw_qc, list):
            schema_errors.append("qc must be an array")
        if not isinstance(raw_commentary, list) or any(not isinstance(item, str) for item in raw_commentary or []):
            schema_errors.append("commentary must be an array of strings")
        if schema_errors:
            return "", [self._schema_issue("; ".join(schema_errors))], [], []

        commentary = [sanitize_prohibited_dashes(item) for item in raw_commentary if item.strip()]
        allowed_severities = {item.value for item in Severity}
        for item in raw_qc:
            if not isinstance(item, Mapping):
                issues.append(self._schema_issue("Each qc item must be an object."))
                continue
            severity = str(item.get("severity", Severity.MEDIUM.value)).lower()
            if severity not in allowed_severities:
                severity = Severity.MEDIUM.value
            issues.append(
                Issue(
                    code=str(item.get("code", "AWE-DRAFT-QC")),
                    title="Draft-provider quality-control finding",
                    severity=severity,
                    message=sanitize_prohibited_dashes(str(item.get("message", ""))),
                    action=sanitize_prohibited_dashes(str(item.get("action", ""))),
                    source="provider",
                )
            )

        if not substantive.strip():
            issues.append(
                Issue(
                    code="AWE-DRAFT-001",
                    title="Draft provider returned no substantive prose",
                    severity=Severity.CRITICAL.value,
                    message="The substantive channel was empty.",
                    action="Return no manuscript prose until a valid evidence-grounded draft is available.",
                )
            )
            return "", issues, commentary, []

        if contains_em_dash(substantive):
            issues.append(
                Issue(
                    code="AWE-STYLE-001",
                    title="Prohibited em dash in drafted prose",
                    severity=Severity.CRITICAL.value,
                    message="The substantive channel contains a prohibited em dash character.",
                    action="Repair the punctuation and revalidate the complete response.",
                )
            )

        leakage_markers = (
            "QC:",
            "COMMENTARY:",
            "As an AI",
            "I drafted",
            "editorial note",
            "evidence item",
            "source packet",
        )
        leaked = [marker for marker in leakage_markers if marker.casefold() in substantive.casefold()]
        if leaked:
            issues.append(
                Issue(
                    code="AWE-CHANNEL-001",
                    title="Non-substantive language leaked into drafted prose",
                    severity=Severity.CRITICAL.value,
                    message=f"Detected channel leakage markers: {leaked}.",
                    action="Keep evidence identifiers, QC, and editorial reasoning outside the manuscript body.",
                )
            )

        issues.extend(self._validate_closed_world_facts(packet, substantive))
        evidence_map, map_issues = self._parse_evidence_map(packet, substantive, raw_map)
        issues.extend(map_issues)

        if any(issue.severity == Severity.CRITICAL.value for issue in issues):
            return "", issues, commentary, evidence_map
        return substantive, issues, commentary, evidence_map

    def _validate_closed_world_facts(self, packet: EvidencePacket, substantive: str) -> list[Issue]:
        allowed_text = "\n".join(
            " ".join(part for part in (item.content, item.source_key) if part)
            for item in packet.items
        )
        allowed = extract_fact_ledger(allowed_text)
        candidate = extract_fact_ledger(substantive)
        issues: list[Issue] = []
        categories: Sequence[tuple[str, Sequence[str], Sequence[str]]] = (
            ("numbers", allowed.numbers, candidate.numbers),
            ("citations", allowed.citations, candidate.citations),
            ("statistical expressions", allowed.statistical_expressions, candidate.statistical_expressions),
            ("units", allowed.units, candidate.units),
            ("study or source identifiers", allowed.registry_ids, candidate.registry_ids),
            ("table, figure, or supplement references", allowed.cross_references, candidate.cross_references),
            ("directionality", allowed.directionality, candidate.directionality),
        )
        for label, permitted, observed in categories:
            permitted_set = set(permitted)
            unexpected = sorted({item for item in observed if item not in permitted_set})
            if unexpected:
                issues.append(
                    Issue(
                        code="AWE-DRAFT-FACT-001",
                        title=f"Draft introduced unsupported {label}",
                        severity=Severity.CRITICAL.value,
                        message=f"The draft contains {label} absent from the closed evidence packet: {unexpected}.",
                        evidence="Closed-world fact comparison",
                        action="Remove the unsupported content or add an authorized evidence item and redraft.",
                        metadata={"category": label, "unexpected": unexpected},
                    )
                )

        allowed_claim_types = {value.casefold() for value in packet.allowed_claim_types}
        allowed_claim_types.update(item.claim_type.casefold() for item in packet.items)
        unexpected_claim_types = sorted({value for value in candidate.claim_force if value.casefold() not in allowed_claim_types})
        if unexpected_claim_types:
            issues.append(
                Issue(
                    code="AWE-DRAFT-INFER-001",
                    title="Draft exceeded the authorized inferential class",
                    severity=Severity.CRITICAL.value,
                    message=f"Unauthorized claim classes occurred in the draft: {unexpected_claim_types}.",
                    evidence="Allowed claim-type comparison",
                    action="Use only the claim classes authorized by the evidence packet.",
                    metadata={"allowed": sorted(allowed_claim_types), "unexpected": unexpected_claim_types},
                )
            )
        return issues

    def _parse_evidence_map(
        self,
        packet: EvidencePacket,
        substantive: str,
        raw_map: list[Any],
    ) -> tuple[list[EvidenceMapEntry], list[Issue]]:
        issues: list[Issue] = []
        valid_ids = {item.id for item in packet.items}
        sentences = split_sentences(substantive)
        parsed: list[EvidenceMapEntry] = []
        by_sentence: dict[int, set[str]] = defaultdict(set)
        claim_types: dict[int, str] = {}
        allowed_claim_types = {value.casefold() for value in packet.allowed_claim_types}
        allowed_claim_types.update(item.claim_type.casefold() for item in packet.items)

        for item in raw_map:
            if not isinstance(item, Mapping):
                issues.append(self._schema_issue("Each evidence_map item must be an object."))
                continue
            index = item.get("sentence_index")
            evidence_ids = item.get("evidence_ids")
            claim_type = str(item.get("claim_type", "descriptive")).casefold()
            if not isinstance(index, int) or index < 0 or index >= len(sentences):
                issues.append(
                    Issue(
                        code="AWE-EVIDENCE-001",
                        title="Evidence map contains an invalid sentence index",
                        severity=Severity.CRITICAL.value,
                        message=f"Sentence index {index!r} is outside the draft range 0 to {max(0, len(sentences) - 1)}.",
                        action="Rebuild the sentence-level evidence map after finalizing the substantive text.",
                    )
                )
                continue
            if not isinstance(evidence_ids, list) or any(not isinstance(value, str) for value in evidence_ids):
                issues.append(self._schema_issue("evidence_ids must be an array of strings."))
                continue
            unknown = sorted({value for value in evidence_ids if value not in valid_ids})
            if unknown:
                issues.append(
                    Issue(
                        code="AWE-EVIDENCE-002",
                        title="Evidence map cites an unknown evidence item",
                        severity=Severity.CRITICAL.value,
                        message=f"Unknown evidence identifiers: {unknown}.",
                        action="Use only identifiers present in the closed evidence packet.",
                        anchor=TextAnchor(start=None, end=None, section=packet.section, quote=sentences[index]),
                    )
                )
            if claim_type not in allowed_claim_types and claim_type != "descriptive":
                issues.append(
                    Issue(
                        code="AWE-EVIDENCE-003",
                        title="Evidence map uses an unauthorized claim type",
                        severity=Severity.CRITICAL.value,
                        message=f"Claim type '{claim_type}' is not authorized by the packet.",
                        action="Use an authorized claim type or amend the packet through author review.",
                        anchor=TextAnchor(section=packet.section, quote=sentences[index]),
                    )
                )
            by_sentence[index].update(value for value in evidence_ids if value in valid_ids)
            claim_types[index] = claim_type

        for index, evidence_ids in sorted(by_sentence.items()):
            parsed.append(
                EvidenceMapEntry(
                    sentence_index=index,
                    evidence_ids=tuple(sorted(evidence_ids)),
                    claim_type=claim_types.get(index, "descriptive"),
                )
            )

        for index, sentence in enumerate(sentences):
            ledger = extract_fact_ledger(sentence)
            factual = bool(
                ledger.numbers
                or ledger.citations
                or ledger.registry_ids
                or ledger.cross_references
                or ledger.directionality
                or ledger.claim_force
            )
            if factual and not by_sentence.get(index):
                issues.append(
                    Issue(
                        code="AWE-EVIDENCE-004",
                        title="Factual sentence has no evidence mapping",
                        severity=Severity.CRITICAL.value,
                        message="A factual or inferential sentence was not linked to an evidence item.",
                        evidence=sentence,
                        action="Map the sentence to one or more evidence identifiers or remove the unsupported claim.",
                        anchor=TextAnchor(section=packet.section, quote=sentence),
                        metadata={"sentence_index": index},
                    )
                )
        return parsed, issues

    @staticmethod
    def _validate_packet(packet: EvidencePacket) -> None:
        if not packet.section.strip():
            raise ValueError("The evidence packet requires a section.")
        if not packet.objective.strip():
            raise ValueError("The evidence packet requires an objective.")
        if not packet.items:
            raise ValueError("The evidence packet requires at least one item.")
        allowed = {"descriptive", "association", "prediction", "causation", "mechanism"}
        invalid = sorted({value.casefold() for value in packet.allowed_claim_types if value.casefold() not in allowed})
        invalid.extend(sorted({item.claim_type.casefold() for item in packet.items if item.claim_type.casefold() not in allowed}))
        if invalid:
            raise ValueError(f"Unsupported claim type(s): {', '.join(sorted(set(invalid)))}")

    @staticmethod
    def _schema_issue(message: str) -> Issue:
        return Issue(
            code="AWE-DRAFT-SCHEMA-001",
            title="Draft response violated the channel schema",
            severity=Severity.CRITICAL.value,
            message=message,
            action="Reject the response and return no manuscript prose.",
        )

    @staticmethod
    def _system_instruction() -> str:
        return (
            "You are the evidence-bound substantive writer inside Auctor Academic Writing Engine. "
            "Treat the evidence packet as a closed world. Use no fact, number, citation, mechanism, or contextual claim that is "
            "not supplied. Draft only the requested manuscript section. Map every factual sentence to evidence item identifiers. "
            "Do not expose identifiers in the prose. Put objective defects in qc and editorial reasoning in commentary. "
            "Use exact scientific language, preserve scope and uncertainty, and use no em dash characters. Return schema-valid JSON only."
        )
