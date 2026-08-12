"""Text model: WritingSample -> Paragraph -> Sentence -> Token.

Mirrors the ontology's TextUnit hierarchy. Offsets are into the original text
so feature measurements can quote evidence spans verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ABBREV = re.compile(
    r"\b(?:Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc|Fig|No|al|approx|cf|Vol|pp|p|e\.g|i\.e)\.$"
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+")
_WORD = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’-]*")


@dataclass
class Sentence:
    text: str
    start: int
    end: int
    tokens: list[str] = field(default_factory=list)


@dataclass
class Paragraph:
    text: str
    start: int
    end: int
    sentences: list[Sentence] = field(default_factory=list)

    @property
    def token_count(self) -> int:
        return sum(len(s.tokens) for s in self.sentences)


@dataclass
class WritingSample:
    text: str
    paragraphs: list[Paragraph] = field(default_factory=list)

    @property
    def sentences(self) -> list[Sentence]:
        return [s for p in self.paragraphs for s in p.sentences]

    @property
    def tokens(self) -> list[str]:
        return [t for s in self.sentences for t in s.tokens]

    @property
    def token_count(self) -> int:
        return len(self.tokens)

    @property
    def normalized_text(self) -> str:
        """Text with curly apostrophes/quotes flattened, same length as original."""
        return (
            self.text.replace("’", "'")
            .replace("‘", "'")
            .replace("“", '"')
            .replace("”", '"')
        )


def _split_sentences(par_text: str, par_start: int) -> list[Sentence]:
    sentences: list[Sentence] = []
    pieces: list[tuple[str, int]] = []
    last = 0
    for m in _SENT_SPLIT.finditer(par_text):
        candidate = par_text[last : m.start() + 1]
        if _ABBREV.search(candidate.rstrip()):
            continue  # split point sits after an abbreviation; keep accumulating
        pieces.append((par_text[last : m.end()], last))
        last = m.end()
    if last < len(par_text):
        pieces.append((par_text[last:], last))
    for chunk, offset in pieces:
        stripped = chunk.strip()
        if not stripped:
            continue
        lead = len(chunk) - len(chunk.lstrip())
        start = par_start + offset + lead
        sentences.append(
            Sentence(
                text=stripped,
                start=start,
                end=start + len(stripped),
                tokens=[t.lower() for t in _WORD.findall(stripped)],
            )
        )
    return sentences


def parse(text: str) -> WritingSample:
    if not text or not text.strip():
        raise ValueError("Cannot parse empty text")
    sample = WritingSample(text=text)
    for m in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]*)*", text):
        chunk = m.group(0)
        if not chunk.strip():
            continue
        par = Paragraph(text=chunk.strip(), start=m.start(), end=m.end())
        par.sentences = _split_sentences(chunk, m.start())
        if par.sentences:
            sample.paragraphs.append(par)
    if not sample.paragraphs:
        raise ValueError("No parseable paragraphs found")
    return sample
