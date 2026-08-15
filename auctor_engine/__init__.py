"""Auctor Academic Writing Engine.

A production-oriented engine for scientific prose, manuscript quality control,
and direct OOXML revision of Microsoft Word documents.
"""

from .models import (
    Channel,
    ChannelBundle,
    EvidenceItem,
    EvidenceMapEntry,
    EvidencePacket,
    FactLedger,
    Issue,
    ManuscriptProfile,
    RevisionProposal,
)
from .pipeline import AcademicWritingEngine
from .docx_engine import ManuscriptDocxEngine
from .guidelines import ReportingGuidelineRegistry
from .schemas import available_schemas, load_schema

__all__ = [
    "AcademicWritingEngine",
    "Channel",
    "ChannelBundle",
    "EvidenceItem",
    "EvidenceMapEntry",
    "EvidencePacket",
    "FactLedger",
    "Issue",
    "ManuscriptDocxEngine",
    "ManuscriptProfile",
    "RevisionProposal",
    "ReportingGuidelineRegistry",
    "available_schemas",
    "load_schema",
]

__version__ = "1.0.0"
