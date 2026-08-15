from __future__ import annotations

from typing import Any, Mapping

from .models import ReviewReport
from .utils import deep_sanitize


class AuctorBridge:
    """Convert a STATS-BRAIN report into Auctor-safe review channels."""

    @staticmethod
    def packet(report: ReviewReport | Mapping[str, Any]) -> dict[str, Any]:
        value = report.to_dict() if isinstance(report, ReviewReport) else dict(report)
        comments: list[dict[str, Any]] = []
        qc: list[dict[str, Any]] = []
        author_queries: list[dict[str, Any]] = []
        for finding in value.get("findings", []):
            base = {
                "code": finding.get("rule_id"),
                "severity": finding.get("severity"),
                "epistemic_status": finding.get("epistemic_status"),
                "location": finding.get("location"),
                "quote": finding.get("evidence_excerpt"),
                "title": finding.get("title"),
            }
            qc.append(
                {
                    **base,
                    "observed": finding.get("observed"),
                    "expected": finding.get("expected"),
                    "rationale": finding.get("rationale"),
                    "release_blocking": finding.get("severity") in {"fatal", "critical"},
                }
            )
            commentary = finding.get("repair") or "Please clarify the statistical target and analysis."
            comments.append({**base, "commentary": commentary})
            if finding.get("severity") == "query" or finding.get("epistemic_status") in {"not_assessable", "not_identifiable"}:
                author_queries.append({**base, "query": commentary})
        return deep_sanitize(
            {
                "engine": "STATS-BRAIN to Auctor bridge",
                "channel_contract": {
                    "substantive": [],
                    "qc": "Machine-readable statistical defects and release gates",
                    "commentary": "Word comments or editorial response",
                    "author_queries": "Questions that must be answered before a verdict",
                },
                "release_status": value.get("release_status"),
                "substantive": [],
                "qc": qc,
                "commentary": comments,
                "author_queries": author_queries,
                "gates": value.get("gates", []),
            }
        )
