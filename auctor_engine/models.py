from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class Channel(str, Enum):
    """Output channels with strict destination rules."""

    SUBSTANTIVE = "substantive"
    QC = "qc"
    COMMENTARY = "commentary"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TextAnchor:
    paragraph_index: int | None = None
    start: int | None = None
    end: int | None = None
    section: str = "other"
    quote: str = ""


@dataclass
class Issue:
    code: str
    title: str
    severity: str
    channel: str = Channel.QC.value
    message: str = ""
    evidence: str = ""
    action: str = ""
    anchor: TextAnchor = field(default_factory=TextAnchor)
    confidence: float = 1.0
    source: str = "auctor"
    auto_fixable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value


@dataclass
class RevisionProposal:
    target: str
    replacement: str
    reason: str
    code: str
    paragraph_index: int | None = None
    section: str = "other"
    change_class: str = "copyedit"
    confidence: float = 1.0
    fact_change_authorized: bool = False
    commentary: str = ""
    priority: int = 50
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FactLedger:
    numbers: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)
    percentages: list[str] = field(default_factory=list)
    effect_measures: list[str] = field(default_factory=list)
    statistical_expressions: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    registry_ids: list[str] = field(default_factory=list)
    cross_references: list[str] = field(default_factory=list)
    directionality: list[str] = field(default_factory=list)
    negation_markers: list[str] = field(default_factory=list)
    claim_force: list[str] = field(default_factory=list)
    abbreviations: list[str] = field(default_factory=list)
    named_terms: list[str] = field(default_factory=list)
    immutable_phrases: list[str] = field(default_factory=list)
    immutable_phrase_occurrences: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManuscriptProfile:
    name: str = "Auctor default scientific manuscript"
    body_font: str = "Times New Roman"
    body_size_pt: float = 11.0
    table_font: str = "Times New Roman"
    table_size_pt: float = 8.0
    language: str = "en-GB"
    page_size: str = "A4"
    margin_cm: float = 2.54
    line_spacing: float = 1.5
    zero_em_dash: bool = True
    preserve_existing_comments: bool = True
    preserve_existing_revisions: bool = True
    use_track_changes: bool = True
    add_editorial_comments: bool = True
    editor_name: str = "Auctor Academic Writing Engine"
    editor_initials: str = "AWE"
    section_contracts: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)




@dataclass(frozen=True)
class EvidenceItem:
    id: str
    content: str
    kind: str = "finding"
    source_key: str = ""
    section: str = "other"
    claim_type: str = "descriptive"
    immutable: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidencePacket:
    section: str
    objective: str
    items: list[EvidenceItem]
    audience: str = "scientific peer reviewers"
    target_journal: str = ""
    allowed_claim_types: list[str] = field(default_factory=lambda: ["descriptive", "association"])
    constraints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvidencePacket":
        raw_items = value.get("items", [])
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("An evidence packet requires a non-empty items array.")
        items: list[EvidenceItem] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                raise ValueError("Each evidence item must be an object.")
            item_id = str(raw.get("id", "")).strip()
            content = str(raw.get("content", "")).strip()
            if not item_id or not content:
                raise ValueError("Each evidence item requires id and content.")
            if item_id in seen:
                raise ValueError(f"Duplicate evidence item id: {item_id}")
            seen.add(item_id)
            metadata = raw.get("metadata", {})
            items.append(
                EvidenceItem(
                    id=item_id,
                    content=content,
                    kind=str(raw.get("kind", "finding")),
                    source_key=str(raw.get("source_key", "")),
                    section=str(raw.get("section", value.get("section", "other"))),
                    claim_type=str(raw.get("claim_type", "descriptive")),
                    immutable=bool(raw.get("immutable", True)),
                    metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
                )
            )
        allowed = value.get("allowed_claim_types", ["descriptive", "association"])
        if isinstance(allowed, str):
            allowed = [allowed]
        return cls(
            section=str(value.get("section", "other")),
            objective=str(value.get("objective", "")).strip(),
            items=items,
            audience=str(value.get("audience", "scientific peer reviewers")),
            target_journal=str(value.get("target_journal", "")),
            allowed_claim_types=[str(item) for item in allowed],
            constraints=[str(item) for item in value.get("constraints", [])],
            metadata=dict(value.get("metadata", {})) if isinstance(value.get("metadata", {}), Mapping) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceMapEntry:
    sentence_index: int
    evidence_ids: tuple[str, ...]
    claim_type: str = "descriptive"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChannelBundle:
    substantive: str
    qc: list[Issue] = field(default_factory=list)
    commentary: list[str] = field(default_factory=list)
    revisions: list[RevisionProposal] = field(default_factory=list)
    fact_ledger: FactLedger = field(default_factory=FactLedger)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "substantive": self.substantive,
            "qc": [issue.to_dict() for issue in self.qc],
            "commentary": list(self.commentary),
            "revisions": [revision.to_dict() for revision in self.revisions],
            "fact_ledger": self.fact_ledger.to_dict(),
            "metadata": dict(self.metadata),
        }
