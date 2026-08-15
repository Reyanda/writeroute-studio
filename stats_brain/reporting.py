from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from .models import ReviewReport
from .utils import deep_sanitize


def write_report(report: ReviewReport | Mapping[str, Any], output: str | Path, format: str | None = None) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = report.to_dict() if isinstance(report, ReviewReport) else dict(report)
    value = deep_sanitize(value)
    selected = (format or path.suffix.lstrip(".") or "json").lower()
    if selected == "json":
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    elif selected in {"yaml", "yml"}:
        path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
    elif selected in {"md", "markdown"}:
        path.write_text(render_markdown(value), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported report format: {selected}")
    return path


def render_markdown(value: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# STATS-BRAIN Statistical and Epidemiological Review")
    lines.append("")
    lines.append(f"**Release status:** {value.get('release_status', 'unknown')}")
    lines.append(f"**Generated:** {value.get('generated_at', '')}")
    lines.append("")
    lines.append("## Reconstructed scientific problem")
    lines.append("")
    problem = value.get("reconstructed_problem", {})
    for key in [
        "task", "design", "estimand_id", "target_population", "sampling_target",
        "exposure_or_intervention", "comparator", "outcome", "time_zero", "time_horizon", "methods",
    ]:
        lines.append(f"- **{key.replace('_', ' ').title()}:** {problem.get(key)}")
    lines.append("")
    lines.append("## Release gates")
    lines.append("")
    lines.append("| Gate | Status | Rationale |")
    lines.append("|---|---:|---|")
    for gate in value.get("gates", []):
        status = "PASS" if gate.get("passed") else "BLOCK"
        lines.append(f"| {gate.get('name')} | {status} | {gate.get('rationale', '')} |")
    lines.append("")
    lines.append("## Dimension scores")
    lines.append("")
    lines.append("These are revision-priority scores, not probabilities that the analysis is valid.")
    lines.append("")
    for key, score in value.get("dimension_scores", {}).items():
        lines.append(f"- **{key.replace('_', ' ').title()}:** {score}/100")
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    for finding in value.get("findings", []):
        lines.append(f"### {finding.get('rule_id')} | {finding.get('severity', '').upper()} | {finding.get('title')}")
        lines.append("")
        lines.append(f"- **Domain:** {finding.get('domain')}")
        lines.append(f"- **Epistemic status:** {finding.get('epistemic_status')}")
        if finding.get("location"):
            lines.append(f"- **Location:** {finding.get('location')}")
        if finding.get("observed") is not None:
            lines.append(f"- **Observed:** `{finding.get('observed')}`")
        if finding.get("expected") is not None:
            lines.append(f"- **Expected:** `{finding.get('expected')}`")
        if finding.get("evidence_excerpt"):
            lines.append(f"- **Evidence:** {finding.get('evidence_excerpt')}")
        lines.append(f"- **Why this matters:** {finding.get('rationale')}")
        lines.append(f"- **Required repair:** {finding.get('repair')}")
        if finding.get("source_ids"):
            lines.append(f"- **Source IDs:** {', '.join(finding.get('source_ids', []))}")
        lines.append("")
    if value.get("debate_notes"):
        lines.append("## Relevant methodological debates")
        lines.append("")
        for debate in value.get("debate_notes", []):
            lines.append(f"### {debate.get('title')}")
            lines.append("")
            lines.append(f"**Status:** {debate.get('status')}")
            lines.append("")
            lines.append(str(debate.get("consensus_floor", "")))
            lines.append("")
            questions = debate.get("decision_questions", [])
            if questions:
                lines.append("Decision questions:")
                for question in questions:
                    lines.append(f"- {question}")
                lines.append("")
    if value.get("not_assessable"):
        lines.append("## Not assessable from supplied material")
        lines.append("")
        for item in value.get("not_assessable", []):
            lines.append(f"- **{item.get('item', 'item')}:** {item.get('reason', '')}")
    return "\n".join(lines).rstrip() + "\n"
