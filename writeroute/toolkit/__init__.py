"""WriteRoute Scientific Toolkit: Formalized Document, Tables, Anti-Slop, Guidelines, and Equations Engine.

Unifies capabilities from academic-tables, no-ai-slop, lucid-sci, reporting-guidelines,
docxml-orchestration, and LaTeX formatting into a clean, modular Python suite.
"""

from __future__ import annotations

from .table_engine import ScientificTableEngine, TableFormatOptions, format_scientific_table
from .slop_engine import ConsolidatedSlopEngine, SlopAuditResult, run_slop_audit
from .guidelines_engine import ReportingGuidelinesEngine, GuidelineAuditReport, run_guideline_audit
from .equations_engine import ScientificEquationEngine, EquationRenderResult

__all__ = [
    "ScientificTableEngine",
    "TableFormatOptions",
    "format_scientific_table",
    "ConsolidatedSlopEngine",
    "SlopAuditResult",
    "run_slop_audit",
    "ReportingGuidelinesEngine",
    "GuidelineAuditReport",
    "run_guideline_audit",
    "ScientificEquationEngine",
    "EquationRenderResult",
]
