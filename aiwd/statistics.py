"""Built-in named statistics for the skill engine.

Each statistic is a function (WritingSample) -> float | None. None means
"not measurable on this sample" (too short, no data) and the measurement is
skipped rather than scored. Register new statistics — including an external
LM perplexity scorer — with @statistic("name").
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable

from .textmodel import WritingSample

STATISTICS: dict[str, Callable[[WritingSample], float | None]] = {}

MIN_SENTENCES = 5
MIN_TOKENS = 50


def statistic(name: str):
    def deco(fn):
        STATISTICS[name] = fn
        return fn

    return deco


def _mean(xs):
    return sum(xs) / len(xs)


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


@statistic("burstiness_cv")
def burstiness_cv(sample: WritingSample) -> float | None:
    lengths = [len(s.tokens) for s in sample.sentences if s.tokens]
    if len(lengths) < MIN_SENTENCES:
        return None
    mu = _mean(lengths)
    return _stdev(lengths) / mu if mu else None


@statistic("sentence_length_iqr_ratio")
def sentence_length_iqr_ratio(sample: WritingSample) -> float | None:
    lengths = sorted(len(s.tokens) for s in sample.sentences if s.tokens)
    if len(lengths) < MIN_SENTENCES:
        return None
    n = len(lengths)
    q1 = lengths[n // 4]
    q3 = lengths[(3 * n) // 4]
    median = lengths[n // 2]
    return (q3 - q1) / median if median else None


@statistic("mattr")
def mattr(sample: WritingSample, window: int = 50) -> float | None:
    tokens = sample.tokens
    if len(tokens) < MIN_TOKENS:
        return None
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    ratios = []
    counts = Counter(tokens[:window])
    ratios.append(len(counts) / window)
    for i in range(window, len(tokens)):
        out_tok, in_tok = tokens[i - window], tokens[i]
        counts[in_tok] += 1
        counts[out_tok] -= 1
        if counts[out_tok] == 0:
            del counts[out_tok]
        ratios.append(len(counts) / window)
    return _mean(ratios)


@statistic("hapax_rate")
def hapax_rate(sample: WritingSample) -> float | None:
    tokens = sample.tokens
    if len(tokens) < MIN_TOKENS:
        return None
    counts = Counter(tokens)
    return sum(1 for c in counts.values() if c == 1) / len(counts)


@statistic("trigram_repetition")
def trigram_repetition(sample: WritingSample) -> float | None:
    tokens = sample.tokens
    if len(tokens) < MIN_TOKENS:
        return None
    trigrams = [tuple(tokens[i : i + 3]) for i in range(len(tokens) - 2)]
    counts = Counter(trigrams)
    repeated = sum(c for c in counts.values() if c > 1)
    return repeated / len(trigrams)


@statistic("normalized_word_entropy")
def normalized_word_entropy(sample: WritingSample) -> float | None:
    tokens = sample.tokens
    if len(tokens) < MIN_TOKENS:
        return None
    counts = Counter(tokens)
    n = len(tokens)
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
    max_entropy = math.log2(len(counts)) if len(counts) > 1 else 1.0
    return entropy / max_entropy


@statistic("paragraph_length_cv")
def paragraph_length_cv(sample: WritingSample) -> float | None:
    lengths = [p.token_count for p in sample.paragraphs if p.token_count]
    if len(lengths) < 3:
        return None
    mu = _mean(lengths)
    return _stdev(lengths) / mu if mu else None


@statistic("sentence_opener_repetition")
def sentence_opener_repetition(sample: WritingSample) -> float | None:
    openers = [s.tokens[0] for s in sample.sentences if s.tokens]
    if len(openers) < MIN_SENTENCES:
        return None
    return Counter(openers).most_common(1)[0][1] / len(openers)


@statistic("complex_sentence_rate")
def complex_sentence_rate(sample: WritingSample) -> float | None:
    sentences = sample.sentences
    if len(sentences) < MIN_SENTENCES:
        return None
    subordinators = {
        "because", "although", "though", "whereas", "while", "since",
        "which", "whose", "whereby", "thereby",
    }
    complex_count = 0
    for s in sentences:
        commas = s.text.count(",")
        if commas >= 2 or (commas >= 1 and subordinators & set(s.tokens)):
            complex_count += 1
    return complex_count / len(sentences)


@statistic("first_person_rate")
def first_person_rate(sample: WritingSample) -> float | None:
    tokens = sample.tokens
    if len(tokens) < MIN_TOKENS:
        return None
    fp = {"i", "me", "my", "mine", "we", "us", "our", "ours", "i'm", "i've", "i'd", "i'll", "we're", "we've"}
    return 1000 * sum(1 for t in tokens if t in fp) / len(tokens)


@statistic("contraction_rate")
def contraction_rate(sample: WritingSample) -> float | None:
    if sample.token_count < MIN_TOKENS:
        return None
    hits = re.findall(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", sample.normalized_text)
    return 1000 * len(hits) / sample.token_count


@statistic("curly_quote_ratio")
def curly_quote_ratio(sample: WritingSample) -> float | None:
    curly = sum(sample.text.count(c) for c in "“”‘’")
    straight = sample.text.count('"') + sample.text.count("'")
    total = curly + straight
    return curly / total if total >= 4 else None


@statistic("markdown_intensity")
def markdown_intensity(sample: WritingSample) -> float | None:
    lines = [ln for ln in sample.text.splitlines() if ln.strip()]
    if len(lines) < 5:
        return None
    structural = 0
    for ln in lines:
        s = ln.strip()
        if re.match(r"^#{1,6}\s", s) or re.match(r"^[-*•]\s", s) or re.match(r"^\d+\.\s", s):
            structural += 1
        elif "**" in s or s.endswith(":") and len(s.split()) <= 8:
            structural += 1
    return 100 * structural / len(lines)


@statistic("entity_density_proxy")
def entity_density_proxy(sample: WritingSample) -> float | None:
    if sample.token_count < MIN_TOKENS:
        return None
    hits = 0
    for s in sample.sentences:
        # capitalised words not at sentence start ≈ proper nouns
        for m in re.finditer(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z’']+\b", s.text):
            if m.start() > 0:
                hits += 1
        hits += len(re.findall(r"\b\d[\d,.:%]*\b", s.text))
    return 1000 * hits / sample.token_count


# --------------------------------------------------------------------------
# Structural and register statistics.
#
# These measure shapes rather than words, so they survive paraphrase in a way a
# lexicon cannot: a model told to avoid the word "moreover" will still open three
# sentences in a row with a participle, still keep every bullet the same length,
# and still put a passive where a subject belongs.
# --------------------------------------------------------------------------

@statistic("passive_voice_rate")
def passive_voice_rate(sample: WritingSample) -> float | None:
    """Share of sentences carrying a be-verb plus past participle.

    Deliberately a proxy: no parser, so it counts `be` followed within two words
    by an -ed or irregular participle. Over-counts adjectival participles
    ("is interested"), which is why the baseline sd is wide.
    """
    if sample.token_count < MIN_TOKENS:
        return None
    rx = re.compile(
        r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?"
        r"(?:\w+ed|done|made|given|taken|seen|known|shown|held|built|found|"
        r"kept|left|sent|told|written|driven|drawn|chosen|brought)\b",
        re.IGNORECASE)
    hits = sum(1 for s in sample.sentences if rx.search(s.text))
    return 100 * hits / max(1, len(sample.sentences))


@statistic("sentence_initial_participle_rate")
def sentence_initial_participle_rate(sample: WritingSample) -> float | None:
    """Sentences opening with a participial or gerund phrase.

    "Building on this, ..." / "Having established X, ..." A model reaches for this
    to manufacture connective tissue between sentences that are not connected.
    """
    if len(sample.sentences) < 5:
        return None
    rx = re.compile(r"^\s*(?:\w+ing|\w+ed|having|being|given|based|driven|drawing)\b[^,]{0,60},",
                    re.IGNORECASE)
    hits = sum(1 for s in sample.sentences if rx.match(s.text))
    return 100 * hits / len(sample.sentences)


@statistic("nominalisation_suffix_density")
def nominalisation_suffix_density(sample: WritingSample) -> float | None:
    """Distinct nominalisations per 1000 tokens, counted once per lemma.

    Counts each nominalised stem once rather than every occurrence, so naming a
    subject repeatedly — "immunisation" in an immunisation report — does not
    register as abstraction. That is the difference between this and
    AbstractNounDensity, which counts every hit.
    """
    if sample.token_count < MIN_TOKENS:
        return None
    stems = {m.group(0).lower().rstrip("s")
             for m in re.finditer(r"\b\w{5,}(?:tion|ment|ness|ity|ance|ence|ship|ism)s?\b",
                                  sample.normalized_text)}
    return 1000 * len(stems) / sample.token_count


@statistic("numeric_specificity")
def numeric_specificity(sample: WritingSample) -> float | None:
    """Numbers, dates, percentages and units per 1000 tokens.

    The counterweight to abstraction: prose that names quantities is doing work
    that generic synthesis cannot fake. Low is the AI-ish direction.
    """
    if sample.token_count < MIN_TOKENS:
        return None
    hits = len(re.findall(
        r"\b\d+(?:[.,]\d+)?\s*(?:%|pp|per cent|percent|million|billion|thousand|"
        r"years?|months?|days?|hours?|minutes?|kg|km|m|usd|\$)?\b",
        sample.normalized_text, re.IGNORECASE))
    hits += len(re.findall(r"\b(?:19|20)\d{2}\b", sample.normalized_text))
    return 1000 * hits / sample.token_count


@statistic("adjective_tricolon_rate")
def adjective_tricolon_rate(sample: WritingSample) -> float | None:
    """Three coordinated adjectives before a noun: "fast, reliable, and scalable".

    Distinct from TricolonListFrequency, which catches any three-item list. This
    catches the adjective stack specifically, which is the marketing tell.
    """
    if sample.token_count < MIN_TOKENS:
        return None
    rx = re.compile(r"\b\w+(?:ive|ous|ful|able|ible|ing|ent|ant|al|ic)\s*,\s*"
                    r"\w+(?:ive|ous|ful|able|ible|ing|ent|ant|al|ic)\s*,?\s+and\s+"
                    r"\w+(?:ive|ous|ful|able|ible|ing|ent|ant|al|ic)\b", re.IGNORECASE)
    return 1000 * len(rx.findall(sample.normalized_text)) / sample.token_count


@statistic("list_item_length_uniformity")
def list_item_length_uniformity(sample: WritingSample) -> float | None:
    """Coefficient of variation of bullet length; low means machine-even bullets.

    Human lists are ragged because the items are different sizes. A generated list
    tends to arrive pre-balanced.
    """
    items = [ln.strip() for ln in sample.text.splitlines()
             if re.match(r"^\s*(?:[-*•]|\d+\.)\s+\S", ln)]
    if len(items) < 4:
        return None
    lens = [len(i.split()) for i in items]
    m = _mean(lens)
    if not m:
        return None
    return _stdev(lens) / m


@statistic("bold_lead_in_bullet_rate")
def bold_lead_in_bullet_rate(sample: WritingSample) -> float | None:
    """Share of bullets shaped "**Label.** explanation" — a strong format tell."""
    items = [ln.strip() for ln in sample.text.splitlines()
             if re.match(r"^\s*(?:[-*•]|\d+\.)\s+\S", ln)]
    if len(items) < 4:
        return None
    rx = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+\*\*[^*]{2,60}\*\*\s*[:.—-]?")
    return 100 * sum(1 for i in items if rx.match(i)) / len(items)


@statistic("title_case_heading_rate")
def title_case_heading_rate(sample: WritingSample) -> float | None:
    """Share of markdown headings in Title Case Rather Than Sentence case."""
    heads = [ln.strip().lstrip("#").strip()
             for ln in sample.text.splitlines() if re.match(r"^\s*#{1,6}\s+\S", ln)]
    if len(heads) < 3:
        return None
    def is_title_case(h: str) -> bool:
        words = [w for w in h.split() if len(w) > 3]
        if len(words) < 2:
            return False
        caps = sum(1 for w in words if w[:1].isupper())
        return caps / len(words) >= 0.8
    return 100 * sum(1 for h in heads if is_title_case(h)) / len(heads)


@statistic("concrete_case_density")
def concrete_case_density(sample: WritingSample) -> float | None:
    """Sentences carrying a concrete case, per 100 sentences.

    Replaces the marker-phrase counter. A case is a sentence that names something
    checkable — a proper noun, a quantity, a date, a currency amount — not one that
    says "for example". Writing "Mozambique, at a 49-point gap" is a case; writing
    "such as several countries" is not, and the old feature scored it the other way
    round.

    Discourse markers still count, but only when something checkable follows in the
    same sentence, so "for example, Nigeria" scores and a bare "such as" does not.
    """
    if len(sample.sentences) < 4:
        return None
    proper = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z’']{2,}\b")
    quantity = re.compile(
        r"\b\d+(?:[.,]\d+)?\s*(?:%|pp|per cent|percent|million|billion|thousand)?\b"
        r"|\b(?:19|20)\d{2}\b|[$€£]\s?\d")
    marker = re.compile(r"\b(?:for example|for instance|such as|e\.g\.|in one case)\b",
                        re.IGNORECASE)
    hits = 0
    for s in sample.sentences:
        body = s.text.strip()
        has_named = bool(proper.search(body)) or bool(quantity.search(body))
        if has_named:
            hits += 1
        elif marker.search(body):
            # A marker with nothing checkable after it is padding, not a case.
            continue
    return 100 * hits / len(sample.sentences)
