from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FactLedger:
    numbers: list[str] = field(default_factory=list)
    percentages: list[str] = field(default_factory=list)
    confidence_intervals: list[str] = field(default_factory=list)
    p_values: list[str] = field(default_factory=list)
    effect_measures: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    negations: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    table_fig_refs: list[str] = field(default_factory=list)

    @classmethod
    def extract_from_text(cls, text: str) -> FactLedger:
        ledger = cls()
        # Numbers & Decimals
        ledger.numbers = re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", text)
        # Percentages
        ledger.percentages = re.findall(r"\b\d+(?:\.\d+)?%", text)
        # CIs
        ledger.confidence_intervals = re.findall(r"95%\s*C[rI][:\s]+[0-9.]+\s*(?:to|–|-)\s*[0-9.]+", text, re.IGNORECASE)
        # P-values
        ledger.p_values = re.findall(r"\bp\s*[<>=]\s*0?\.\d+\b", text, re.IGNORECASE)
        # Effect measures
        ledger.effect_measures = re.findall(r"\b(?:odds ratio|hazard ratio|risk ratio|relative risk|OR|HR|RR)\b", text, re.IGNORECASE)
        # Directionality
        ledger.directions = re.findall(r"\b(?:increase[ds]?|decrease[ds]?|higher|lower|elevated|reduced|no change|attenuated)\b", text, re.IGNORECASE)
        # Negations
        ledger.negations = re.findall(r"\b(?:not|no|never|neither|nor|without|absence|cannot|unable)\b", text, re.IGNORECASE)
        # Identifiers (NCT, DOI, PMID, PROSPERO)
        ledger.identifiers = re.findall(r"\b(?:NCT\d+|10\.\d{4,9}/[-._;()/:A-Z0-9]+|PMID:\s*\d+|ISRCTN\d+)\b", text, re.IGNORECASE)
        # Citations
        ledger.citations = re.findall(r"\([A-Z][a-z]+(?:\s+et\s+al\.)?,?\s*\d{4}\)|\[\d+\]", text)
        # Table / Figure refs
        ledger.table_fig_refs = re.findall(r"\b(?:Table\s+\d+|Figure\s+\d+|Fig\.\s*\d+|Appendix\s+[A-Z\d]+)\b", text, re.IGNORECASE)
        return ledger

    def verify_invariants(self, candidate_text: str) -> list[str]:
        """Verifies that candidate text does not alter any frozen facts in the ledger."""
        violations = []
        cand_ledger = FactLedger.extract_from_text(candidate_text)

        # 1. Number fidelity: All original numbers must be present
        for num in set(self.numbers):
            if num not in cand_ledger.numbers and num not in candidate_text:
                violations.append(f"Number altered or omitted: '{num}'")

        # 2. Percentage fidelity
        for pct in set(self.percentages):
            if pct not in cand_ledger.percentages and pct not in candidate_text:
                violations.append(f"Percentage altered or omitted: '{pct}'")

        # 3. Effect measure preservation (e.g. no RR -> OR promotion)
        orig_em = {em.upper() for em in self.effect_measures}
        cand_em = {em.upper() for em in cand_ledger.effect_measures}
        if orig_em != cand_em:
            violations.append(f"Effect measure shift detected: {orig_em} vs {cand_em}")

        # 4. Negation preservation
        for neg in set(self.negations):
            if neg.lower() not in candidate_text.lower():
                violations.append(f"Negation removed: '{neg}' (potential inversion of findings)")

        # 5. Identifier preservation
        for ident in set(self.identifiers):
            if ident not in candidate_text:
                violations.append(f"Identifier missing in rewrite: '{ident}'")

        return violations


class ThreeChannelEnforcer:
    """Guarantees strict separation between Substantive, QC, and Commentary channels."""

    QC_PATTERN = re.compile(
        r"(\[?(?:STATS-BRAIN|STYLE-PATTERN|LUCID-SCI|PROSE|CORE|RULE|QC)[^\]]*\]?|"
        r"severity:\s*(?:fatal|critical|major|minor)|"
        r"\b(?:finding\s+code|rule_id|invariant\s+check)\b)",
        re.IGNORECASE,
    )

    ASSISTANT_RESIDUE = re.compile(
        r"(here is the (?:revised|improved|updated) text:?|"
        r"as an ai (?:assistant|model)|"
        r"i have (?:corrected|rewritten|fixed) the following:?)",
        re.IGNORECASE,
    )

    @classmethod
    def sanitize_substantive_channel(cls, text: str) -> str:
        """Strips any leaked QC or assistant commentary from the substantive manuscript prose."""
        cleaned = cls.QC_PATTERN.sub("", text)
        cleaned = cls.ASSISTANT_RESIDUE.sub("", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def check_channel_leakage(cls, substantive_text: str) -> list[str]:
        leaks = []
        if cls.QC_PATTERN.search(substantive_text):
            leaks.append("QC rule codes or severity language leaked into substantive manuscript body.")
        if cls.ASSISTANT_RESIDUE.search(substantive_text):
            leaks.append("AI assistant conversational residue leaked into substantive channel.")
        return leaks


class RevisionAuthority:
    MECHANICAL = "mechanical"
    COPYEDIT = "copyedit"
    SUBSTANTIVE = "substantive"
    DEVELOPMENTAL = "developmental"

    @classmethod
    def validate_action(cls, authority: str, original: str, proposed: str) -> dict[str, Any]:
        """Validates that proposed changes adhere strictly to the declared revision authority."""
        if authority == cls.DEVELOPMENTAL:
            # Developmental review NEVER modifies substantive prose
            if original.strip() != proposed.strip():
                return {
                    "valid": False,
                    "reason": "Developmental authority prohibits changing substantive prose (critique belongs in commentary channel only).",
                }
            return {"valid": True}

        if authority == cls.MECHANICAL:
            # Only whitespace and punctuation may change
            orig_alpha = re.sub(r"[\s\W_]+", "", original)
            prop_alpha = re.sub(r"[\s\W_]+", "", proposed)
            if orig_alpha != prop_alpha:
                return {
                    "valid": False,
                    "reason": "Mechanical authority permits punctuation/formatting fixes only. Words were modified.",
                }
            return {"valid": True}

        if authority in (cls.COPYEDIT, cls.SUBSTANTIVE):
            ledger = FactLedger.extract_from_text(original)
            violations = ledger.verify_invariants(proposed)
            if violations:
                return {
                    "valid": False,
                    "reason": f"Fact Ledger invariant check failed: {'; '.join(violations)}",
                    "violations": violations,
                }
            return {"valid": True}

        return {"valid": True}


def document_artifact_router(artifact_kind: str) -> dict[str, str]:
    """Routes an artifact to its authoritative lead skill and secondary engines."""
    routes = {
        "docx": {
            "lead_skill": "docxml-orchestration",
            "secondary": "docx-xml-engine",
            "citation_engine": "mendeley-citations",
            "writing_doctrine": "auctor-writing-engine",
        },
        "latex": {
            "lead_skill": "latex-export-engine",
            "secondary": "bibtex-parser",
            "writing_doctrine": "auctor-writing-engine",
        },
        "scientific_manuscript": {
            "lead_skill": "auctor-writing-engine",
            "review_engines": ["stats-brain", "pattern-engine-v2", "lucid-sci"],
            "doctrine": "three-channel-immutable-ledger",
        },
    }
    return routes.get(artifact_kind, routes["scientific_manuscript"])
