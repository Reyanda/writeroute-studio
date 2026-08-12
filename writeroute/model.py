"""Document model and protected-span masking for WriteRoute.

The editor works on exact character offsets. Quoted examples, blockquotes and code
are masked before style detection so a document does not flag the bad prose it is
teaching. The mask preserves text length and newlines, so every reported span still
points into the original document.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from aiwd.textmodel import Paragraph, Sentence, WritingSample, parse


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    text: str = ""

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


@dataclass
class Document:
    text: str
    sample: WritingSample
    protected: list[Span] = field(default_factory=list)
    reported_voice: list[Span] = field(default_factory=list)

    @property
    def masked_text(self) -> str:
        """The document with protected spans blanked, offsets and newlines preserved.

        Protected content is replaced with NUL rather than a space. A space made a
        fenced code block into one enormous whitespace run, and every pattern that
        quantifies whitespace then had to divide that run — a 40 KB block cost nearly
        three seconds across the pattern pass, and larger ones did not finish. NUL is
        neither whitespace nor a word character, so it reads to every pattern as
        opaque non-prose, which is what a masked span actually is.
        """
        chars = list(self.text)
        for span in self.protected:
            for i in range(span.start, span.end):
                if chars[i] != "\n":
                    chars[i] = MASK_CHAR
        return "".join(chars)

    @property
    def protected_coverage(self) -> float:
        """Fraction of the document that masking removed from the audit.

        Spans are merged, so a chain of fences, indented blocks and quotations can
        collapse into one span covering nearly everything; a document in the benchmark
        corpus reached 87,973 of 88,283 characters. An audit of that document describes
        the mask, not the prose, so the verdict has to say so rather than report clean.
        """
        if not self.text:
            return 0.0
        return sum(s.end - s.start for s in self.protected) / len(self.text)

    def is_protected(self, start: int, end: int) -> bool:
        return any(s.overlaps(start, end) for s in self.protected)

    def is_reported_voice(self, start: int, end: int) -> bool:
        return any(s.overlaps(start, end) for s in self.reported_voice)

    def sentence_for_span(self, start: int, end: int) -> Sentence | None:
        for sentence in self.sample.sentences:
            if sentence.start <= start and end <= sentence.end:
                return sentence
        return None

    def paragraph_for_span(self, start: int, end: int) -> Paragraph | None:
        for paragraph in self.sample.paragraphs:
            if paragraph.start <= start and end <= paragraph.end:
                return paragraph
        return None


MASK_CHAR = "\x00"

_FENCED_CODE = re.compile(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", re.DOTALL)
# A table row is not a sentence. Journal supplements and extracted DOCX tables arrive
# as pipe- or tab-delimited rows, and auditing a cell as prose produced the largest
# single false-positive family in the benchmark: "Improved WASH | 0.39 | 0.15" read as
# a causal claim, twenty of eighty-four sampled hard findings.
#
# Two signals, because one is not enough. A line with three or more cells is a row on
# its own. A two-column row is only a row when it sits in a run with another one —
# tables come in blocks, and that run requirement is what keeps an isolated prose
# pipe ("the A|B ratio") from masking a whole paragraph.
_MIN_RUN = 2


def _cell_count(line: str) -> int:
    stripped = line.rstrip("\n")
    if "\t" in stripped:
        return len(stripped.split("\t"))
    if " | " in stripped:
        return len(stripped.split(" | "))
    return 1


def find_table_rows(text: str) -> list[Span]:
    lines = text.splitlines(keepends=True)
    counts = [_cell_count(line) for line in lines]
    offsets: list[int] = []
    at = 0
    for line in lines:
        offsets.append(at)
        at += len(line)

    spans: list[Span] = []
    index = 0
    while index < len(lines):
        if counts[index] < 2:
            index += 1
            continue
        run_end = index
        while run_end + 1 < len(lines) and counts[run_end + 1] >= 2:
            run_end += 1
        run_length = run_end - index + 1
        if run_length >= _MIN_RUN or counts[index] >= 3:
            start = offsets[index]
            end = offsets[run_end] + len(lines[run_end])
            spans.append(Span(start, end, "table_row", text[start:end]))
        index = run_end + 1
    return spans
_INLINE_CODE = re.compile(r"(?<!`)`[^`\n]+`(?!`)")
_BLOCKQUOTE = re.compile(r"(?m)^\s*>.*(?:\n|$)")
_INDENTED_CODE = re.compile(r"(?m)^(?: {4}|\t)\S.*(?:\n|$)")
_CURLY_DOUBLE = re.compile(r"“[^”\n]{2,}”")
_CURLY_SINGLE = re.compile(r"‘[^’\n]{2,}’")
_STRAIGHT_DOUBLE = re.compile(r'"[^"\n]{2,}"')

# A sentence reporting another source is not automatically the current author's
# voice. We do not mask it completely, because the framing may still be poor; we
# mark it so findings can be downgraded and never silently rewritten.
_REPORTED_CUES = re.compile(
    r"\b(?:according to|as reported by|as described by|as stated by|"
    r"(?:the\s+)?(?:author|authors|report|paper|study|review|court|committee|"
    r"agency|minister|company|team)\s+(?:said|says|wrote|writes|argued|argues|"
    r"reported|reports|described|describes|claimed|claims|concluded|concludes))\b",
    re.IGNORECASE,
)


def _merge_spans(spans: Iterable[Span]) -> list[Span]:
    ordered = sorted(spans, key=lambda s: (s.start, -s.end))
    if not ordered:
        return []
    merged: list[Span] = [ordered[0]]
    for span in ordered[1:]:
        prev = merged[-1]
        if span.start <= prev.end:
            end = max(prev.end, span.end)
            kinds = "+".join(sorted(set(prev.kind.split("+") + span.kind.split("+"))))
            merged[-1] = Span(prev.start, end, kinds)
        else:
            merged.append(span)
    return merged


def find_protected_spans(text: str, include_quoted: bool = False) -> list[Span]:
    spans: list[Span] = []
    patterns = [
        ("fenced_code", _FENCED_CODE),
        ("inline_code", _INLINE_CODE),
        ("blockquote", _BLOCKQUOTE),
        ("indented_code", _INDENTED_CODE),
    ]
    if not include_quoted:
        patterns.extend([
            ("quotation", _CURLY_DOUBLE),
            ("quotation", _CURLY_SINGLE),
            ("quotation", _STRAIGHT_DOUBLE),
        ])
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            spans.append(Span(match.start(), match.end(), kind, match.group(0)))
    spans.extend(find_table_rows(text))
    return _merge_spans(spans)


def find_reported_voice(sample: WritingSample) -> list[Span]:
    spans: list[Span] = []
    for sentence in sample.sentences:
        if _REPORTED_CUES.search(sentence.text):
            spans.append(Span(sentence.start, sentence.end, "reported_voice", sentence.text))
    return spans


def build_document(text: str, include_quoted: bool = False) -> Document:
    sample = parse(text)
    return Document(
        text=text,
        sample=sample,
        protected=find_protected_spans(text, include_quoted=include_quoted),
        reported_voice=find_reported_voice(sample),
    )


def clean_spacing(text: str) -> str:
    """Repair punctuation and spacing after a local deletion or substitution."""
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"(?m)^\s+", "", text)
    # A deleted sentence opener can leave punctuation at the start.
    text = re.sub(r"(^|\n)(?:[,;:]\s*)+", r"\1", text)
    return text


def capitalise_after_deletion(original: str, replacement: str) -> str:
    """Preserve sentence-initial capitalisation for a span replacement."""
    if not replacement:
        return replacement
    if original[:1].isupper() and replacement[:1].islower():
        return replacement[0].upper() + replacement[1:]
    return replacement
