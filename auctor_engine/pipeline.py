from __future__ import annotations

from importlib.resources import files
from typing import Any, Mapping

import yaml

from .critic import AcademicCritic
from .drafting import EvidenceDrafter
from .guidelines import ReportingGuidelineRegistry
from .models import ChannelBundle, EvidencePacket, Issue, ManuscriptProfile, Severity
from .provider import WritingProvider
from .rewrite import ProviderDevelopmentalReviewer, ProviderRewriter, SafeCopyeditor
from .text_engine import compare_fact_ledgers, extract_fact_ledger


class AcademicWritingEngine:
    """Section-aware academic writing and revision pipeline."""

    def __init__(
        self,
        profile: ManuscriptProfile | None = None,
        *,
        use_negative_engine: bool = True,
    ):
        self.profile = profile or ManuscriptProfile()
        self.critic = AcademicCritic(use_negative_engine=use_negative_engine)
        self.copyeditor = SafeCopyeditor()
        self.guidelines = ReportingGuidelineRegistry()
        ontology_file = files("auctor_engine.data").joinpath("positive_writing_ontology.yaml")
        with ontology_file.open("r", encoding="utf-8") as handle:
            self.ontology = yaml.safe_load(handle)

    def process_text(
        self,
        text: str,
        *,
        section: str = "other",
        mode: str = "copyedit",
        metadata: Mapping[str, Any] | None = None,
        provider: WritingProvider | None = None,
        immutable_phrases: tuple[str, ...] = (),
    ) -> ChannelBundle:
        metadata = dict(metadata or {})
        if mode not in {"mechanical", "copyedit", "substantive", "developmental"}:
            raise ValueError("mode must be mechanical, copyedit, substantive, or developmental")

        original_ledger = extract_fact_ledger(text, immutable_phrases)
        if mode == "mechanical":
            revised, applied, skipped = self.copyeditor.revise(
                text,
                section=section,
                change_classes={"mechanical"},
            )
        elif mode in {"copyedit", "substantive"}:
            revised, applied, skipped = self.copyeditor.revise(text, section=section)
        else:
            revised, applied, skipped = text, [], []

        qc: list[Issue] = []
        commentary = [proposal.commentary or proposal.reason for proposal in applied]

        if provider is not None and mode == "substantive":
            contract = self.writing_contract(section)
            provider_rewriter = ProviderRewriter(provider)
            provider_text, provider_issues, provider_commentary = provider_rewriter.rewrite(
                revised,
                section=section,
                section_contract=contract,
                immutable_phrases=immutable_phrases,
            )
            revised = provider_text
            qc.extend(provider_issues)
            commentary.extend(provider_commentary)
        elif provider is not None and mode == "developmental":
            reviewer = ProviderDevelopmentalReviewer(provider)
            provider_issues, provider_commentary = reviewer.review(
                text,
                section=section,
                writing_contract=self.writing_contract(section),
                immutable_phrases=immutable_phrases,
            )
            qc.extend(provider_issues)
            commentary.extend(provider_commentary)

        revised_ledger = extract_fact_ledger(revised, immutable_phrases)
        fact_issues = compare_fact_ledgers(original_ledger, revised_ledger)
        qc.extend(fact_issues)
        qc.extend(self.critic.audit(revised, section=section, metadata=metadata))
        reporting_profiles = self.guidelines.recommend(metadata)
        if reporting_profiles:
            qc.extend(
                self.guidelines.audit_section(
                    revised,
                    section=section,
                    profile_ids=reporting_profiles,
                )
            )
        for proposal in skipped:
            qc.append(
                Issue(
                    code="AWE-REV-001",
                    title="Revision anchor was not applied",
                    severity=Severity.LOW.value,
                    message=f"The exact target '{proposal.target}' could not be located after earlier revisions.",
                    action="Review the proposal manually.",
                    metadata={"proposal": proposal.to_dict()},
                )
            )

        return ChannelBundle(
            substantive=revised,
            qc=qc,
            commentary=commentary,
            revisions=applied,
            fact_ledger=original_ledger,
            metadata={
                "engine": "Auctor Academic Writing Engine",
                "version": "1.0.0",
                "mode": mode,
                "section": section,
                "reporting_guidelines": reporting_profiles,
                "reporting_guideline_scope": "coverage proxies only; confirm against official checklists",
                "authorship_inference": "not_performed",
                "channel_contract": {
                    "substantive": "publication-ready manuscript prose only",
                    "qc": "verifiable defects and release gates only",
                    "commentary": "editorial reasoning and author queries only",
                },
            },
        )

    def draft_section(
        self,
        packet: EvidencePacket | Mapping[str, Any],
        *,
        provider: WritingProvider,
    ) -> ChannelBundle:
        packet = packet if isinstance(packet, EvidencePacket) else EvidencePacket.from_mapping(packet)
        drafter = EvidenceDrafter(provider)
        substantive, qc, commentary, evidence_map = drafter.draft(
            packet,
            writing_contract=self.writing_contract(packet.section),
        )
        metadata = dict(packet.metadata)
        reporting_profiles = self.guidelines.recommend(metadata)
        if substantive:
            qc.extend(self.critic.audit(substantive, section=packet.section, metadata=metadata))
            if reporting_profiles:
                qc.extend(
                    self.guidelines.audit_section(
                        substantive,
                        section=packet.section,
                        profile_ids=reporting_profiles,
                    )
                )
        allowed_text = "\n".join(item.content for item in packet.items)
        return ChannelBundle(
            substantive=substantive,
            qc=qc,
            commentary=commentary,
            revisions=[],
            fact_ledger=extract_fact_ledger(allowed_text),
            metadata={
                "engine": "Auctor Academic Writing Engine",
                "version": "1.0.0",
                "mode": "evidence_bound_draft",
                "section": packet.section,
                "objective": packet.objective,
                "evidence_map": [entry.to_dict() for entry in evidence_map],
                "evidence_item_ids": [item.id for item in packet.items],
                "reporting_guidelines": reporting_profiles,
                "authorship_inference": "not_performed",
                "channel_contract": {
                    "substantive": "publication-ready manuscript prose only",
                    "qc": "verifiable defects and release gates only",
                    "commentary": "editorial reasoning and author queries only",
                },
            },
        )

    def prepare_draft_request(
        self,
        packet: EvidencePacket | Mapping[str, Any],
    ) -> dict[str, Any]:
        packet = packet if isinstance(packet, EvidencePacket) else EvidencePacket.from_mapping(packet)
        return EvidenceDrafter().prepare_request(packet, writing_contract=self.writing_contract(packet.section))

    def validate_draft_response(
        self,
        packet: EvidencePacket | Mapping[str, Any],
        response: Mapping[str, Any],
    ) -> ChannelBundle:
        packet = packet if isinstance(packet, EvidencePacket) else EvidencePacket.from_mapping(packet)
        drafter = EvidenceDrafter()
        substantive, qc, commentary, evidence_map = drafter.validate_response(
            packet,
            response,
            writing_contract=self.writing_contract(packet.section),
        )
        metadata = dict(packet.metadata)
        reporting_profiles = self.guidelines.recommend(metadata)
        if substantive:
            qc.extend(self.critic.audit(substantive, section=packet.section, metadata=metadata))
            if reporting_profiles:
                qc.extend(self.guidelines.audit_section(substantive, section=packet.section, profile_ids=reporting_profiles))
        return ChannelBundle(
            substantive=substantive,
            qc=qc,
            commentary=commentary,
            revisions=[],
            fact_ledger=extract_fact_ledger("\n".join(item.content for item in packet.items)),
            metadata={
                "engine": "Auctor Academic Writing Engine",
                "version": "1.0.0",
                "mode": "evidence_bound_draft_validation",
                "section": packet.section,
                "objective": packet.objective,
                "evidence_map": [entry.to_dict() for entry in evidence_map],
                "evidence_item_ids": [item.id for item in packet.items],
                "reporting_guidelines": reporting_profiles,
                "authorship_inference": "not_performed",
            },
        )

    def writing_contract(self, section: str) -> Mapping[str, Any]:
        return {
            "section_contract": self.section_contract(section),
            "paragraph_contract": self.ontology.get("paragraph_contract", {}),
            "sentence_contract": self.ontology.get("sentence_contract", {}),
            "right_pattern_library": self.ontology.get("right_pattern_library", []),
            "release_gates": self.ontology.get("release_gates", []),
        }

    def section_contract(self, section: str) -> Mapping[str, Any]:
        contracts = self.ontology.get("section_contracts", {})
        return contracts.get(section, contracts.get("other", {}))
