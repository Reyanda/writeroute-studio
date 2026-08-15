"""Skill engine: loads declarative feature packs and compiles them into extractors.

A pack is a JSON file declaring FeatureTypes (ontology class) with a measurement
kind, parameters, direction, weight, and a human baseline. Packs are merged from
three roots, later roots overriding earlier features by id:

  1. built-in   aiwd/data/packs/
  2. user       ~/.aiwd/packs/
  3. project    ./packs/ (relative to cwd)

Baselines can additionally be overridden by a calibration file produced by
`aiwd calibrate` (see scoring.calibrate)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .statistics import STATISTICS
from .textmodel import WritingSample

BUILTIN_PACK_DIR = Path(__file__).parent / "data" / "packs"
USER_PACK_DIR = Path.home() / ".aiwd" / "packs"

KINDS = {"lexicon_density", "regex_rate", "paragraph_start_lexicon", "final_paragraph_lexicon", "statistic"}


@dataclass
class Match:
    """Evidence span in the original text."""
    start: int
    end: int
    text: str


@dataclass
class FeatureSpec:
    id: str
    family: str
    kind: str
    unit: str
    direction: str  # high_is_ai | low_is_ai
    weight: float
    baseline_mean: float
    baseline_sd: float
    baseline_source: str
    pack: str
    explanation: str = ""
    lexicon: list[str] = field(default_factory=list)
    pattern: str | None = None
    flags: str = ""
    statistic: str | None = None
    rewrites: dict[str, list[str]] = field(default_factory=dict)
    safe_rewrites: list[str] = field(default_factory=list)
    zero_is_neutral: bool | None = None
    not_evidence_in: list[str] = field(default_factory=list)
    skill_pattern: str = ""

    @property
    def is_presence_feature(self) -> bool:
        """True when a hit is evidence but a non-hit is not.

        Lexicon and regex features detect the PRESENCE of a stock phrase or
        construction. Finding "great question" in a report is evidence; not finding
        it says nothing about who wrote the report, because most sentences in any
        text contain none of these. Scoring the zero as evidence of humanness gives
        a document credit for every phrase it happens not to contain, and means
        every rare-phrase detector added to the pack dilutes whatever real signal
        the other features found.

        Statistics are different: a low burstiness or a low numeric density is a
        property of the whole text, measured on every token, and its low end is
        genuinely informative.
        """
        if self.zero_is_neutral is not None:
            return self.zero_is_neutral
        return (self.kind in ("lexicon_density", "regex_rate",
                              "paragraph_start_lexicon", "final_paragraph_lexicon")
                and self.direction == "high_is_ai")

    def _regex(self) -> re.Pattern:
        flags = re.IGNORECASE if "i" in self.flags else 0
        return re.compile(self.pattern, flags)

    def _lexicon_regex(self) -> re.Pattern:
        parts = sorted((re.escape(t) for t in self.lexicon), key=len, reverse=True)
        return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)

    def measure(self, sample: WritingSample) -> tuple[float | None, list[Match]]:
        """Return (raw value, evidence matches) or (None, []) if not measurable."""
        text = sample.normalized_text
        tokens = sample.token_count
        if self.kind == "statistic":
            fn = STATISTICS.get(self.statistic or "")
            if fn is None:
                return None, []
            return fn(sample), []
        if self.kind == "lexicon_density":
            if tokens < 50:
                return None, []
            matches = [Match(m.start(), m.end(), sample.text[m.start() : m.end()])
                       for m in self._lexicon_regex().finditer(text)]
            return 1000 * len(matches) / tokens, matches
        if self.kind == "regex_rate":
            if tokens < 50:
                return None, []
            matches = [Match(m.start(), m.end(), sample.text[m.start() : m.end()])
                       for m in self._regex().finditer(text)]
            return 1000 * len(matches) / tokens, matches
        if self.kind == "paragraph_start_lexicon":
            paragraphs = sample.paragraphs
            if len(paragraphs) < 2:
                return None, []
            rx = re.compile(r"^(?:" + "|".join(re.escape(t) for t in self.lexicon) + r")\b", re.IGNORECASE)
            matches = []
            for p in paragraphs:
                m = rx.match(p.text)
                if m:
                    matches.append(Match(p.start, p.start + m.end(), p.text[: m.end()]))
            return len(matches) / len(paragraphs), matches
        if self.kind == "final_paragraph_lexicon":
            tail = sample.paragraphs[-2:]
            rx = self._lexicon_regex()
            matches = []
            for p in tail:
                norm = text[p.start : p.end]
                for m in rx.finditer(norm):
                    matches.append(Match(p.start + m.start(), p.start + m.end(),
                                         sample.text[p.start + m.start() : p.start + m.end()]))
            return (1.0 if matches else 0.0), matches
        return None, []


class SkillRegistry:
    def __init__(self, features: dict[str, FeatureSpec], families: dict, packs: list[str]):
        self.features = features
        self.families = families
        self.packs = packs

    @classmethod
    def load(cls, extra_dirs: list[Path] | None = None,
             baseline_overrides: dict | None = None) -> "SkillRegistry":
        ontology = json.loads((Path(__file__).parent / "data" / "ontology.json").read_text())
        families = ontology["families"]
        dirs = [BUILTIN_PACK_DIR, USER_PACK_DIR, Path.cwd() / "packs"]
        if extra_dirs:
            dirs.extend(extra_dirs)
        features: dict[str, FeatureSpec] = {}
        pack_names: list[str] = []
        for d in dirs:
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.json")):
                data = json.loads(path.read_text())
                if "features" not in data:
                    continue  # not a pack (e.g. a stray baselines file)
                pack_names.append(f"{data.get('pack', path.stem)} ({path})")
                for f in data["features"]:
                    spec = cls._parse_feature(f, data.get("pack", path.stem), families)
                    features[spec.id] = spec
        if baseline_overrides:
            for fid, bl in baseline_overrides.items():
                if fid in features and bl.get("sd", 0) > 0:
                    features[fid].baseline_mean = bl["mean"]
                    features[fid].baseline_sd = bl["sd"]
                    features[fid].baseline_source = bl.get("source", "calibrated")
        return cls(features, families, pack_names)

    @staticmethod
    def _parse_feature(f: dict, pack: str, families: dict) -> FeatureSpec:
        if f["family"] not in families:
            raise ValueError(f"Feature {f['id']} references unknown family {f['family']}")
        if f["kind"] not in KINDS:
            raise ValueError(f"Feature {f['id']} has unknown kind {f['kind']}")
        if f["direction"] not in ("high_is_ai", "low_is_ai"):
            raise ValueError(f"Feature {f['id']} has invalid direction")
        bl = f["baseline"]
        if bl["sd"] <= 0:
            raise ValueError(f"Feature {f['id']} baseline sd must be positive")
        return FeatureSpec(
            id=f["id"], family=f["family"], kind=f["kind"], unit=f.get("unit", ""),
            direction=f["direction"], weight=float(f.get("weight", 1.0)),
            baseline_mean=float(bl["mean"]), baseline_sd=float(bl["sd"]),
            baseline_source=bl.get("source", "default_prior"), pack=pack,
            explanation=f.get("explanation", ""), lexicon=f.get("lexicon", []),
            pattern=f.get("pattern"), flags=f.get("flags", ""),
            statistic=f.get("statistic"), rewrites=f.get("rewrites", {}),
            safe_rewrites=f.get("safe_rewrites", []),
            zero_is_neutral=f.get("zeroIsNeutral"),
            not_evidence_in=f.get("notEvidenceIn", []),
            skill_pattern=f.get("skillPattern", ""),
        )
