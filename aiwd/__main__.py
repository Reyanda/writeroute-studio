"""CLI for aiwd.

  python3 -m aiwd scan <file> [--json] [--baselines FILE] [--genre G] [--fail-over X]
  python3 -m aiwd clean <file> [--apply] [-o OUT] [--json]
  python3 -m aiwd improve <file> [--model M] [--max-iter N] [--genre G]
  python3 -m aiwd calibrate <dir> [-o OUT]
  python3 -m aiwd packs
  python3 -m aiwd ontology
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .rewrite import clean_text
from .scoring import calibrate, load_baselines, scan_text
from .skillengine import SkillRegistry

BAR = 24


def _bar(x: float) -> str:
    filled = round(x * BAR)
    return "█" * filled + "░" * (BAR - filled)


def _registry(args) -> SkillRegistry:
    overrides = None
    if getattr(args, "baselines", None):
        overrides = load_baselines(Path(args.baselines))
    return SkillRegistry.load(baseline_overrides=overrides)


GENRES = ("institutional", "clinical", "submission", "technical",
          "tutorial", "documentation")


def _sanity(report: dict) -> list[str]:
    """Warn when the unit of analysis makes the statistics meaningless.

    Several features measure paragraph and sentence shape. Hand them a file of
    fragments — a bad extraction, a list of headings, a CSV column — and they will
    return confident numbers about a thing that is not prose. Better to say so.
    """
    warn = []
    tokens, paras = report["tokenCount"], report["paragraphCount"]
    sents = report["sentenceCount"]
    if paras and tokens / paras < 12:
        warn.append(f"median passage is ~{tokens // max(1, paras)} tokens: this looks like "
                    f"fragments rather than prose, so paragraph and burstiness features "
                    f"are not measuring what they claim")
    if tokens < 300:
        warn.append(f"only {tokens} tokens: too short for the probabilistic family to mean much")
    if sents and tokens / sents < 6:
        warn.append("very short sentences throughout: check the text is not a list or a table")
    return warn


def cmd_scan(args) -> int:
    text = Path(args.file).read_text(errors="replace")
    genre = getattr(args, "genre", "") or ""
    from .allowlist import load_allowlist
    allowlist = load_allowlist(enabled=not getattr(args, "no_allowlist", False))
    report = scan_text(text, _registry(args), genre=genre, allowlist=allowlist,
                       reported_guard=not getattr(args, "no_reported_guard", False))
    report["genre"] = genre or "unspecified"
    report["sanityWarnings"] = _sanity(report)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return _gate(report, args)
    dr = report["detectionResult"]
    print(f"aiwd v{__version__} — {args.file}")
    print(f"  tokens {report['tokenCount']}  paragraphs {report['paragraphCount']}  "
          f"sentences {report['sentenceCount']}\n")
    print(f"  AI probability  {dr['globalAiProbability']:.2f}  {_bar(dr['globalAiProbability'])}")
    print(f"  confidence      {dr['globalConfidence']:.2f}")
    print(f"  decision        {dr['decisionLabel']}\n")
    print("  Family scores (0=human-like, 1=AI-like):")
    for fs in dr["familyScores"]:
        print(f"    {fs['family']:<24} {fs['familyAiScore']:.2f}  {_bar(fs['familyAiScore'])}")
    print("\n  Top signals:")
    for e in dr["explainedBy"]:
        print(f"    [{e['aiLikenessContribution']:.2f}] {e['featureType']} ({e['family']})")
        if e["note"]:
            print(f"           {e['note']}")
    evidence = [
        (f["featureType"], h["text"])
        for f in report["features"] if f["aiLikenessContribution"] > 0.6
        for h in f["evidence"][:2]
    ]
    if evidence:
        print("\n  Sample evidence:")
        for fid, quote in evidence[:8]:
            print(f"    {fid}: “{quote.strip()}”")
    exempt = report.get("allowListExemptions", [])
    if exempt:
        total = sum(r["count"] for r in exempt)
        print(f"\n  Allow-listed — {total} hits excused as field-standard, not counted:")
        for r in exempt:
            eg = ", ".join(f"\u201c{e}\u201d" for e in r["examples"])
            print(f"    {r['featureType']} / {r['allowListEntry']} x{r['count']}: {eg}")
            print(f"      {r['reason']}")
        print("    Re-run with --no-allowlist to score these as slop.")
    quoted = report.get("reportedVoiceDiscounts", [])
    if quoted:
        total = sum(r["count"] for r in quoted)
        frac = report.get("reportedVoiceFraction", 0.0)
        print(f"\n  Reported voice — {total} hits discounted as quoted or attributed "
              f"({frac:.0%} of the text):")
        for r in quoted:
            eg = ", ".join(f"\u201c{e}\u201d" for e in r["examples"])
            print(f"    {r['featureType']} / {r['kind']} x{r['count']}: {eg}")
        print("    Someone else's words are not evidence about this author.")
        print("    Re-run with --no-reported-guard to score them anyway.")
    declined = [f for f in report["features"] if f.get("declinedForGenre")]
    if declined:
        print(f"\n  Declined for the {genre} genre — these measure the register, not slop:")
        for f in declined:
            print(f"    {f['featureType']} ({f['family']})")
    silent = [f for f in report["features"] if not f.get("countsTowardsScore", True)
              and not f.get("declinedForGenre")]
    if silent:
        print(f"\n  Checked and found nothing: {len(silent)} features "
              f"(reported, not scored — an absence is not evidence)")
    for w in report.get("sanityWarnings", []):
        print(f"\n  ! {w}")
    print(f"\n  Note: {report['caveat']}")
    return _gate(report, args)


def _gate(report: dict, args) -> int:
    """Exit non-zero when the score breaches a declared ceiling.

    A rule a script enforces cannot be forgotten; a rule in a README competes with
    whatever the writer is doing and loses. `--fail-over` is what makes this
    usable in a build.
    """
    ceiling = getattr(args, "fail_over", None)
    if ceiling is None:
        return 0
    p = report["detectionResult"]["globalAiProbability"]
    if p > ceiling:
        print(f"\n  FAIL — AI probability {p:.3f} exceeds the ceiling {ceiling:.2f}",
              file=sys.stderr)
        return 1
    return 0


def cmd_clean(args) -> int:
    text = Path(args.file).read_text(errors="replace")
    result = clean_text(text, _registry(args), apply=args.apply)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(f"aiwd clean — {args.file}: {len(result['suggestions'])} suggestion(s), "
          f"{result['appliedCount']} auto-applied")
    for s in result["suggestions"]:
        opts = " | ".join(f"“{o}”" if o else "(delete)" for o in s["options"]) or "(rewrite manually)"
        auto = " [auto]" if s["autoApplicable"] else ""
        print(f"  “{s['original']}” → {opts}{auto}")
        print(f"      {s['featureType']}: {s['rationale']}")
    if args.apply and result["cleanedText"] is not None:
        out = Path(args.out) if args.out else Path(args.file).with_suffix(".cleaned.txt")
        out.write_text(result["cleanedText"])
        print(f"\n  Cleaned text written to {out} (safe substitutions only; "
              "review remaining suggestions by hand).")
    return 0


def cmd_improve(args) -> int:
    text = Path(args.file).read_text(errors="replace")
    from .llm import get_callback
    from .revision import improve_text
    try:
        callback = get_callback(provider=args.provider, model=args.model)
        result = improve_text(text, callback, _registry(args),
                              genre=getattr(args, "genre", "") or "",
                              max_iterations=args.max_iter)
    except Exception as exc:
        print(f"LLM revision failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(f"aiwd improve — {args.file}")
    print(f"  AI probability  {result['scoreBefore']:.3f} → {result['scoreAfter']:.3f}"
          f"  ({'changed' if result['changed'] else 'unchanged'})")
    for i, it in enumerate(result["iterations"], 1):
        after = "—" if it["scoreAfter"] is None else f"{it['scoreAfter']:.3f}"
        print(f"  iteration {i}: {it['reason']}  ({it['scoreBefore']:.3f} → {after})")
        for v in it["violations"]:
            print(f"      rejected: {v}")
    if result["changed"]:
        out = Path(args.out) if args.out else Path(args.file).with_suffix(".improved.txt")
        out.write_text(result["finalText"])
        print(f"\n  Revised text written to {out} (invariants verified: numbers, "
              "normative modals, citations unchanged).")
    return 0


def cmd_calibrate(args) -> int:
    folder = Path(args.dir)
    paths = sorted(p for p in folder.glob("**/*") if p.suffix in {".txt", ".md"} and p.is_file())
    if len(paths) < 2:
        print("Need at least 2 .txt/.md human reference documents to calibrate.", file=sys.stderr)
        return 1
    result = calibrate(paths)
    out = Path(args.out) if args.out else Path("outputs") / "baselines.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"Calibrated {len(result['baselines'])} feature baselines from "
          f"{len(result['documents'])} documents → {out}")
    print("Use with: python3 -m aiwd scan <file> --baselines " + str(out))
    return 0


def cmd_packs(args) -> int:
    reg = SkillRegistry.load()
    print("Loaded packs:")
    for p in reg.packs:
        print(f"  - {p}")
    print(f"\n{len(reg.features)} features across {len(reg.families)} families:")
    by_family: dict[str, list[str]] = {}
    for f in reg.features.values():
        by_family.setdefault(f.family, []).append(f.id)
    for fam, ids in by_family.items():
        print(f"  {fam}: {', '.join(sorted(ids))}")
    return 0


def cmd_ontology(args) -> int:
    print((Path(__file__).parent / "data" / "ontology.json").read_text())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aiwd", description="AI writing detection & anti-AI toolkit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="score a document against the ontology")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.add_argument("--baselines")
    p.add_argument("--genre", choices=GENRES,
                   help="switch off features that measure this genre's own conventions")
    p.add_argument("--no-reported-guard", action="store_true",
                   help="score quoted and attributed text as the author's own")
    p.add_argument("--no-allowlist", action="store_true",
                   help="score field-standard terms as slop (disables domain allow-lists)")
    p.add_argument("--fail-over", type=float, metavar="X",
                   help="exit non-zero when the AI probability exceeds X")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("clean", help="suggest (and optionally apply safe) de-slop edits")
    p.add_argument("file")
    p.add_argument("--apply", action="store_true")
    p.add_argument("-o", "--out")
    p.add_argument("--json", action="store_true")
    p.add_argument("--baselines")
    p.set_defaults(fn=cmd_clean)

    p = sub.add_parser("improve", help="LLM revision loop with preservation gate (DeepSeek by default)")
    p.add_argument("file")
    p.add_argument("--provider", choices=("deepseek", "claude"), default="deepseek")
    p.add_argument("--model", default=None,
                   help="override the provider default (deepseek-chat / claude-opus-4-8)")
    p.add_argument("--max-iter", type=int, default=3)
    p.add_argument("--genre", choices=GENRES)
    p.add_argument("-o", "--out")
    p.add_argument("--json", action="store_true")
    p.add_argument("--baselines")
    p.set_defaults(fn=cmd_improve)

    p = sub.add_parser("calibrate", help="compute baselines from a folder of human reference texts")
    p.add_argument("dir")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("packs", help="list loaded skill packs and features")
    p.set_defaults(fn=cmd_packs)

    p = sub.add_parser("ontology", help="print the machine-readable ontology")
    p.set_defaults(fn=cmd_ontology)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
