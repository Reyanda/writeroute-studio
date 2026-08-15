from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .docx_engine import ManuscriptDocxEngine
from .guidelines import ReportingGuidelineRegistry
from .pipeline import AcademicWritingEngine
from .schemas import available_schemas, load_schema


def _read_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("The JSON configuration must contain an object at the root.")
    return value


def _write_json(value: Any, path: str | None) -> None:
    text = json.dumps(value, indent=2, ensure_ascii=False)
    if path:
        Path(path).write_text(text, encoding="utf-8")
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auctor",
        description="Auctor Academic Writing Engine for section-aware prose and preservation-first DOCX revision.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    text = sub.add_parser("text", help="Audit and safely copyedit a plain-text passage.")
    text.add_argument("input", help="UTF-8 text file, or - for standard input.")
    text.add_argument("--section", default="other")
    text.add_argument("--mode", default="copyedit", choices=["mechanical", "copyedit", "substantive", "developmental"])
    text.add_argument("--output", help="Write the channel bundle as JSON.")
    text.add_argument("--metadata", help="JSON metadata for study design and reporting-guideline routing.")
    text.add_argument("--no-negative-engine", action="store_true")


    draft_request = sub.add_parser("draft-request", help="Build a schema-bound provider request from an evidence packet.")
    draft_request.add_argument("evidence_packet")
    draft_request.add_argument("--output")

    draft_validate = sub.add_parser("draft-validate", help="Validate a provider response against a closed evidence packet.")
    draft_validate.add_argument("evidence_packet")
    draft_validate.add_argument("response")
    draft_validate.add_argument("--output")
    draft_validate.add_argument("--no-negative-engine", action="store_true")

    inspect = sub.add_parser("docx-inspect", help="Inspect Word structure, review state, and semantic tags.")
    inspect.add_argument("input")
    inspect.add_argument("--output")

    audit = sub.add_parser("docx-audit", help="Audit manuscript prose without changing the DOCX.")
    audit.add_argument("input")
    audit.add_argument("--output")
    audit.add_argument("--metadata", help="JSON metadata for study design and reporting-guideline routing.")
    audit.add_argument("--no-negative-engine", action="store_true")

    prepare = sub.add_parser("docx-prepare", help="Prepare and revise a manuscript through direct OOXML editing.")
    prepare.add_argument("input")
    prepare.add_argument("output")
    prepare.add_argument("--report")
    prepare.add_argument("--tags", help="JSON file with citations, references, bookmarks, and ref_fields arrays.")
    prepare.add_argument("--metadata", help="JSON metadata for study design and reporting-guideline routing.")
    prepare.add_argument("--author", default="Auctor Academic Writing Engine")
    prepare.add_argument("--initials", default="AWE")
    prepare.add_argument("--no-safe-edits", action="store_true")
    prepare.add_argument("--no-track-changes", action="store_true")
    prepare.add_argument("--no-comments", action="store_true")
    prepare.add_argument("--no-negative-engine", action="store_true")


    guidelines = sub.add_parser("guidelines", help="List versioned reporting-guideline profiles.")
    guidelines.add_argument("--output")

    schemas = sub.add_parser("schemas", help="List or export packaged JSON contracts.")
    schemas.add_argument("--name", help="Schema file name with or without the .json suffix.")
    schemas.add_argument("--output")

    finalize = sub.add_parser("docx-finalize", help="Accept or reject revisions and preserve or remove comments explicitly.")
    finalize.add_argument("input")
    finalize.add_argument("output")
    finalize.add_argument("--revisions", choices=["preserve", "accept", "reject"], default="accept")
    finalize.add_argument("--comments", choices=["preserve", "remove"], default="remove")
    finalize.add_argument("--report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "text":
            source = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
            engine = AcademicWritingEngine(use_negative_engine=not args.no_negative_engine)
            bundle = engine.process_text(
                source,
                section=args.section,
                mode=args.mode,
                metadata=_read_json(args.metadata),
            )
            _write_json(bundle.to_dict(), args.output)
            return 0


        if args.command == "draft-request":
            packet = _read_json(args.evidence_packet)
            engine = AcademicWritingEngine(use_negative_engine=False)
            _write_json(engine.prepare_draft_request(packet), args.output)
            return 0

        if args.command == "draft-validate":
            packet = _read_json(args.evidence_packet)
            response = _read_json(args.response)
            engine = AcademicWritingEngine(use_negative_engine=not args.no_negative_engine)
            bundle = engine.validate_draft_response(packet, response)
            _write_json(bundle.to_dict(), args.output)
            return 0 if not any(issue.severity == "critical" for issue in bundle.qc) else 2

        if args.command == "guidelines":
            registry = ReportingGuidelineRegistry()
            _write_json({"profiles": registry.available(), "scope_note": registry.data.get("scope_note", "")}, args.output)
            return 0

        if args.command == "schemas":
            if args.name:
                _write_json(load_schema(args.name), args.output)
            else:
                _write_json({"schemas": available_schemas()}, args.output)
            return 0

        if args.command == "docx-inspect":
            engine = ManuscriptDocxEngine()
            _write_json(engine.inspect(args.input), args.output)
            return 0

        if args.command == "docx-audit":
            engine = ManuscriptDocxEngine(use_negative_engine=not args.no_negative_engine)
            _write_json(engine.audit(args.input, metadata=_read_json(args.metadata)), args.output)
            return 0

        if args.command == "docx-finalize":
            engine = ManuscriptDocxEngine()
            report = engine.finalize(
                args.input,
                args.output,
                revisions=args.revisions,
                comments=args.comments,
                report_path=args.report,
            )
            if not args.report:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["validation"]["valid"] else 2

        if args.command == "docx-prepare":
            config = _read_json(args.tags)
            engine = ManuscriptDocxEngine(use_negative_engine=not args.no_negative_engine)
            report = engine.prepare(
                args.input,
                args.output,
                apply_safe_edits=not args.no_safe_edits,
                track_changes=not args.no_track_changes,
                add_comments=not args.no_comments,
                author=args.author,
                initials=args.initials,
                citation_tags=config.get("citations", []),
                reference_tags=config.get("references", []),
                bookmarks=config.get("bookmarks", []),
                ref_fields=config.get("ref_fields", []),
                report_path=args.report,
                metadata=_read_json(args.metadata),
            )
            if not args.report:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["validation"]["valid"] else 2
    except Exception as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
