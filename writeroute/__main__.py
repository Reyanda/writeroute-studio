"""Command-line interface for WriteRoute."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .audit import audit_text
from .contracts import WritingBrief, compile_draft_contract, compile_revision_contract
from .genres import get_genre, load_genres
from .patterns import pattern_catalogue
from .route import draft_with_callback, repair_text, rewrite_with_callback, suggest_text, verify_text
from .voice import build_voice_profile, load_voice_profile, save_voice_profile, voice_distance


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _write_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _human_audit(report) -> None:
    data = report.to_dict()
    counts = data["counts"]
    print(f"WriteRoute audit | genre={data['genre']} | burden={data['editorialBurden']:.1f}/100 | {data['status']}")
    print(f"{counts['words']} words; {counts['findings']} findings ({counts['hard']} hard, {counts['review']} review, {counts['soft']} soft)")
    if not data["findings"]:
        print("No editorial defect crossed the configured threshold.")
        return
    for finding in data["findings"]:
        span = finding["span"]
        quote = " ".join(finding["original"].split())
        if len(quote) > 120:
            quote = quote[:117] + "..."
        print(f"\n{finding['id']} [{finding['severity']}] {finding['title']} @{span['start']}:{span['end']}")
        print(f"  “{quote}”")
        print(f"  {finding['rationale']}")
        print(f"  Fix: {finding['action']}")


def _callback(provider: str, model: str | None):
    from aiwd.llm import get_callback
    return get_callback(provider, model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="writeroute", description="Preservation-first professional writing and editing route")
    parser.add_argument("--version", action="version", version="WriteRoute 2.0.0")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="audit observable editorial defects; never infer authorship")
    audit.add_argument("file")
    audit.add_argument("--genre", default="auto")
    audit.add_argument("--include-quoted", action="store_true")
    audit.add_argument("--json", action="store_true")
    audit.add_argument("--fail-over", type=float, default=None, metavar="BURDEN")

    suggest = sub.add_parser("suggest", help="rank exact edits and evidence-aware rewrite frames")
    suggest.add_argument("file")
    suggest.add_argument("--genre", default="auto")
    suggest.add_argument("--voice-profile")
    suggest.add_argument("--include-quoted", action="store_true")
    suggest.add_argument("--source-text", action="store_true", help="annotate a source document but forbid mutation")
    suggest.add_argument("--max-candidates", type=int, default=3)
    suggest.add_argument("--json", action="store_true")

    repair = sub.add_parser("repair", help="apply only deterministic edits that clear every gate")
    repair.add_argument("file")
    repair.add_argument("--genre", default="auto")
    repair.add_argument("--voice-profile")
    repair.add_argument("--include-quoted", action="store_true")
    repair.add_argument("--source-text", action="store_true", help="return source byte-for-byte; report findings only")
    repair.add_argument("-o", "--output")
    repair.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="compare a candidate with its source")
    verify.add_argument("original")
    verify.add_argument("candidate")
    verify.add_argument("--genre", default="auto")
    verify.add_argument("--json", action="store_true")

    draft = sub.add_parser("draft", help="draft from an evidence-bounded brief under editorial and voice gates")
    draft.add_argument("--genre", required=True)
    draft.add_argument("--audience", default="")
    draft.add_argument("--purpose", required=True)
    draft.add_argument("--reader-action", default="")
    draft.add_argument("--evidence", action="append", default=[])
    draft.add_argument("--evidence-file", action="append", default=[])
    draft.add_argument("--constraint", action="append", default=[])
    draft.add_argument("--voice-note", action="append", default=[])
    draft.add_argument("--voice-profile")
    draft.add_argument("--length", default="")
    draft.add_argument("--language", default="English")
    draft.add_argument("--provider", choices=["deepseek", "claude", "compatible"], default="deepseek")
    draft.add_argument("--model")
    draft.add_argument("--candidates", type=int, default=3)
    draft.add_argument("-o", "--output")
    draft.add_argument("--json", action="store_true")

    rewrite = sub.add_parser("rewrite", help="run a multi-candidate LLM tournament under hard gates")
    rewrite.add_argument("file")
    rewrite.add_argument("--genre", default="auto")
    rewrite.add_argument("--provider", choices=["deepseek", "claude", "compatible"], default="deepseek")
    rewrite.add_argument("--model")
    rewrite.add_argument("--candidates", type=int, default=3)
    rewrite.add_argument("--voice-profile")
    rewrite.add_argument("--source-text", action="store_true", help="forbid all model and deterministic mutations")
    rewrite.add_argument("-o", "--output")
    rewrite.add_argument("--json", action="store_true")

    profile = sub.add_parser("profile", help="build a voice profile from approved human samples")
    profile.add_argument("source")
    profile.add_argument("-o", "--output", required=True)
    profile.add_argument("--name", default="default")
    profile.add_argument("--strict", action="store_true")

    voice = sub.add_parser("voice-check", help="measure drift from a saved voice profile")
    voice.add_argument("profile")
    voice.add_argument("file")
    voice.add_argument("--json", action="store_true")

    contract = sub.add_parser("contract", help="compile an author-mode draft or revision contract")
    contract.add_argument("--genre", required=True)
    contract.add_argument("--audience", default="")
    contract.add_argument("--purpose", default="")
    contract.add_argument("--reader-action", default="")
    contract.add_argument("--evidence", action="append", default=[])
    contract.add_argument("--constraint", action="append", default=[])
    contract.add_argument("--voice-note", action="append", default=[])
    contract.add_argument("--length", default="")
    contract.add_argument("--revise", metavar="FILE")

    sub.add_parser("genres", help="list genre profiles")
    sub.add_parser("patterns", help="list surface pattern catalogue")

    benchmark = sub.add_parser("benchmark", help="run the included frozen regression benchmark")
    benchmark.add_argument("--json", action="store_true")

    sub.add_parser("engines", help="show which engines are installed and what each needs")

    trace = sub.add_parser("trace", help="convert a raster image to SVG [needs the tracer extra]")
    trace.add_argument("image", help="PNG or JPG to convert")
    trace.add_argument("-o", "--output", help="destination SVG (default: alongside the input)")
    trace.add_argument("--mode", choices=("vector", "parity", "wrapper"), default="parity",
                       help="pure vector, vector plus a raster residual where it differs, "
                            "or the original embedded at exact dimensions")
    trace.add_argument("--preset", help="named tracing preset; see --list-presets")
    trace.add_argument("--remove-background", action="store_true")
    trace.add_argument("--list-presets", action="store_true")
    trace.add_argument("--json", action="store_true")

    pdf = sub.add_parser("pdf", help="detect, fill and annotate PDF form fields [needs the pdf extra]")
    pdf_sub = pdf.add_subparsers(dest="pdf_command", required=True)
    pdf_detect = pdf_sub.add_parser("detect", help="find form fields and report them")
    pdf_detect.add_argument("file")
    pdf_detect.add_argument("-o", "--output", help="write the field map as JSON")
    pdf_detect.add_argument("--json", action="store_true")
    pdf_fill = pdf_sub.add_parser("fill", help="fill detected fields from a JSON value map")
    pdf_fill.add_argument("file")
    pdf_fill.add_argument("values", help="JSON file of field id to value")
    pdf_fill.add_argument("-o", "--output", required=True)
    pdf_unbundle = pdf_sub.add_parser("unbundle", help="dump raw page primitives as JSON")
    pdf_unbundle.add_argument("file")
    pdf_unbundle.add_argument("-o", "--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # The optional engines are dispatched first and imported inside their handlers, so a
    # prose-only install never loads a native dependency it does not have.
    if args.command in {"engines", "trace", "pdf"}:
        from .engines import run_engine_command
        from .optional import MissingDependency
        try:
            return run_engine_command(args)
        except MissingDependency as exc:
            print(str(exc), file=sys.stderr)
            return 3

    if args.command == "audit":
        report = audit_text(_read(args.file), args.genre, include_quoted=args.include_quoted)
        _write_json(report.to_dict()) if args.json else _human_audit(report)
        return 2 if args.fail_over is not None and report.editorial_burden > args.fail_over else 0

    if args.command == "suggest":
        profile = load_voice_profile(args.voice_profile) if args.voice_profile else None
        payload = suggest_text(
            _read(args.file), args.genre,
            include_quoted=args.include_quoted,
            voice_profile=profile,
            max_candidates=args.max_candidates,
            source_text=args.source_text,
        )
        if args.json:
            _write_json(payload)
        else:
            print(f"WriteRoute suggestions | burden={payload['editorialBurden']:.1f}/100 | safe exact edits={payload['safeReplacementCount']} | author-input frames={payload['authorInputCount']}")
            for finding in payload["findings"]:
                print(f"\n{finding['id']} [{finding['severity']}] {finding['title']}: {finding['original']!r}")
                for candidate in finding["candidates"]:
                    mark = "AUTO" if candidate["safeToApply"] else "FRAME" if candidate["requiresAuthorInput"] else "BLOCKED"
                    print(f"  {mark}: {candidate['preview']}")
                    if candidate["rationale"]:
                        print(f"    {candidate['rationale']}")
        return 0

    if args.command == "repair":
        profile = load_voice_profile(args.voice_profile) if args.voice_profile else None
        payload = repair_text(
            _read(args.file), args.genre,
            include_quoted=args.include_quoted,
            voice_profile=profile,
            source_text=args.source_text,
        )
        if args.output:
            Path(args.output).write_text(payload["finalText"], encoding="utf-8")
        if args.json:
            _write_json(payload)
        elif not args.output:
            print(payload["finalText"])
        else:
            print(f"wrote {args.output}; {len(payload['applied'])} edits applied; changed={payload['changed']}")
        return 0

    if args.command == "verify":
        payload = verify_text(_read(args.original), _read(args.candidate), args.genre)
        if args.json:
            _write_json(payload)
        else:
            print("PASS" if payload["passes"] else "FAIL")
            print(payload["reason"])
            print(f"burden: {payload['editorialBurdenBefore']:.1f} -> {payload['editorialBurdenAfter']:.1f}")
            for item in payload["integrity"]["violations"]:
                print(f"- [{item['severity']}] {item['message']}")
        return 0 if payload["passes"] else 3

    if args.command == "draft":
        evidence = list(args.evidence)
        for path in args.evidence_file:
            evidence.append(_read(path))
        brief = WritingBrief.create(
            genre=args.genre,
            audience=args.audience,
            purpose=args.purpose,
            reader_action=args.reader_action,
            evidence=evidence,
            constraints=args.constraint,
            voice_notes=args.voice_note,
            length=args.length,
            language=args.language,
        )
        profile = load_voice_profile(args.voice_profile) if args.voice_profile else None
        payload = draft_with_callback(
            brief, _callback(args.provider, args.model),
            candidates=args.candidates, voice_profile=profile,
        )
        if args.output and payload["accepted"]:
            Path(args.output).write_text(payload["finalText"], encoding="utf-8")
        if args.json:
            _write_json(payload)
        elif payload["accepted"] and not args.output:
            print(payload["finalText"])
        elif payload["accepted"]:
            print(f"wrote {args.output}; {payload['reason']}")
        else:
            print(payload["reason"], file=sys.stderr)
        return 0 if payload["accepted"] else 4

    if args.command == "rewrite":
        profile = load_voice_profile(args.voice_profile) if args.voice_profile else None
        payload = rewrite_with_callback(
            _read(args.file), _callback(args.provider, args.model), args.genre,
            candidates=args.candidates, voice_profile=profile, source_text=args.source_text,
        )
        if args.output:
            Path(args.output).write_text(payload["finalText"], encoding="utf-8")
        if args.json:
            _write_json(payload)
        elif not args.output:
            print(payload["finalText"])
        else:
            print(f"wrote {args.output}; changed={payload['changed']}; {payload['reason']}")
        return 0

    if args.command == "profile":
        profile = build_voice_profile(args.source, name=args.name, strict=args.strict)
        save_voice_profile(profile, args.output)
        print(f"wrote {args.output}; {profile.sample_count} samples, {profile.word_count} words")
        for warning in profile.warnings:
            print(f"warning: {warning}")
        return 0

    if args.command == "voice-check":
        payload = voice_distance(load_voice_profile(args.profile), _read(args.file)).to_dict()
        if args.json:
            _write_json(payload)
        else:
            print(f"voice distance={payload['score']:.2f}/100 ({payload['interpretation']})")
            for row in payload["metricDeltas"]:
                print(f"- {row['metric']}: observed {row['observed']}, profile {row['profile']} (z={row['standardizedDelta']})")
        return 0

    if args.command == "contract":
        genre = get_genre(args.genre)
        if args.revise:
            text = _read(args.revise)
            report = audit_text(text, genre=genre.id)
            print(compile_revision_contract(text, report, genre, voice_notes=args.voice_note))
        else:
            brief = WritingBrief.create(
                genre=genre.id, audience=args.audience, purpose=args.purpose,
                reader_action=args.reader_action, evidence=args.evidence,
                constraints=args.constraint, voice_notes=args.voice_note, length=args.length,
            )
            print(compile_draft_contract(brief, genre))
        return 0

    if args.command == "genres":
        _write_json([
            {"id": p.id, "name": p.name, "purpose": p.purpose, "audience": p.audience, "requiredMoves": list(p.required_moves)}
            for p in load_genres().values()
        ])
        return 0

    if args.command == "patterns":
        _write_json(pattern_catalogue())
        return 0

    if args.command == "benchmark":
        from evals.run import run_benchmark
        payload = run_benchmark()
        if args.json:
            _write_json(payload)
        else:
            print(payload["summary"])
            for key, value in payload["metrics"].items():
                print(f"{key}: {value}")
        return 0 if payload["shipGate"] else 4

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
