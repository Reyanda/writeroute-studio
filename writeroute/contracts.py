"""Bounded writing contracts for author-mode drafting and preservation-first revision."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .audit import AuditReport
from .genres import GenreProfile
from .integrity import protected_terms


@dataclass
class WritingBrief:
    genre: str
    audience: str
    purpose: str
    reader_action: str = ""
    evidence: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    voice_notes: tuple[str, ...] = ()
    length: str = ""
    language: str = "English"

    @classmethod
    def create(
        cls,
        *,
        genre: str,
        audience: str,
        purpose: str,
        reader_action: str = "",
        evidence: Iterable[str] = (),
        constraints: Iterable[str] = (),
        voice_notes: Iterable[str] = (),
        length: str = "",
        language: str = "English",
    ) -> "WritingBrief":
        return cls(
            genre=genre,
            audience=audience,
            purpose=purpose,
            reader_action=reader_action,
            evidence=tuple(evidence),
            constraints=tuple(constraints),
            voice_notes=tuple(voice_notes),
            length=length,
            language=language,
        )


def _lines(values: Iterable[str], empty: str = "- none supplied") -> str:
    rows = [f"- {value}" for value in values if value and value.strip()]
    return "\n".join(rows) if rows else empty


def compile_draft_contract(brief: WritingBrief, genre: GenreProfile) -> str:
    return f"""AUTHOR-MODE WRITING CONTRACT

DOCUMENT JOB
- Genre: {genre.name} ({genre.id})
- Audience: {brief.audience or genre.audience}
- Purpose: {brief.purpose or genre.purpose}
- Reader should think, decide or do: {brief.reader_action or 'infer from the stated purpose'}
- Language: {brief.language}
- Length: {brief.length or 'only as long as the document job requires'}

EVIDENCE BOUNDARY
{_lines(brief.evidence, '- no evidence supplied; do not invent facts, quotations, statistics, sources or citations')}

HARD CONSTRAINTS
{_lines(brief.constraints)}

VOICE SIGNALS
{_lines(brief.voice_notes, '- restrained, direct and specific; preserve any supplied author sample over generic polish')}

REQUIRED RHETORICAL MOVES
{_lines(genre.required_moves)}

GENRE RULES
{_lines(genre.prompt_rules)}

PROHIBITIONS
- Write as the author, not as an assistant helping the author.
- Return only the finished document. No preface, rationale, checklist, change log, self-evaluation or closing offer.
- Do not announce importance, quality, clarity, rigour or comprehensiveness. Show the evidence, test, mechanism or consequence.
- Do not use generic scaffolds, faux insight, dramatic reveals, business slogans or summary recaps.
- Do not add a caveat unless it changes the scope, certainty, safety or interpretation of a claim.
- Do not manufacture symmetry. Paragraphs and sentences should follow the argument, not a template.
- Do not invent facts, quotations, statistics, examples, sources or citations. Mark a necessary unresolved fact as [AUTHOR INPUT: ...].

OUTPUT
Return only the final {genre.name.lower()} text."""


def compile_revision_contract(
    text: str,
    report: AuditReport,
    genre: GenreProfile,
    *,
    voice_notes: Iterable[str] = (),
) -> str:
    defect_rows: list[str] = []
    for finding in report.findings[:40]:
        quote = finding.original.replace("\n", " ").strip()
        if len(quote) > 160:
            quote = quote[:157] + "..."
        defect_rows.append(
            f'- {finding.id} [{finding.severity}] {finding.title}: “{quote}”\n'
            f'  Repair: {finding.action}'
        )
    invariants = protected_terms(text, genre.integrity_policy)
    return f"""PRESERVATION-FIRST REVISION CONTRACT

DOCUMENT JOB
- Genre: {genre.name} ({genre.id})
- Purpose: {genre.purpose}
- Audience: {genre.audience}
- Current editorial burden: {report.editorial_burden:.1f}/100 ({report.status})

DEFECTS TO REPAIR
{chr(10).join(defect_rows) if defect_rows else '- none; return the passage byte-for-byte unchanged'}

HARD MEANING INVARIANTS
The acceptance gate independently checks numbers and units, dates, citations, URLs, quotations, names, acronyms, code identifiers, file paths, headings where required, negation, quantifier scope, comparison direction, causal strength and modal force. Do not alter them.

Protected anchors detected in this passage:
{_lines(invariants[:100], '- no lexical anchors detected; meaning preservation still applies')}

VOICE SIGNALS
{_lines(voice_notes, '- preserve the passage’s vocabulary, cadence, level of polish, uncertainty and useful edge')}

GENRE RULES
{_lines(genre.prompt_rules)}

REVISION METHOD
1. Repair only a named defect or a grammar error exposed by that repair.
2. Make the smallest edit that works. Leave strong sentences untouched.
3. Replace abstraction with a concrete fact only when that fact already exists in the passage.
4. When a finding needs missing evidence, do not invent it. Leave the claim unchanged or insert [AUTHOR INPUT: exact missing item].
5. Preserve source attribution. A report of another author’s wording is not automatically the current author’s claim.
6. Do not improve style by weakening or strengthening certainty, causality, obligation, exclusion or recommendation force.

OUTPUT
Return only the revised passage. No preface, explanation, quality assurance, change log, markdown fence or closing offer."""
