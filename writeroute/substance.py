"""Conservative substantive-risk checks.

These checks do not judge whether a proposition is true. They identify places where
prose makes a causal, safety, novelty, statistical or recommendation claim without
showing the support a demanding reader would need. Such findings are review gates,
not automatic rewrites.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .genres import GenreProfile
from .model import Document


@dataclass(frozen=True)
class RawFinding:
    pattern_id: str
    title: str
    category: str
    severity: str
    confidence: float
    start: int
    end: int
    rationale: str
    action: str
    source: str = "substance"
    reported_voice: bool = False


_CITATION = re.compile(
    r"\[[0-9,;\s–—-]+\]|\([A-Z][A-Za-z'’.-]+(?:\s+et\s+al\.?)?,?\s+(?:19|20)\d{2}[a-z]?\)|"
    r"\bdoi\s*:|https?://|\b(?:according to|reported by|data from|analysis of|we (?:found|estimated|observed|measured))\b",
    re.IGNORECASE,
)
_EFFECT = re.compile(
    r"\b(?:OR|RR|HR|IRR|ARR|NNT|AUC|R\^?2|β|beta|mean difference|risk difference|"
    r"odds ratio|risk ratio|hazard ratio)\s*[=:]?\s*[-−]?\d|\b\d+(?:\.\d+)?\s*%\b|"
    r"\b(?:90|95|99)\s*%\s*CI\b",
    re.IGNORECASE,
)
_OBSERVATIONAL = re.compile(
    r"\b(?:observational|cross-sectional|case-control|retrospective|prospective cohort|cohort study|"
    r"ecological|survey|secondary analysis|non-randomi[sz]ed|registry data)\b",
    re.IGNORECASE,
)
_CAUSAL = re.compile(
    r"\b(?:caused?|causing|led to|leads to|resulted in|results in|drove|drives|produced|"
    r"prevented?|eliminated?|improved|reduced|increased)\b",
    re.IGNORECASE,
)
_ASSOCIATIONAL_GUARD = re.compile(
    r"\b(?:associated with|correlated with|linked to|may|might|could|suggests?|consistent with|"
    r"estimated effect|target trial|instrumental variable|difference-in-differences|randomi[sz]ed|"
    r"causal inference|do-calculus|g-formula|inverse probability|propensity score)\b",
    re.IGNORECASE,
)
_SAFETY = re.compile(
    r"\b(?:safe|harmless|risk-free|without risk|no adverse effects?|proven|guaranteed|"
    r"reliable under all|works? every time|eliminates? (?:all )?risk|cannot fail|will prevent|"
    r"(?:is|are|was|were|remains?|proved|proven|shown to be|considered) (?:effective|stable|reliable)|"
    r"safe and effective)\b",
    re.IGNORECASE,
)
_NOVELTY = re.compile(
    r"\b(?:first-ever|unprecedented|never before|only (?:tool|study|system|approach|method)|"
    r"the first (?:study|analysis|tool|system|approach|method))\b",
    re.IGNORECASE,
)
_SIGNIFICANCE = re.compile(r"\bstatistically significant\b|\bsignificant(?:ly)?\s+(?:higher|lower|increased|decreased|improved|reduced)\b", re.IGNORECASE)
_RECOMMEND = re.compile(r"\b(?:we recommend|it is recommended|should|must|ought to|needs? to|priority should be|policymakers? should)\b", re.IGNORECASE)
_ACTOR = re.compile(
    r"\b(?:ministry|government|programme|program|agency|team|manager|clinician|provider|funder|"
    r"researcher|committee|organisation|organization|developer|administrator|user|WHO|UNICEF|"
    r"district|facility|employer|supplier|investor|reader|patient|board|company|client|customer|"
    r"regulator|institution|vendor|contractor|author)s?\b",
    re.IGNORECASE,
)
_RATIONALE = re.compile(r"\b(?:because|given|since|to reduce|to increase|to prevent|so that|based on|in response to|therefore)\b", re.IGNORECASE)
_ABSTRACT_NOUN = re.compile(r"\b\w{5,}(?:tion|sion|ment|ness|ity|ance|ence|ship|ism)s?\b", re.IGNORECASE)
_CONCRETE_MARKER = re.compile(r"\b\d|\b[A-Z]{2,}\b|\b[A-Z][a-z]+\s+[A-Z][a-z]+\b|`|https?://|\[[0-9]", re.MULTILINE)
_QUALITY_WORD = re.compile(r"\b(?:robust|rigorous|comprehensive|high-quality|reliable|validated)\b", re.IGNORECASE)
_QUALITY_EVIDENCE = re.compile(
    r"\b(?:sensitivity analysis|validation (?:set|sample|study)|external validation|bootstrap|"
    r"cross-validation|inter-rater|calibration|coverage|audit|independent dataset|replication|"
    r"pre-registered|preregistered|protocol|quality assurance)\b",
    re.IGNORECASE,
)


def _severity(pattern_id: str, default: str, genre: GenreProfile) -> str:
    if pattern_id in genre.hard_patterns:
        return "hard"
    if pattern_id in genre.soft_patterns:
        return "soft"
    return default


def _nearby(text: str, start: int, end: int, radius: int = 220) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)]


def _finding(
    document: Document,
    genre: GenreProfile,
    pattern_id: str,
    title: str,
    category: str,
    default_severity: str,
    confidence: float,
    start: int,
    end: int,
    rationale: str,
    action: str,
) -> RawFinding:
    reported = document.is_reported_voice(start, end)
    severity = _severity(pattern_id, default_severity, genre)
    if reported and severity == "hard":
        severity = "review"
    return RawFinding(
        pattern_id=pattern_id,
        title=title,
        category=category,
        severity=severity,
        confidence=max(0.35, confidence - (0.2 if reported else 0.0)),
        start=start,
        end=end,
        rationale=rationale,
        action=action,
        reported_voice=reported,
    )


def scan_substance(document: Document, genre: GenreProfile) -> list[RawFinding]:
    text = document.text
    lower = text.casefold()
    findings: list[RawFinding] = []
    observational_document = bool(_OBSERVATIONAL.search(text))

    for sentence in document.sample.sentences:
        if document.is_protected(sentence.start, sentence.end):
            continue
        s = sentence.text
        nearby = _nearby(text, sentence.start, sentence.end)

        causal = _CAUSAL.search(s)
        if causal and genre.id in {"scientific", "systematic-review"}:
            has_support = bool(_CITATION.search(nearby) or _EFFECT.search(s))
            guarded = bool(_ASSOCIATIONAL_GUARD.search(s))
            if observational_document and not guarded:
                findings.append(_finding(
                    document, genre, "causal_overreach_observational", "Causal wording in an observational context", "claim_support",
                    "hard", 0.93, sentence.start + causal.start(), sentence.start + causal.end(),
                    "The document describes observational evidence, but this sentence states a causal effect without an identification argument.",
                    "State the identifying assumptions and causal method, or recast the result as an association.",
                ))
            elif not has_support and not guarded:
                findings.append(_finding(
                    document, genre, "unsupported_causal_claim", "Unsupported causal wording", "claim_support",
                    "review", 0.82, sentence.start + causal.start(), sentence.start + causal.end(),
                    "The sentence uses causal language without a visible design, estimate, source or qualification that warrants it.",
                    "Name the causal design and estimand, cite the evidence, or use associational language that matches the study.",
                ))

        safety = _SAFETY.search(s)
        if safety and not (_CITATION.search(nearby) or _EFFECT.search(s)):
            pattern_id = "unsupported_guarantee" if genre.id == "technical" else "unsupported_safety_claim"
            findings.append(_finding(
                document, genre, pattern_id, "Unsupported safety, effectiveness or guarantee claim", "claim_support",
                "review", 0.88, sentence.start + safety.start(), sentence.start + safety.end(),
                "The sentence makes a strong safety, effectiveness, stability or guarantee claim without a visible condition or supporting test.",
                "Name the test, population, operating condition and limitation, or narrow the claim.",
            ))

        novelty = _NOVELTY.search(s)
        if novelty and not _CITATION.search(nearby):
            findings.append(_finding(
                document, genre, "unsupported_novelty_claim", "Unsupported novelty claim", "claim_support",
                "review", 0.86, sentence.start + novelty.start(), sentence.start + novelty.end(),
                "A first, only or unprecedented claim requires a defined search space and date.",
                "State how the comparison set was searched and bounded, or replace the novelty claim with the specific contribution.",
            ))

        sig = _SIGNIFICANCE.search(s)
        if sig and not (_EFFECT.search(s) or re.search(r"\bp\s*[<=>]\s*0?\.\d+", s, re.IGNORECASE)):
            findings.append(_finding(
                document, genre, "statistical_significance_without_estimate", "Significance without effect estimate", "statistical_reporting",
                "hard" if genre.id in {"scientific", "systematic-review"} else "review", 0.96,
                sentence.start + sig.start(), sentence.start + sig.end(),
                "Statistical significance alone does not show the magnitude or precision of the result.",
                "Report the effect estimate and uncertainty interval; retain the p-value only if it serves the inferential framework.",
            ))

        quality = _QUALITY_WORD.search(s)
        literal_quality_use = bool(re.search(r"\b(?:robust standard errors?|robust regression|robust variance|quality assurance protocol)\b", s, re.IGNORECASE))
        if quality and not literal_quality_use and not _QUALITY_EVIDENCE.search(nearby):
            # Contextual literal uses are handled in the phrase layer; this check is
            # deliberately limited to prose that appears to evaluate a method or output.
            if re.search(r"\b(?:method|analysis|approach|framework|evidence|result|model|process|review|assessment)\b", s, re.IGNORECASE):
                findings.append(_finding(
                    document, genre, "unsubstantiated_quality_claim", "Quality label without criterion", "claim_support",
                    "review", 0.78, sentence.start + quality.start(), sentence.start + quality.end(),
                    "The quality adjective is not tied to a named test, comparator, coverage rule or validation result.",
                    "Replace the label with the procedure and result that support it.",
                ))

        rec = _RECOMMEND.search(s)
        if rec and genre.id in {"policy-brief", "professional-report", "grant", "systematic-review"}:
            explicit_subject = bool(re.search(r"^[A-Z][^,.;!?]{1,90}\b(?:should|must|needs? to|ought to)\b", s))
            lacks_actor = not (_ACTOR.search(s) or explicit_subject)
            lacks_reason = not _RATIONALE.search(nearby) and not _CITATION.search(nearby)
            if lacks_actor or lacks_reason:
                missing = []
                if lacks_actor:
                    missing.append("responsible actor")
                if lacks_reason:
                    missing.append("evidence or rationale")
                findings.append(_finding(
                    document, genre, "unsupported_recommendation", "Recommendation missing an execution basis", "decision_support",
                    "hard" if genre.id in {"policy-brief", "professional-report"} else "review", 0.84,
                    sentence.start + rec.start(), sentence.start + rec.end(),
                    "The recommendation lacks " + " and ".join(missing) + ".",
                    "Name who should do what, by when, under which condition, and why the evidence supports that action.",
                ))

    # Paragraph-level abstraction check. This never auto-edits because a precise
    # concrete example must come from the author or evidence base.
    for paragraph in document.sample.paragraphs:
        words = re.findall(r"\b[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’-]*\b", paragraph.text)
        if len(words) < 45 or _CONCRETE_MARKER.search(paragraph.text):
            continue
        abstract = len(_ABSTRACT_NOUN.findall(paragraph.text))
        vague_verbs = len(re.findall(
            r"\b(?:enable|facilitate|foster|support|enhance|improve|drive|deliver|promote|ensure|address)\b",
            paragraph.text, re.IGNORECASE,
        ))
        if abstract >= 6 and vague_verbs >= 2:
            findings.append(_finding(
                document, genre, "abstraction_without_substrate", "Abstract paragraph without a concrete substrate", "specificity",
                "soft", 0.72, paragraph.start, paragraph.end,
                "The paragraph relies on abstract nouns and generic verbs without a named example, measure, actor or mechanism.",
                "Add the specific actor, action, object, mechanism, example or number that the paragraph is meant to convey.",
            ))

    # De-duplicate overlapping substantive findings of the same category.
    findings.sort(key=lambda f: (f.start, f.end, f.pattern_id))
    output: list[RawFinding] = []
    seen: set[tuple[str, int, int]] = set()
    for item in findings:
        key = (item.pattern_id, item.start, item.end)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output
