"""Voice profiles that constrain editing without teaching the tool to imitate slop.

A profile describes measurable habits, not a caricature: sentence-length range,
punctuation, function words, contractions, paragraph shape, lexical diversity and
spelling preference. Lower distance means the candidate remains closer to the
approved samples.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from aiwd.statistics import mattr as _mattr
from aiwd.textmodel import parse


_FUNCTION_WORDS = (
    "a", "an", "the", "and", "but", "or", "because", "although", "while", "if",
    "that", "which", "who", "of", "to", "in", "on", "for", "with", "without",
    "from", "by", "as", "at", "this", "these", "it", "we", "I", "you",
)
_UK_US = (
    ("analyse", "analyze"), ("analysed", "analyzed"), ("organisation", "organization"),
    ("organise", "organize"), ("colour", "color"), ("behaviour", "behavior"),
    ("centre", "center"), ("programme", "program"), ("modelling", "modeling"),
    ("randomised", "randomized"), ("recognise", "recognize"), ("prioritise", "prioritize"),
)
_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst"}


@dataclass
class VoiceProfile:
    name: str
    version: str
    sample_count: int
    word_count: int
    metrics_mean: dict[str, float]
    metrics_sd: dict[str, float]
    function_words: dict[str, float]
    char_trigrams: dict[str, float]
    spelling_preference: str
    source_hashes: list[str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceProfile":
        return cls(**data)


@dataclass
class VoiceDistance:
    score: float
    interpretation: str
    metric_deltas: list[dict]
    profile: str

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 2),
            "interpretation": self.interpretation,
            "metricDeltas": self.metric_deltas,
            "profile": self.profile,
        }


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _sd(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _cv(values: Sequence[float]) -> float:
    mean = _mean(values)
    return _sd(values) / mean if mean else 0.0


def _rate(count: int, total: int, scale: float = 1000.0) -> float:
    return scale * count / max(1, total)


def _char_trigrams(text: str, limit: int = 120) -> dict[str, float]:
    normalized = re.sub(r"\s+", " ", text.casefold())
    grams = Counter(normalized[i:i + 3] for i in range(max(0, len(normalized) - 2)))
    total = sum(grams.values()) or 1
    return {gram: count / total for gram, count in grams.most_common(limit)}


def _metrics(text: str) -> tuple[dict[str, float], dict[str, float], dict[str, float], str]:
    sample = parse(text)
    sentence_lengths = [len(s.tokens) for s in sample.sentences if s.tokens]
    paragraph_lengths = [p.token_count for p in sample.paragraphs if p.token_count]
    words = sample.tokens
    token_count = max(1, len(words))
    normalized = sample.normalized_text
    contractions = re.findall(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", normalized, re.IGNORECASE)
    first_person = sum(1 for w in words if w in {"i", "me", "my", "mine", "we", "us", "our", "ours"})
    sentence_initial_and_but = sum(1 for s in sample.sentences if s.tokens and s.tokens[0] in {"and", "but"})
    metric = {
        "sentence_mean": _mean(sentence_lengths),
        "sentence_sd": _sd(sentence_lengths),
        "sentence_cv": _cv(sentence_lengths),
        "sentence_median": statistics.median(sentence_lengths) if sentence_lengths else 0.0,
        "short_sentence_share": sum(n <= 8 for n in sentence_lengths) / max(1, len(sentence_lengths)),
        "long_sentence_share": sum(n >= 30 for n in sentence_lengths) / max(1, len(sentence_lengths)),
        "paragraph_mean": _mean(paragraph_lengths),
        "paragraph_cv": _cv(paragraph_lengths),
        "mattr": float(_mattr(sample) or (len(set(words)) / token_count)),
        "mean_word_length": _mean([len(w) for w in words]),
        "first_person_per_1000": _rate(first_person, token_count),
        "contractions_per_1000": _rate(len(contractions), token_count),
        "comma_per_1000": _rate(text.count(","), token_count),
        "semicolon_per_1000": _rate(text.count(";"), token_count),
        "colon_per_1000": _rate(text.count(":"), token_count),
        "em_dash_per_1000": _rate(text.count("—"), token_count),
        "parenthesis_per_1000": _rate(text.count("(") + text.count(")"), token_count),
        "question_per_1000": _rate(text.count("?"), token_count),
        "exclamation_per_1000": _rate(text.count("!"), token_count),
        "sentence_initial_and_but_share": sentence_initial_and_but / max(1, len(sentence_lengths)),
    }
    function_counts = Counter(words)
    function = {word: function_counts[word] / token_count for word in _FUNCTION_WORDS}
    uk = sum(len(re.findall(rf"\b{re.escape(left)}\b", normalized, re.IGNORECASE)) for left, _ in _UK_US)
    us = sum(len(re.findall(rf"\b{re.escape(right)}\b", normalized, re.IGNORECASE)) for _, right in _UK_US)
    spelling = "UK" if uk > us else "US" if us > uk else "undetermined"
    return metric, function, _char_trigrams(text), spelling


def _existing_path(value: str | Path) -> Path | None:
    """Resolve a sample path without treating arbitrary prose as a file name."""
    if isinstance(value, Path):
        try:
            return value if value.exists() else None
        except OSError:
            return None
    if not isinstance(value, str) or "\n" in value or "\r" in value or len(value) > 1024:
        return None
    try:
        path = Path(value)
        return path if path.exists() else None
    except (OSError, ValueError):
        return None


def _read_sample_path(path: Path) -> list[str]:
    if path.is_dir():
        return [
            item.read_text(encoding="utf-8")
            for item in sorted(path.rglob("*"))
            if item.is_file() and item.suffix.casefold() in _TEXT_SUFFIXES
        ]
    return [path.read_text(encoding="utf-8")]


def _collect_samples(samples: str | Path | Iterable[str | Path]) -> list[str]:
    direct_path = _existing_path(samples) if isinstance(samples, (str, Path)) else None
    if direct_path is not None:
        texts = _read_sample_path(direct_path)
    elif isinstance(samples, str):
        texts = [samples]
    else:
        texts = []
        for value in samples:
            path = _existing_path(value)
            texts.extend(_read_sample_path(path) if path is not None else [str(value)])
    return [text for text in texts if text and text.strip()]


def build_voice_profile(
    samples: str | Path | Iterable[str | Path],
    *,
    name: str = "default",
    strict: bool = False,
) -> VoiceProfile:
    texts = _collect_samples(samples)
    if not texts:
        raise ValueError("A voice profile needs at least one non-empty sample")
    metric_rows: list[dict[str, float]] = []
    function_rows: list[dict[str, float]] = []
    trigram_rows: list[dict[str, float]] = []
    spellings: list[str] = []
    warnings: list[str] = []

    # Import lazily to avoid a circular dependency at module import time.
    from .audit import audit_text

    clean_texts: list[str] = []
    for index, text in enumerate(texts, 1):
        report = audit_text(text, genre="auto")
        hard = sum(f.severity == "hard" for f in report.findings)
        if hard >= 3 or report.editorial_burden >= 75:
            message = f"sample {index} has high editorial burden ({report.editorial_burden:.1f}; {hard} hard findings)"
            if strict:
                raise ValueError(message + "; strict voice profiling rejected it")
            warnings.append(message)
        clean_texts.append(text)
        metric, function, trigrams, spelling = _metrics(text)
        metric_rows.append(metric)
        function_rows.append(function)
        trigram_rows.append(trigrams)
        spellings.append(spelling)

    metric_names = sorted(metric_rows[0])
    means = {name_: _mean([row[name_] for row in metric_rows]) for name_ in metric_names}
    sds = {name_: _sd([row[name_] for row in metric_rows]) for name_ in metric_names}
    function = {word: _mean([row[word] for row in function_rows]) for word in _FUNCTION_WORDS}
    gram_keys = Counter(g for row in trigram_rows for g in row).most_common(160)
    trigrams = {gram: _mean([row.get(gram, 0.0) for row in trigram_rows]) for gram, _ in gram_keys}
    spelling_counts = Counter(s for s in spellings if s != "undetermined")
    spelling = spelling_counts.most_common(1)[0][0] if spelling_counts else "undetermined"
    hashes = [hashlib.sha256(text.encode()).hexdigest() for text in clean_texts]
    words = sum(len(parse(text).tokens) for text in clean_texts)
    if words < 500:
        warnings.append("profile contains fewer than 500 words; voice estimates are provisional")

    return VoiceProfile(
        name=name,
        version="2.0",
        sample_count=len(clean_texts),
        word_count=words,
        metrics_mean={k: round(v, 6) for k, v in means.items()},
        metrics_sd={k: round(v, 6) for k, v in sds.items()},
        function_words={k: round(v, 8) for k, v in function.items()},
        char_trigrams={k: round(v, 8) for k, v in trigrams.items()},
        spelling_preference=spelling,
        source_hashes=hashes,
        warnings=warnings,
    )


def _cosine_distance(left: dict[str, float], right: dict[str, float]) -> float:
    keys = set(left) | set(right)
    dot = sum(left.get(k, 0.0) * right.get(k, 0.0) for k in keys)
    lnorm = math.sqrt(sum(left.get(k, 0.0) ** 2 for k in keys))
    rnorm = math.sqrt(sum(right.get(k, 0.0) ** 2 for k in keys))
    if not lnorm or not rnorm:
        return 0.0
    return 1.0 - dot / (lnorm * rnorm)


def _profile_obj(profile: VoiceProfile | dict | str | Path) -> VoiceProfile:
    if isinstance(profile, VoiceProfile):
        return profile
    if isinstance(profile, dict):
        return VoiceProfile.from_dict(profile)
    return load_voice_profile(profile)


def voice_distance(profile: VoiceProfile | dict | str | Path, text: str) -> VoiceDistance:
    selected = _profile_obj(profile)
    metrics, function, trigrams, spelling = _metrics(text)
    deltas: list[dict] = []
    z_values: list[float] = []
    default_scales = {
        "sentence_mean": 5.0, "sentence_sd": 4.0, "sentence_cv": 0.2,
        "sentence_median": 5.0, "short_sentence_share": 0.15, "long_sentence_share": 0.12,
        "paragraph_mean": 20.0, "paragraph_cv": 0.25, "mattr": 0.08,
        "mean_word_length": 0.4, "first_person_per_1000": 8.0,
        "contractions_per_1000": 5.0, "comma_per_1000": 8.0,
        "semicolon_per_1000": 2.5, "colon_per_1000": 3.0, "em_dash_per_1000": 2.0,
        "parenthesis_per_1000": 4.0, "question_per_1000": 3.0,
        "exclamation_per_1000": 2.0, "sentence_initial_and_but_share": 0.1,
    }
    for name, target in selected.metrics_mean.items():
        observed = metrics.get(name, 0.0)
        scale = max(selected.metrics_sd.get(name, 0.0), default_scales.get(name, max(0.1, abs(target) * 0.2)))
        z = abs(observed - target) / scale
        z_values.append(min(4.0, z))
        deltas.append({
            "metric": name,
            "observed": round(observed, 3),
            "profile": round(target, 3),
            "standardizedDelta": round(z, 2),
        })
    function_distance = _cosine_distance(selected.function_words, function)
    trigram_distance = _cosine_distance(selected.char_trigrams, trigrams)
    numeric = _mean(z_values) / 4.0
    spelling_penalty = 0.10 if selected.spelling_preference != "undetermined" and spelling not in {"undetermined", selected.spelling_preference} else 0.0
    composite = min(1.0, 0.55 * numeric + 0.25 * function_distance + 0.20 * trigram_distance + spelling_penalty)
    score = 100 * composite
    top = sorted(deltas, key=lambda row: row["standardizedDelta"], reverse=True)[:5]
    interpretation = "close" if score < 20 else "noticeable drift" if score < 40 else "substantial drift" if score < 65 else "far from profile"
    return VoiceDistance(score=score, interpretation=interpretation, metric_deltas=top, profile=selected.name)


def save_voice_profile(profile: VoiceProfile, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def load_voice_profile(path: str | Path) -> VoiceProfile:
    return VoiceProfile.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
