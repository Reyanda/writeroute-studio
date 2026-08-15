from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .auctor_bridge import AuctorBridge
from .document import load_review_input
from .knowledge import export_registry, load_yaml, registry_counts
from .reporting import write_report
from .reviewer import StatsBrainReviewer


def _load_mapping(path: str | Path) -> dict:
    source = Path(path)
    if source.suffix.lower() == ".json":
        value = json.loads(source.read_text(encoding="utf-8"))
    else:
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Input must contain a mapping")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stats-brain", description="Estimand-first statistical and epidemiological reviewer")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="Review JSON, YAML, text, Markdown, or DOCX input")
    review.add_argument("input")
    review.add_argument("--manifest", help="Optional JSON or YAML manifest merged with a text or DOCX input")
    review.add_argument("--mode", choices=["screen", "full", "forensic", "protocol", "replication"], default="full")
    review.add_argument("--non-exhaustive", action="store_true")
    review.add_argument("--output", required=True)
    review.add_argument("--format", choices=["json", "yaml", "md"])
    review.add_argument("--auctor-packet", help="Optional path for an Auctor review packet")

    inspect = sub.add_parser("inspect", help="Print registry counts")
    inspect.add_argument("--json", action="store_true")

    registry = sub.add_parser("registry", help="Export a knowledge registry")
    registry.add_argument("name", choices=[
        "statistical_ontology", "method_registry", "estimand_registry", "design_profiles",
        "debate_registry", "source_registry", "rule_catalog",
    ])
    registry.add_argument("--output", required=True)

    debate = sub.add_parser("debate", help="Show a debate registry entry")
    debate.add_argument("debate_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            counts = registry_counts()
            if args.json:
                print(json.dumps(counts, indent=2))
            else:
                for key, value in counts.items():
                    print(f"{key}: {value}")
            return 0
        if args.command == "registry":
            export_registry(args.name, args.output)
            return 0
        if args.command == "debate":
            debates = load_yaml("debate_registry").get("debates", {})
            if args.debate_id not in debates:
                raise KeyError(f"Unknown debate: {args.debate_id}")
            print(yaml.safe_dump({args.debate_id: debates[args.debate_id]}, sort_keys=False, allow_unicode=True))
            return 0
        if args.command == "review":
            value = load_review_input(args.input)
            if args.manifest:
                supplied = _load_mapping(args.manifest)
                existing = value.get("manifest", {})
                value["manifest"] = {**existing, **supplied}
            value["mode"] = args.mode
            value["exhaustive"] = not args.non_exhaustive
            report = StatsBrainReviewer().review(value)
            write_report(report, args.output, args.format)
            if args.auctor_packet:
                packet = AuctorBridge.packet(report)
                Path(args.auctor_packet).write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
            return 0
    except Exception as exc:
        print(f"stats-brain: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
