"""Genre profiles and light-weight genre inference."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

GENRE_DIR = Path(__file__).parent / "data" / "genres"


@dataclass(frozen=True)
class GenreProfile:
    id: str
    name: str
    aliases: tuple[str, ...]
    purpose: str
    audience: str
    integrity_policy: str
    hard_patterns: frozenset[str]
    soft_patterns: frozenset[str]
    disabled_patterns: frozenset[str]
    required_moves: tuple[str, ...]
    protected_conventions: tuple[str, ...]
    prompt_rules: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict) -> "GenreProfile":
        return cls(
            id=data["id"],
            name=data["name"],
            aliases=tuple(data.get("aliases", [])),
            purpose=data.get("purpose", ""),
            audience=data.get("audience", ""),
            integrity_policy=data.get("integrityPolicy", data["id"]),
            hard_patterns=frozenset(data.get("hardPatterns", [])),
            soft_patterns=frozenset(data.get("softPatterns", [])),
            disabled_patterns=frozenset(data.get("disabledPatterns", [])),
            required_moves=tuple(data.get("requiredMoves", [])),
            protected_conventions=tuple(data.get("protectedConventions", [])),
            prompt_rules=tuple(data.get("promptRules", [])),
        )


@lru_cache(maxsize=1)
def load_genres() -> dict[str, GenreProfile]:
    profiles: dict[str, GenreProfile] = {}
    for path in sorted(GENRE_DIR.glob("*.json")):
        profile = GenreProfile.from_dict(json.loads(path.read_text()))
        profiles[profile.id] = profile
    return profiles


def get_genre(name: str | None) -> GenreProfile:
    profiles = load_genres()
    if not name:
        return profiles["general"]
    key = name.casefold().strip()
    if key in profiles:
        return profiles[key]
    for profile in profiles.values():
        if key in {a.casefold() for a in profile.aliases}:
            return profile
    raise ValueError(f"Unknown genre {name!r}; choose one of {', '.join(sorted(profiles))}")


_GENRE_CUES: dict[str, tuple[tuple[str, float], ...]] = {
    "systematic-review": (
        (r"\bPRISMA\b", 3.0), (r"\bPROSPERO\b", 3.0),
        (r"\bmeta-analysis\b|\bsystematic review\b|\bscoping review\b", 2.5),
        (r"\bAMSTAR(?:-?2)?\b|\bGRADE\b|risk of bias", 2.0),
    ),
    "scientific": (
        (r"(?im)^#{0,3}\s*(?:methods?|results?|discussion|abstract)\s*$", 1.8),
        (r"\b95\s*%\s*CI\b|\bp\s*[<=>]\s*0?\.\d+", 2.0),
        (r"\bparticipants?\b|\bcohort\b|\brandomi[sz]ed\b|\bregression\b", 1.0),
    ),
    "legal": (
        (r"(?im)^#{0,3}\s*(?:issue|brief answer|rule|analysis|conclusion)\s*$", 2.0),
        (r"\bnotwithstanding\b|\bhereinafter\b|\bpursuant to\b", 2.0),
        (r"\bSection\s+\d|\bArticle\s+\d|\bshall\b", 1.0),
    ),
    "technical": (
        (r"```|\bAPI\b|\bCLI\b|\bendpoint\b", 1.5),
        (r"(?m)^\s*(?:pip|npm|pnpm|curl|python|docker|git)\s+", 2.0),
        (r"(?im)^#{1,6}\s*(?:installation|usage|parameters|returns|troubleshooting)", 1.5),
    ),
    "policy-brief": (
        (r"(?im)^#{0,3}\s*(?:policy options?|recommendations?|key messages?)\s*$", 2.0),
        (r"\bpolicymakers?\b|\bministry\b|\bimplementation\b", 1.0),
    ),
    "professional-report": (
        (r"(?im)^#{0,3}\s*(?:executive summary|findings|recommendations|terms of reference)\s*$", 1.8),
        (r"\bstakeholder\b|\bdeliverable\b|\bworkstream\b", 0.8),
    ),
    "grant": (
        (r"(?im)^#{0,3}\s*(?:objectives?|work plan|budget|theory of change|monitoring and evaluation)\s*$", 2.0),
        (r"\bfunder\b|\bgrant\b|\bapplicant\b", 1.2),
    ),
    "email": (
        (r"(?im)^(?:dear|hi|hello)\s+[^\n]+", 2.0),
        (r"(?im)^(?:best regards|kind regards|sincerely|many thanks)[,\s]*$", 2.0),
    ),
}


def infer_genre(text: str) -> dict:
    scores: dict[str, float] = {name: 0.0 for name in _GENRE_CUES}
    evidence: dict[str, list[str]] = {name: [] for name in _GENRE_CUES}
    for genre, cues in _GENRE_CUES.items():
        for pattern, weight in cues:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                scores[genre] += weight
                evidence[genre].append(match.group(0)[:80])
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    winner, score = ordered[0] if ordered else ("general", 0.0)
    if score < 1.5:
        winner = "general"
    total = sum(max(0.0, s) for s in scores.values()) or 1.0
    return {
        "genre": winner,
        "confidence": round(min(1.0, score / 4.0), 3),
        "alternatives": [
            {"genre": name, "score": round(value, 3), "evidence": evidence[name]}
            for name, value in ordered[:3] if value > 0
        ],
        "scoreShare": round(score / total, 3) if score else 0.0,
    }
