from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SEVERITY_ORDER = {"fatal": 5, "critical": 4, "major": 3, "minor": 2, "query": 1, "info": 0}
EPISTEMIC_CLASSES = {
    "known_error",
    "consensus_requirement",
    "context_dependent",
    "active_debate",
    "not_identifiable",
    "not_assessable",
    "informational",
}


@dataclass(slots=True)
class ReviewContext:
    """Input supplied to the reviewer.

    The manifest is the authoritative structured description. Manuscript text is
    optional and is used for reporting and wording checks. Data summaries or
    executable result objects can be attached under artifacts.
    """

    manifest: dict[str, Any] = field(default_factory=dict)
    manuscript_text: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    mode: str = "full"
    exhaustive: bool = True
    source_name: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReviewContext":
        return cls(
            manifest=dict(value.get("manifest", value)),
            manuscript_text=str(value.get("manuscript_text", "")),
            artifacts=dict(value.get("artifacts", {})),
            mode=str(value.get("mode", "full")),
            exhaustive=bool(value.get("exhaustive", True)),
            source_name=value.get("source_name"),
        )


@dataclass(slots=True)
class ReviewFinding:
    rule_id: str
    title: str
    domain: str
    severity: str
    epistemic_status: str
    status: str = "open"
    confidence: str = "high"
    location: str | None = None
    observed: Any = None
    expected: Any = None
    rationale: str = ""
    repair: str = ""
    evidence_excerpt: str | None = None
    source_ids: list[str] = field(default_factory=list)
    channels: tuple[str, ...] = ("qc", "commentary")
    manual_review: bool = False
    interaction_of: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"Unknown severity: {self.severity}")
        if self.epistemic_status not in EPISTEMIC_CLASSES:
            raise ValueError(f"Unknown epistemic status: {self.epistemic_status}")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["channels"] = list(self.channels)
        return value


@dataclass(slots=True)
class ReviewGate:
    gate_id: str
    name: str
    passed: bool
    rationale: str
    blocking_rule_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReviewReport:
    engine: str = "STATS-BRAIN"
    version: str = "1.0.0"
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_name: str | None = None
    reconstructed_problem: dict[str, Any] = field(default_factory=dict)
    findings: list[ReviewFinding] = field(default_factory=list)
    gates: list[ReviewGate] = field(default_factory=list)
    dimension_scores: dict[str, int] = field(default_factory=dict)
    debate_notes: list[dict[str, Any]] = field(default_factory=list)
    not_assessable: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def release_status(self) -> str:
        if any(not gate.passed for gate in self.gates):
            return "blocked"
        if any(f.severity in {"fatal", "critical", "major"} and f.status == "open" for f in self.findings):
            return "author_review_required"
        if any(f.status == "open" for f in self.findings):
            return "minor_revision"
        return "ready"

    def sorted_findings(self) -> list[ReviewFinding]:
        return sorted(
            self.findings,
            key=lambda item: (-SEVERITY_ORDER[item.severity], item.domain, item.rule_id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "version": self.version,
            "generated_at": self.generated_at,
            "source_name": self.source_name,
            "release_status": self.release_status,
            "reconstructed_problem": self.reconstructed_problem,
            "dimension_scores": self.dimension_scores,
            "gates": [gate.to_dict() for gate in self.gates],
            "findings": [finding.to_dict() for finding in self.sorted_findings()],
            "debate_notes": self.debate_notes,
            "not_assessable": self.not_assessable,
            "metadata": self.metadata,
        }

    def add_findings(self, findings: Sequence[ReviewFinding]) -> None:
        self.findings.extend(findings)
