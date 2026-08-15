from __future__ import annotations

from typing import Any

from .knowledge import load_yaml
from .utils import as_list, flatten_strings, normalize_key, unique_preserve


class DebateResolver:
    """Surface methodological disagreement without manufacturing false consensus."""

    def __init__(self) -> None:
        registry = load_yaml("debate_registry")
        self.status_definitions = registry.get("status_definitions", {})
        self.debates = registry.get("debates", {})

    def relevant(self, reconstructed_problem: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
        tokens = set()
        for item in flatten_strings({"problem": reconstructed_problem, "manifest": manifest}):
            tokens.update(normalize_key(part) for part in item.split() if part)
        methods = set(as_list(reconstructed_problem.get("methods")))
        task = reconstructed_problem.get("task")
        domain_hints = set(methods)
        domain_hints.add(str(task or ""))
        selected: list[tuple[int, str, dict[str, Any]]] = []
        for debate_id, profile in self.debates.items():
            searchable = " ".join(
                [
                    debate_id,
                    str(profile.get("title", "")),
                    str(profile.get("domain", "")),
                    str(profile.get("question", "")),
                    " ".join(profile.get("invalid_shortcuts", [])),
                ]
            )
            normalized = set(normalize_key(part) for part in searchable.split())
            score = len(tokens & normalized)
            if any(hint and hint in searchable for hint in domain_hints):
                score += 3
            if debate_id in {
                "p_value_as_evidence_threshold",
                "confidence_interval_interpretation",
                "null_result_vs_absence",
            }:
                score += 1
            if score > 0:
                selected.append((score, debate_id, profile))
        selected.sort(key=lambda item: (-item[0], item[1]))
        output: list[dict[str, Any]] = []
        for score, debate_id, profile in selected[:15]:
            output.append(
                {
                    "debate_id": debate_id,
                    "title": profile.get("title"),
                    "status": profile.get("status"),
                    "status_definition": self.status_definitions.get(profile.get("status")),
                    "question": profile.get("question"),
                    "consensus_floor": profile.get("consensus_floor"),
                    "legitimate_positions": profile.get("legitimate_positions", []),
                    "invalid_shortcuts": profile.get("invalid_shortcuts", []),
                    "decision_questions": profile.get("decision_questions", []),
                    "source_ids": profile.get("source_ids", []),
                    "relevance_score": score,
                }
            )
        return output

    def classify_shortcuts(self, shortcuts: list[str]) -> list[dict[str, Any]]:
        normalized = set(normalize_key(item) for item in shortcuts)
        findings: list[dict[str, Any]] = []
        for debate_id, profile in self.debates.items():
            invalid = set(normalize_key(item) for item in profile.get("invalid_shortcuts", []))
            matched = sorted(normalized & invalid)
            if matched:
                findings.append(
                    {
                        "debate_id": debate_id,
                        "status": profile.get("status"),
                        "matched_shortcuts": matched,
                        "consensus_floor": profile.get("consensus_floor"),
                    }
                )
        return findings
