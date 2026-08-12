"""Genre-aware formatting advice.

Lives in the engine rather than in the web layer because two front ends consume it:
the local FastAPI server and the browser build running this same package under
Pyodide. Keeping one copy is the point — the previous release had already grown two
independent catalogues of the same editorial vocabulary.
"""
from __future__ import annotations

import re
from typing import Any

from .genres import get_genre


def formatting_advice(text: str, genre: str) -> dict[str, Any]:
    g = get_genre(genre)
    words = re.findall(r"\b[\w’'-]+\b", text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    long_paras = sum(len(re.findall(r"\b\w+\b", p)) > 180 for p in paragraphs)
    long_sents = sum(len(re.findall(r"\b\w+\b", s)) > 35 for s in re.split(r"(?<=[.!?])\s+", text) if s.strip())
    heading_count = len(re.findall(r"(?m)^#{1,6}\s+|^[A-Z][^\n]{0,72}:$", text))
    bullets = len(re.findall(r"(?m)^\s*[-*•]\s+", text))
    profile = {
        "scientific": {
            "label": "Journal manuscript",
            "recommendations": [
                "Use descriptive section headings that match the target journal's article structure.",
                "Keep results paragraphs claim-led: estimate first, uncertainty immediately after, interpretation later.",
                "Reserve bold and italics for journal-permitted functions, not emphasis.",
                "Keep tables and figures referenced in sequence and avoid repeating their full contents in prose.",
            ],
        },
        "systematic-review": {
            "label": "Evidence synthesis",
            "recommendations": [
                "Keep methods reproducible with explicit databases, dates, eligibility rules and appraisal methods.",
                "Use structured result blocks for study flow, characteristics, effect estimates and certainty.",
                "Separate evidence description from causal or policy interpretation.",
            ],
        },
        "policy-brief": {
            "label": "Policy brief",
            "recommendations": [
                "Lead with the decision, then the evidence that justifies it.",
                "Use short sections, informative headings and compact bullets only where scanning matters.",
                "Give each recommendation an owner, action and implementation condition where evidence supports it.",
            ],
        },
        "professional-report": {
            "label": "Professional report",
            "recommendations": [
                "Use a decision-first executive summary and a clear hierarchy of findings, implications and actions.",
                "Keep paragraphs visually compact and convert dense inventories into tables or structured lists.",
                "Use consistent heading levels; avoid decorative micro-headings over one or two sentences.",
            ],
        },
        "grant": {
            "label": "Grant proposal",
            "recommendations": [
                "Make need, gap, approach, feasibility and measurable outcome visually distinct.",
                "Keep claims of novelty and impact tied to evidence or explicit assumptions.",
                "Use tables for workplans, milestones, risks and budgets when allowed.",
            ],
        },
        "legal": {
            "label": "Legal prose",
            "recommendations": [
                "Preserve defined terms, modal force, exceptions and cross-references exactly.",
                "Use numbered headings and paragraphs where the document is operational or review-heavy.",
                "Prefer short propositions but never simplify language in a way that changes legal scope.",
            ],
        },
        "technical": {
            "label": "Technical documentation",
            "recommendations": [
                "Separate concept, prerequisite, procedure, example and failure-state content.",
                "Use code blocks for literal commands and tables for stable parameter references.",
                "Prefer task-based headings that tell the reader what they can accomplish.",
            ],
        },
        "email": {
            "label": "Professional correspondence",
            "recommendations": [
                "Keep the opening functional, place the request early, and make the next action unambiguous.",
                "Use bullets only for genuinely parallel items; otherwise keep the note conversational.",
            ],
        },
        "essay": {
            "label": "Essay or commentary",
            "recommendations": [
                "Let argument structure drive headings; do not turn each paragraph into a labelled module.",
                "Vary paragraph length with the argument and end on the strongest substantive point rather than a recap.",
            ],
        },
        "general": {
            "label": "General professional prose",
            "recommendations": [
                "Use informative headings only where they improve navigation.",
                "Keep paragraphs focused on one claim or task and avoid ornamental emphasis.",
                "Use lists for parallel items, not to avoid writing coherent prose.",
            ],
        },
    }
    info = profile.get(g.id, profile["general"])
    diagnostics: list[str] = []
    if long_paras:
        diagnostics.append(f"{long_paras} paragraph(s) exceed 180 words; consider splitting at a change in claim or reader task.")
    if long_sents:
        diagnostics.append(f"{long_sents} sentence(s) exceed 35 words; inspect them for stacked clauses or buried verbs.")
    if len(words) > 900 and heading_count == 0 and g.id in {"policy-brief", "professional-report", "technical", "grant"}:
        diagnostics.append("The document is long enough to benefit from navigational headings.")
    if bullets > 10 and g.id in {"scientific", "essay"}:
        diagnostics.append("List density is high for this genre; convert argumentative bullets into prose unless the venue expects lists.")
    return {
        "genre": g.id,
        "label": info["label"],
        "recommendations": info["recommendations"],
        "diagnostics": diagnostics,
        "metrics": {
            "words": len(words),
            "paragraphs": len(paragraphs),
            "headingsDetected": heading_count,
            "bulletItems": bullets,
            "longParagraphs": long_paras,
            "longSentences": long_sents,
        },
    }
