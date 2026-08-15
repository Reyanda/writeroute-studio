"""Scoring layer: FeatureMeasurements -> FamilyScores -> DetectionResult.

The report dict is isomorphic to the ontology's JSON schema:
features[] carry featureType/family/value/zScoreAgainstHumanBaseline/
aiLikenessContribution/measurementMethod; detectionResult carries
globalAiProbability/globalConfidence/decisionLabel/familyScores[].
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .skillengine import Match, SkillRegistry
from .allowlist import AllowList, Exemption, load_allowlist
from .reported import Discount, reported_fraction, reported_spans
from .reported import filter_matches as filter_reported
from .textmodel import WritingSample, parse

# logistic steepness: z of +2 -> contribution ~0.90
_K = 1.1

AI_THRESHOLD = 0.65
HUMAN_THRESHOLD = 0.40
MIXED_SPREAD = 0.30


@dataclass
class Measurement:
    feature_id: str
    family: str
    value: float
    z: float
    contribution: float  # 0..1, 0.5 = neutral
    weight: float
    method: str
    unit: str
    explanation: str
    baseline_source: str
    skill_pattern: str = ""
    declined_for_genre: str = ""
    matches: list[Match] = field(default_factory=list)
    # A presence feature that found nothing is reported — the reader should see it
    # was checked — but contributes no evidence, so it stays out of the aggregate.
    # Scoring it either way is wrong: as AI it would be an accusation from silence,
    # as human it would be credit for a phrase the text happened not to contain.
    scored: bool = True


def _contribution(z: float, direction: str) -> float:
    z_ai = z if direction == "high_is_ai" else -z
    return 1.0 / (1.0 + math.exp(-_K * z_ai))


def measure_all(sample: WritingSample, registry: SkillRegistry,
                genre: str = "", allowlist: AllowList | None = None,
                exemptions: list | None = None, discounts: list | None = None,
                reported_guard: bool = True) -> list[Measurement]:
    """Measure every feature. A genre switches off the features that measure its
    own conventions: institutional prose has no first person and clinical methods
    are passive, and scoring either as machine-likeness would push a writer to make
    a correct document worse. Declined features stay in the report with the reason.
    """
    out = []
    allowlist = load_allowlist() if allowlist is None else allowlist
    # Quoted and attributed material is someone else's writing. Computed once for
    # the document rather than per feature, because the spans do not depend on
    # which feature is being measured.
    voice_spans = reported_spans(sample.normalized_text) if reported_guard else []
    for spec in registry.features.values():
        value, matches = spec.measure(sample)
        if value is None:
            continue
        # A field-standard term is not a buzzword. Drop the excused hits and
        # rescale the rate to the hits that remain, so the score reflects only
        # what the allow-list did not account for.
        if matches and allowlist.entries:
            kept, excused = allowlist.filter_matches(spec.id, sample.normalized_text,
                                                     matches, genre=genre)
            if excused:
                if exemptions is not None:
                    exemptions.extend(excused)
                if matches:
                    value = value * (len(kept) / len(matches))
                matches = kept
        # Then discount what the author is quoting rather than asserting. Rates are
        # rescaled to the hits that remain, matching the allow-list convention.
        if matches and voice_spans:
            kept, quoted = filter_reported(spec.id, matches, voice_spans)
            if quoted:
                if discounts is not None:
                    discounts.extend(quoted)
                value = value * (len(kept) / len(matches))
                matches = kept
        z = (value - spec.baseline_mean) / spec.baseline_sd
        # A presence feature that found nothing is neutral, not exculpatory. The
        # absence of "great question" is not evidence a human wrote the text, and
        # scoring it as such means every rare-phrase detector added to the packs
        # dilutes the signal the other features found.
        found_nothing = spec.is_presence_feature and value <= 0
        declined = genre if genre and genre in spec.not_evidence_in else ""
        contribution = 0.5 if found_nothing else _contribution(z, spec.direction)
        out.append(Measurement(
            feature_id=spec.id, family=spec.family, value=value, z=z,
            contribution=contribution, weight=spec.weight,
            scored=not found_nothing and not declined,
            skill_pattern=spec.skill_pattern, declined_for_genre=declined,
            method=f"{spec.kind}:{spec.statistic or spec.pack}", unit=spec.unit,
            explanation=spec.explanation, baseline_source=spec.baseline_source,
            matches=matches,
        ))
    return out


def score(sample: WritingSample, registry: SkillRegistry, genre: str = "",
          allowlist: AllowList | None = None, reported_guard: bool = True) -> dict:
    exemptions: list[Exemption] = []
    discounts: list[Discount] = []
    measurements = measure_all(sample, registry, genre=genre,
                               allowlist=allowlist, exemptions=exemptions,
                               discounts=discounts, reported_guard=reported_guard)
    family_scores: dict[str, float] = {}
    for fam in registry.families:
        ms = [m for m in measurements if m.family == fam and m.scored]
        if not ms:
            continue
        wsum = sum(m.weight for m in ms)
        family_scores[fam] = sum(m.contribution * m.weight for m in ms) / wsum

    fam_weights = {f: v["weight"] for f, v in registry.families.items()}
    covered = {f: w for f, w in fam_weights.items() if f in family_scores}
    coverage = sum(covered.values())  # families weights sum to 1.0
    global_p = (
        sum(family_scores[f] * w for f, w in covered.items()) / coverage
        if coverage else 0.5
    )

    tokens_factor = min(1.0, sample.token_count / 800)
    confidence = round(tokens_factor * (0.4 + 0.6 * coverage), 3)

    spread = (max(family_scores.values()) - min(family_scores.values())) if family_scores else 0.0
    if global_p >= AI_THRESHOLD:
        label = "AI_GENERATED"
    elif global_p <= HUMAN_THRESHOLD:
        label = "HUMAN_GENERATED"
    elif spread >= MIXED_SPREAD:
        label = "MIXED"
    else:
        label = "UNCERTAIN"

    top = sorted([m for m in measurements if m.scored],
                 key=lambda m: (m.contribution - 0.5) * m.weight, reverse=True)
    exempt_summary: dict[str, dict] = {}
    for ex in exemptions:
        key = f"{ex.feature_id}:{ex.entry_id}"
        row = exempt_summary.setdefault(key, {
            "featureType": ex.feature_id, "allowListEntry": ex.entry_id,
            "reason": ex.reason, "count": 0, "examples": [],
        })
        row["count"] += 1
        if len(row["examples"]) < 3 and ex.text not in row["examples"]:
            row["examples"].append(ex.text)

    quoted_summary: dict[str, dict] = {}
    for d in discounts:
        key = f"{d.feature_id}:{d.kind}"
        row = quoted_summary.setdefault(key, {
            "featureType": d.feature_id, "kind": d.kind, "count": 0, "examples": [],
        })
        row["count"] += 1
        if len(row["examples"]) < 3 and d.text not in row["examples"]:
            row["examples"].append(d.text)

    return {
        "tokenCount": sample.token_count,
        "paragraphCount": len(sample.paragraphs),
        "sentenceCount": len(sample.sentences),
        "features": [
            {
                "featureType": m.feature_id,
                "family": m.family,
                "value": round(m.value, 4),
                "zScoreAgainstHumanBaseline": round(m.z, 3),
                "aiLikenessContribution": round(m.contribution, 3),
                "measurementMethod": m.method,
                "unit": m.unit,
                "baselineSource": m.baseline_source,
                "countsTowardsScore": m.scored,
                "skillPattern": m.skill_pattern,
                "declinedForGenre": m.declined_for_genre,
                "evidence": [
                    {"start": h.start, "end": h.end, "text": h.text} for h in m.matches[:8]
                ],
            }
            for m in measurements
        ],
        "allowListExemptions": sorted(exempt_summary.values(),
                                      key=lambda r: -r["count"]),
        "reportedVoiceDiscounts": sorted(quoted_summary.values(),
                                         key=lambda r: -r["count"]),
        "reportedVoiceFraction": round(
            reported_fraction(sample.normalized_text) if reported_guard else 0.0, 3),
        "detectionResult": {
            "globalAiProbability": round(global_p, 3),
            "globalConfidence": confidence,
            "decisionLabel": label,
            "familyScores": [
                {"family": f, "familyAiScore": round(s, 3)} for f, s in sorted(
                    family_scores.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "explainedBy": [
                {"featureType": m.feature_id, "family": m.family,
                 "aiLikenessContribution": round(m.contribution, 3),
                 "note": m.explanation}
                for m in top[:5]
            ],
        },
        "caveat": (
            "Screening evidence, not proof of authorship. False positives are elevated for "
            "non-native, highly technical, and grammar-checker-assisted prose. Baselines marked "
            "default_prior are uncalibrated; run `aiwd calibrate` on your own human reference "
            "texts before relying on scores."
        ),
    }


def scan_text(text: str, registry: SkillRegistry | None = None,
              genre: str = "", allowlist: AllowList | None = None,
              reported_guard: bool = True) -> dict:
    registry = registry or SkillRegistry.load()
    return score(parse(text), registry, genre=genre, allowlist=allowlist,
                 reported_guard=reported_guard)


def calibrate(paths: list[Path], registry: SkillRegistry | None = None) -> dict:
    """Compute per-feature baselines from human reference texts."""
    registry = registry or SkillRegistry.load()
    per_feature: dict[str, list[float]] = {}
    used = []
    for path in paths:
        text = path.read_text(errors="replace")
        try:
            sample = parse(text)
        except ValueError:
            continue
        used.append(str(path))
        for m in measure_all(sample, registry):
            per_feature.setdefault(m.feature_id, []).append(m.value)
    baselines = {}
    for fid, values in per_feature.items():
        if len(values) < 2:
            continue
        mu = sum(values) / len(values)
        sd = math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))
        baselines[fid] = {
            "mean": round(mu, 4),
            "sd": round(max(sd, abs(mu) * 0.05 + 1e-6), 4),  # floor sd to avoid brittle z-scores
            "source": f"calibrated_n{len(values)}",
        }
    return {"documents": used, "baselines": baselines}


def load_baselines(path: Path) -> dict:
    data = json.loads(path.read_text())
    return data.get("baselines", data)
