"""The WriteRoute orchestration layer.

Route: brief/evidence -> genre -> audit -> candidate generation -> integrity gate ->
voice/genre gate -> accept only a net improvement. Detection never substitutes for
editorial judgment, and no candidate can win by merely scoring as less AI-like.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence, Callable, Iterable

from .audit import AuditReport, audit_text
from .candidates import Candidate, _boundary_apply, candidates_for_finding
from .contracts import WritingBrief, compile_draft_contract, compile_revision_contract
from .evidence import verify_draft_evidence
from .genres import GenreProfile, get_genre, infer_genre
from .integrity import IntegrityReport, verify_integrity
from .voice import VoiceProfile, voice_distance

RevisionCallback = Callable[[str, str], str]


_AGGREGATE_LENGTH_DELETIONS = frozenset({
    "assistant_meta_preface", "closing_offer", "reasoning_leak",
    "importance_meta", "throat_clearing", "generic_world_opener",
    "summary_recap", "empty_transition", "rhetorical_setup",
    "sycophantic_opener", "fake_profound_kicker",
})


def _integrity_effective(report: IntegrityReport, applied_edits: Iterable[dict[str, Any]]) -> bool:
    """Accept only strict integrity or an explicitly bounded candidate exception.

    Candidate evaluation records the exact categories consumed by a recognised
    deletion. This aggregate gate never infers a new exception from a pattern
    name, and it never permits an added semantic anchor.
    """
    if report.passes:
        return True
    edits = list(applied_edits)
    if not edits:
        return False
    allowed: set[str] = set()
    has_override = False
    for edit in edits:
        if edit.get("contextualOverride"):
            has_override = True
            allowed.update(edit.get("contextualOverrideCategories", []))
    hard = [violation for violation in report.violations if violation.severity == "hard"]
    hard_categories = {violation.category for violation in hard}
    deletion_edits = [edit for edit in edits if edit.get("replacement") == ""]
    aggregate_length = bool(
        "length" in hard_categories
        and deletion_edits
        and all(edit.get("allowAggregateLength", False) for edit in deletion_edits)
    )
    if aggregate_length:
        has_override = True
        allowed.add("length")
    if not has_override or not hard_categories or not hard_categories.issubset(allowed):
        return False
    return not any(violation.added for violation in hard)


@dataclass
class RewriteAttempt:
    index: int
    accepted: bool
    reason: str
    burden: float | None
    integrity: dict[str, Any] | None
    voice_distance: float | None
    text: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "accepted": self.accepted,
            "reason": self.reason,
            "editorialBurden": None if self.burden is None else round(self.burden, 1),
            "integrity": self.integrity,
            "voiceDistance": None if self.voice_distance is None else round(self.voice_distance, 2),
        }


def _select_genre(text: str, genre: str | None) -> GenreProfile:
    name = infer_genre(text)["genre"] if not genre or genre == "auto" else genre
    return get_genre(name)


def suggest_text(
    text: str,
    genre: str | None = "auto",
    *,
    include_quoted: bool = False,
    voice_profile: VoiceProfile | dict | str | None = None,
    max_candidates: int = 3,
    source_text: bool = False,
) -> dict[str, Any]:
    selected = _select_genre(text, genre)
    report = audit_text(text, genre=selected.id, include_quoted=include_quoted)
    for finding in report.findings:
        candidates = candidates_for_finding(
            text,
            finding,
            selected,
            report,
            voice_profile=voice_profile,
            max_candidates=max_candidates,
        )
        candidate_payloads = [candidate.to_dict() for candidate in candidates]
        if source_text:
            for candidate in candidate_payloads:
                if candidate["safeToApply"]:
                    candidate["safeToApply"] = False
                    candidate["risk"] = "source-text"
                    candidate["rationale"] = (
                        candidate["rationale"] + " Source-text mode permits annotation but forbids mutation."
                    ).strip()
        finding.candidates = candidate_payloads
    payload = report.to_dict()
    payload["mode"] = "suggest"
    payload["sourceTextMode"] = source_text
    payload["safeReplacementCount"] = sum(
        candidate.get("safeToApply", False)
        for finding in payload["findings"]
        for candidate in finding.get("candidates", [])
    )
    payload["authorInputCount"] = sum(
        candidate.get("requiresAuthorInput", False)
        for finding in payload["findings"]
        for candidate in finding.get("candidates", [])
    )
    return payload


def _top_exact_candidate(
    text: str,
    finding,
    selected: GenreProfile,
    report: AuditReport,
    voice_profile,
) -> Candidate | None:
    candidates = candidates_for_finding(
        text, finding, selected, report, voice_profile=voice_profile, max_candidates=5
    )
    exact = [c for c in candidates if c.kind == "replacement" and c.safe_to_apply and c.start is not None]
    return max(exact, key=lambda c: c.score) if exact else None


def repair_text(
    text: str,
    genre: str | None = "auto",
    *,
    include_quoted: bool = False,
    voice_profile: VoiceProfile | dict | str | None = None,
    source_text: bool = False,
) -> dict[str, Any]:
    selected = _select_genre(text, genre)
    before = audit_text(text, genre=selected.id, include_quoted=include_quoted)
    if source_text:
        integrity = verify_integrity(text, text, selected.integrity_policy)
        return {
            "mode": "repair",
            "genre": selected.id,
            "sourceTextMode": True,
            "changed": False,
            "originalText": text,
            "finalText": text,
            "applied": [],
            "skipped": [
                {"findingId": finding.id, "reason": "source-text mode permits annotation but forbids mutation"}
                for finding in before.findings
            ],
            "auditBefore": before.to_dict(),
            "auditAfter": before.to_dict(),
            "integrity": integrity.to_dict(),
            "integrityEffective": True,
            "contextualExemption": False,
            "byteExactNoOp": True,
            "reason": "source text returned byte-for-byte",
        }
    if before.status == "not_assessable":
        # Masking removed most of the document from the audit, so there is no basis for
        # deciding which spans are safe to touch. Return the text untouched and say why.
        return {
            "mode": "repair",
            "genre": selected.id,
            "sourceTextMode": False,
            "changed": False,
            "originalText": text,
            "finalText": text,
            "applied": [],
            "skipped": [
                {"findingId": finding.id, "reason": "document not assessable: most of it is protected content"}
                for finding in before.findings
            ],
            "auditBefore": before.to_dict(),
            "auditAfter": before.to_dict(),
            "integrity": verify_integrity(text, text, selected.integrity_policy).to_dict(),
            "integrityEffective": True,
            "contextualExemption": False,
            "byteExactNoOp": True,
            "reason": (
                f"{before.metrics['protectedCoverage']:.0%} of the document is code, quotation "
                "or tabular content; too little prose remains to repair safely"
            ),
        }
    if before.clean:
        return {
            "mode": "repair",
            "genre": selected.id,
            "sourceTextMode": False,
            "changed": False,
            "originalText": text,
            "finalText": text,
            "applied": [],
            "skipped": [],
            "auditBefore": before.to_dict(),
            "auditAfter": before.to_dict(),
            "integrity": verify_integrity(text, text, selected.integrity_policy).to_dict(),
            "integrityEffective": True,
            "contextualExemption": False,
            "byteExactNoOp": True,
        }

    proposed: list[tuple[Any, Candidate]] = []
    skipped: list[dict[str, Any]] = []
    for finding in before.findings:
        if finding.reported_voice or finding.severity == "review":
            skipped.append({"findingId": finding.id, "reason": "source-reported or review-level finding requires author judgment"})
            continue
        candidate = _top_exact_candidate(text, finding, selected, before, voice_profile)
        if candidate:
            proposed.append((finding, candidate))
        else:
            skipped.append({"findingId": finding.id, "reason": "no deterministic replacement cleared all gates"})

    # Resolve overlapping edits by expected value, then apply right-to-left so offsets
    # from the original remain valid. Lower-offset edits are unaffected by changes to
    # text occurring later in the document.
    proposed.sort(key=lambda item: (item[1].score, item[0].confidence), reverse=True)
    chosen: list[tuple[Any, Candidate]] = []
    occupied: list[tuple[int, int]] = []
    for finding, candidate in proposed:
        assert candidate.start is not None and candidate.end is not None
        if any(candidate.start < end and start < candidate.end for start, end in occupied):
            skipped.append({"findingId": finding.id, "reason": "overlaps a higher-ranked edit"})
            continue
        occupied.append((candidate.start, candidate.end))
        chosen.append((finding, candidate))

    current = text
    applied: list[dict[str, Any]] = []
    for finding, candidate in sorted(chosen, key=lambda item: item[1].start or 0, reverse=True):
        start, end = candidate.start, candidate.end
        assert start is not None and end is not None and candidate.replacement is not None
        trial = _boundary_apply(current, start, end, candidate.replacement)
        integrity = verify_integrity(text, trial, selected.integrity_policy)
        trial_audit = audit_text(trial, genre=selected.id, include_quoted=include_quoted)
        candidate_integrity = candidate.integrity or {}
        applied_record = {
            "findingId": finding.id,
            "patternId": finding.pattern_id,
            "start": start,
            "end": end,
            "original": text[start:end],
            "replacement": candidate.replacement,
            "contextualOverride": bool(candidate_integrity.get("contextualOverride")),
            "contextualOverrideCategories": list(candidate_integrity.get("contextualOverrideCategories", [])),
            "allowAggregateLength": bool(
                candidate.replacement == ""
                and finding.source == "surface"
                and finding.confidence >= 0.85
                and finding.pattern_id in _AGGREGATE_LENGTH_DELETIONS
            ),
        }
        trial_applied = applied + [applied_record]
        if not _integrity_effective(integrity, trial_applied) or trial_audit.editorial_burden > before.editorial_burden + 0.05:
            skipped.append({"findingId": finding.id, "reason": "combined edit failed the full-document acceptance gate"})
            continue
        current = trial
        applied.append(applied_record)

    after = audit_text(current, genre=selected.id, include_quoted=include_quoted)
    integrity = verify_integrity(text, current, selected.integrity_policy)
    # Absolute final gate. A chain of individually safe edits can still interact.
    if not _integrity_effective(integrity, applied) or after.editorial_burden > before.editorial_burden + 0.05:
        current = text
        after = before
        applied = []
        skipped.append({"findingId": None, "reason": "final combined candidate failed; returned original byte-for-byte"})
        integrity = verify_integrity(text, text, selected.integrity_policy)

    effective_integrity = _integrity_effective(integrity, applied) if current != text else True
    contextual_exemption = effective_integrity and not integrity.passes

    return {
        "mode": "repair",
        "genre": selected.id,
        "sourceTextMode": False,
        "changed": current != text,
        "originalText": text,
        "finalText": current,
        "applied": list(reversed(applied)),
        "skipped": skipped,
        "auditBefore": before.to_dict(),
        "auditAfter": after.to_dict(),
        "integrity": integrity.to_dict(),
        "integrityEffective": effective_integrity,
        "contextualExemption": contextual_exemption,
        "byteExactNoOp": current == text,
    }


def verify_text(original: str, candidate: str, genre: str | None = "auto") -> dict[str, Any]:
    selected = _select_genre(original, genre)
    integrity = verify_integrity(original, candidate, selected.integrity_policy)
    before = audit_text(original, genre=selected.id)
    after = audit_text(candidate, genre=selected.id)
    before_unsafe = Counter((f.pattern_id, f.severity) for f in before.findings if f.severity in {"hard", "review"})
    after_unsafe = Counter((f.pattern_id, f.severity) for f in after.findings if f.severity in {"hard", "review"})
    introduced = after_unsafe - before_unsafe
    burden_delta = after.editorial_burden - before.editorial_burden

    contextual_exemption = False
    if not integrity.passes:
        # A semantic exception is accepted only when the candidate is exactly the
        # deterministic repair produced by the same bounded rules. Arbitrary model
        # prose cannot claim the exception merely because it deletes similar words.
        deterministic = repair_text(original, genre=selected.id)
        contextual_exemption = bool(
            deterministic.get("changed")
            and deterministic.get("contextualExemption")
            and deterministic.get("integrityEffective")
            and deterministic.get("finalText") == candidate
        )
    effective_integrity = integrity.passes or contextual_exemption
    passes = effective_integrity and not introduced and burden_delta <= 0.05
    if passes and contextual_exemption:
        reason = "accepted as the exact bounded deterministic repair"
    elif passes:
        reason = "accepted"
    else:
        reason = "candidate failed one or more preservation, damage or net-improvement gates"
    return {
        "passes": passes,
        "genre": selected.id,
        "integrity": integrity.to_dict(),
        "integrityEffective": effective_integrity,
        "contextualExemption": contextual_exemption,
        "editorialBurdenBefore": round(before.editorial_burden, 1),
        "editorialBurdenAfter": round(after.editorial_burden, 1),
        "burdenDelta": round(burden_delta, 1),
        "introducedUnsafeFindings": [
            {"patternId": pattern, "severity": severity, "count": count}
            for (pattern, severity), count in sorted(introduced.items())
        ],
        "reason": reason,
    }


def _pre_tournament_guard(
    text: str,
    before: AuditReport,
    selected: GenreProfile,
    source_text: bool,
) -> dict[str, Any] | None:
    """The three states in which no rewrite may be attempted, and the reason for each.

    Shared by the callback path and the browser path so the two cannot drift into
    disagreeing about when mutation is permitted.
    """
    def payload(reason: str, mode_extra: dict[str, Any] | None = None) -> dict[str, Any]:
        out = {
            "mode": "rewrite",
            "genre": selected.id,
            "changed": False,
            "finalText": text,
            "auditBefore": before.to_dict(),
            "auditAfter": before.to_dict(),
            "attempts": [],
            "reason": reason,
        }
        out.update(mode_extra or {})
        return out

    if source_text:
        return payload("source-text mode permits annotation but forbids mutation",
                       {"sourceTextMode": True})
    if before.status == "not_assessable":
        return payload(
            f"{before.metrics['protectedCoverage']:.0%} of the document is code, quotation "
            "or tabular content; too little prose remains to rewrite safely",
            {"notAssessable": True},
        )
    if before.clean:
        return payload("clean input returned byte-for-byte")
    return None


def rewrite_with_callback(
    text: str,
    callback: RevisionCallback,
    genre: str | None = "auto",
    *,
    candidates: int = 3,
    voice_profile: VoiceProfile | dict | str | None = None,
    voice_notes: Iterable[str] = (),
    source_text: bool = False,
) -> dict[str, Any]:
    selected = _select_genre(text, genre)
    before = audit_text(text, genre=selected.id)
    guard = _pre_tournament_guard(text, before, selected, source_text)
    if guard is not None:
        return guard
    contract = compile_revision_contract(text, before, selected, voice_notes=voice_notes)
    attempts: list[RewriteAttempt] = []
    raw_candidates: list[tuple[str, str]] = []
    for index in range(1, max(1, candidates) + 1):
        variant_contract = candidate_contract(contract, index)
        try:
            raw_candidates.append((f"model-{index}", callback(variant_contract, text)))
        except Exception as exc:  # provider failures must not erase the safe baseline
            attempts.append(RewriteAttempt(index, False, f"provider error: {exc}", None, None, None))
    return run_tournament(
        text, raw_candidates, selected, before, contract,
        voice_profile=voice_profile, prior_attempts=attempts,
    )


def candidate_contract(contract: str, index: int) -> str:
    """The per-candidate instruction. Shared so an out-of-process generator — the
    browser calling a provider directly, for instance — sends the same contract the
    in-process callback path sends."""
    return contract + f"\n\nCANDIDATE {index}: Use a distinct minimal solution; do not paraphrase merely to differ."


def rewrite_with_candidates(
    text: str,
    candidates: Sequence[str],
    genre: str | None = None,
    *,
    voice_profile: VoiceProfile | dict | str | None = None,
    voice_notes: Iterable[str] = (),
    source_text: bool = False,
) -> dict[str, Any]:
    """Run the tournament over candidates generated elsewhere.

    The browser build needs this. Pyodide has no sockets, so the provider call happens
    in JavaScript and the API key never reaches any server, ours included. The gates
    that decide whether a candidate is acceptable still run here, in the same code the
    server path uses — the generator moved, the adjudication did not.
    """
    selected = _select_genre(text, genre)
    before = audit_text(text, genre=selected.id)
    guard = _pre_tournament_guard(text, before, selected, source_text)
    if guard is not None:
        return guard
    contract = compile_revision_contract(text, before, selected, voice_notes=voice_notes)
    raw = [(f"model-{i}", c) for i, c in enumerate(candidates, 1) if c and c.strip()]
    return run_tournament(text, raw, selected, before, contract,
                          voice_profile=voice_profile, prior_attempts=[])


def run_tournament(
    text: str,
    raw_candidates: list[tuple[str, str]],
    selected: GenreProfile,
    before: AuditReport,
    contract: str,
    *,
    voice_profile: VoiceProfile | dict | str | None = None,
    prior_attempts: list[RewriteAttempt] | None = None,
) -> dict[str, Any]:
    attempts: list[RewriteAttempt] = list(prior_attempts or [])
    survivors: list[tuple[float, str, AuditReport, dict[str, Any], float | None]] = []

    # The deterministic repair is a cheap baseline and enters the same tournament.
    deterministic = repair_text(text, genre=selected.id, voice_profile=voice_profile)
    if deterministic["changed"]:
        raw_candidates = [("deterministic", deterministic["finalText"])] + list(raw_candidates)

    for ordinal, (label, candidate) in enumerate(raw_candidates, 1):
        verification = verify_text(text, candidate, selected.id)
        integrity_payload = verification["integrity"]
        after = audit_text(candidate, genre=selected.id)
        if not verification["passes"]:
            attempts.append(RewriteAttempt(ordinal, False, f"{label}: preservation, damage or net-improvement gate failed", after.editorial_burden, integrity_payload, None))
            continue
        reduction = before.editorial_burden - after.editorial_burden
        if reduction < 0.5:
            attempts.append(RewriteAttempt(ordinal, False, f"{label}: no material improvement", after.editorial_burden, integrity_payload, None))
            continue
        vdistance = voice_distance(voice_profile, candidate).score if voice_profile is not None else None
        if vdistance is not None and vdistance > 50:
            attempts.append(RewriteAttempt(ordinal, False, f"{label}: voice gate failed", after.editorial_burden, integrity_payload, vdistance))
            continue
        length_penalty = abs(len(candidate.split()) / max(1, len(text.split())) - 1.0)
        score = 2.0 * reduction - 20.0 * length_penalty - (vdistance or 0.0) * 0.25
        survivors.append((score, candidate, after, integrity_payload, vdistance))
        attempts.append(RewriteAttempt(ordinal, True, f"{label}: cleared all gates", after.editorial_burden, integrity_payload, vdistance))

    if not survivors:
        return {
            "mode": "rewrite",
            "genre": selected.id,
            "changed": False,
            "finalText": text,
            "auditBefore": before.to_dict(),
            "auditAfter": before.to_dict(),
            "attempts": [attempt.to_dict() for attempt in attempts],
            "contract": contract,
            "reason": "no candidate cleared every gate; original returned",
        }
    survivors.sort(key=lambda row: row[0], reverse=True)
    _, best, after, integrity_payload, vdistance = survivors[0]
    return {
        "mode": "rewrite",
        "genre": selected.id,
        "changed": best != text,
        "finalText": best,
        "auditBefore": before.to_dict(),
        "auditAfter": after.to_dict(),
        "integrity": integrity_payload,
        "voiceDistance": vdistance,
        "attempts": [attempt.to_dict() for attempt in attempts],
        "contract": contract,
        "reason": "highest-ranked candidate clearing every gate",
    }


def draft_with_callback(
    brief: WritingBrief,
    callback: RevisionCallback,
    *,
    candidates: int = 3,
    voice_profile: VoiceProfile | dict | str | None = None,
) -> dict[str, Any]:
    """Draft from a bounded brief and rank only candidates that clear hard gates."""
    selected = get_genre(brief.genre)
    contract = compile_draft_contract(brief, selected)
    attempts: list[dict[str, Any]] = []
    survivors: list[tuple[float, str, AuditReport, dict[str, Any], float | None]] = []
    for index in range(1, max(1, candidates) + 1):
        try:
            text = callback(contract + f"\n\nDRAFT CANDIDATE {index}: solve the same brief without ornamental variation.", "")
            report = audit_text(text, genre=selected.id)
            evidence = verify_draft_evidence(brief, text)
            hard = [finding for finding in report.findings if finding.severity == "hard"]
            review = [finding for finding in report.findings if finding.severity == "review"]
            unresolved = text.count("[AUTHOR INPUT:")
            vdistance = voice_distance(voice_profile, text).score if voice_profile is not None else None
            accepted = not hard and not review and evidence.passes and (vdistance is None or vdistance <= 50)
            attempt = {
                "index": index,
                "accepted": accepted,
                "hardFindings": len(hard),
                "reviewFindings": len(review),
                "editorialBurden": round(report.editorial_burden, 1),
                "authorInputMarkers": unresolved,
                "evidenceBoundary": evidence.to_dict(),
                "voiceDistance": None if vdistance is None else round(vdistance, 2),
            }
            if not accepted:
                reasons: list[str] = []
                if hard:
                    reasons.append("hard editorial findings")
                if review:
                    reasons.append("unresolved review-level claims")
                if not evidence.passes:
                    reasons.append("unsupported factual anchors")
                if vdistance is not None and vdistance > 50:
                    reasons.append("voice drift")
                attempt["reason"] = ", ".join(reasons) or "candidate failed a release gate"
            attempts.append(attempt)
            if accepted:
                score = -report.editorial_burden - unresolved * 2 - (vdistance or 0.0) * 0.2
                survivors.append((score, text, report, evidence.to_dict(), vdistance))
        except Exception as exc:
            attempts.append({"index": index, "accepted": False, "error": str(exc)})
    if not survivors:
        return {
            "mode": "draft",
            "accepted": False,
            "finalText": "",
            "attempts": attempts,
            "contract": contract,
            "reason": "no draft candidate cleared editorial, evidence and voice gates",
        }
    survivors.sort(key=lambda row: row[0], reverse=True)
    _, text, report, evidence, vdistance = survivors[0]
    return {
        "mode": "draft",
        "accepted": True,
        "finalText": text,
        "audit": report.to_dict(),
        "evidenceBoundary": evidence,
        "voiceDistance": vdistance,
        "attempts": attempts,
        "contract": contract,
        "reason": "highest-ranked draft clearing every configured gate",
    }
