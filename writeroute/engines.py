"""Command handlers for the optional engines.

Kept out of `__main__` so that a prose-only install never imports a native dependency it
does not have. Everything heavy is imported inside a function body, not at module scope.

The three engines share the output conventions the prose commands already use: `--json`
prints a machine-readable payload and nothing else, a human run prints a short report, and
a missing output path is derived from the input rather than demanded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .optional import engine_status


def _emit(payload: dict[str, Any], as_json: bool, human: str) -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(human)


def cmd_engines(_args) -> int:
    """What is installed here, and what the rest would need."""
    status = engine_status()
    width = max(len(name) for name in status)
    for name, info in status.items():
        state = "ready" if info["available"] else "not installed"
        print(f"{name:<{width}}  {state:<14}  {info['does']}")
        if not info["available"]:
            needs = ", ".join(info["needs"]) or "its dependencies"
            print(f"{'':<{width}}  missing {needs}")
            print(f"{'':<{width}}  pip install 'writeroute[{info['extra']}]'")
    return 0


def cmd_trace(args) -> int:
    """Raster to SVG."""
    import tracer

    if args.list_presets:
        for name in sorted(tracer.PRESETS):
            print(name)
        return 0

    source = Path(args.image)
    if not source.is_file():
        print(f"no such image: {source}", file=sys.stderr)
        return 1
    destination = Path(args.output) if args.output else source.with_suffix(".svg")

    config = tracer.TracingConfig()
    if args.preset:
        if args.preset not in tracer.PRESETS:
            print(f"unknown preset {args.preset!r}; run --list-presets", file=sys.stderr)
            return 1
        config = tracer.PRESETS[args.preset]

    image = str(source)
    if args.remove_background:
        image = tracer.remove_background(image)

    if args.mode == "parity":
        result = tracer.convert_with_parity(image, config=config)
        svg, report = result.svg, {
            "mode": "parity",
            "similarity": getattr(result, "similarity", None),
            "residualUsed": getattr(result, "residual_used", None),
        }
    else:
        svg = tracer.convert(image, config=config)
        report = {"mode": args.mode, "similarity": None, "residualUsed": False}

    destination.write_text(svg) if isinstance(svg, str) else destination.write_bytes(svg)
    size = destination.stat().st_size
    report.update({"source": str(source), "output": str(destination), "bytes": size})
    _emit(report, args.json,
          f"{source.name} -> {destination.name}  ({size:,} bytes, {report['mode']} mode)")
    return 0


def cmd_pdf(args) -> int:
    """Form-field detection, filling and raw unbundling."""
    import pdfstudio

    source = Path(args.file)
    if not source.is_file():
        print(f"no such PDF: {source}", file=sys.stderr)
        return 1

    if args.pdf_command == "unbundle":
        unbundler = pdfstudio.TracerUnbundler(str(source))
        try:
            payload = unbundler.unbundle_document()
        finally:
            unbundler.close()
        destination = Path(args.output) if args.output else source.with_suffix(".unbundled.json")
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"{source.name} -> {destination.name}")
        return 0

    if args.pdf_command == "detect":
        # Detection is per page and needs the open document for geometry, so the
        # unbundler is held for the whole pass rather than reopened per page.
        unbundler = pdfstudio.TracerUnbundler(str(source))
        detector = pdfstudio.TracerSlotDetector()
        try:
            document = unbundler.unbundle_document()
            pages = document.get("pages", [])
            fields: list[dict[str, Any]] = []
            for index, page in enumerate(pages):
                fields.extend(detector.detect_slots_for_page(page, unbundler.doc, index))
        finally:
            unbundler.close()
        payload = {"source": source.name, "pages": len(pages), "fields": fields}
        if args.output:
            Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        plural = "" if len(fields) == 1 else "s"
        _emit(payload, args.json,
              f"{source.name}: {len(fields)} field{plural} across {len(pages)} page"
              f"{'' if len(pages) == 1 else 's'}")
        return 0

    if args.pdf_command == "fill":
        # The value file is the JSON that `pdf detect --output` wrote, with a `value` added
        # to each field. Keyed by 1-based page number, which is what the rebundler expects.
        payload = json.loads(Path(args.values).read_text())
        fields = payload.get("fields", payload) if isinstance(payload, dict) else payload
        by_page: dict[int, list[dict[str, Any]]] = {}
        for field in fields:
            if not field.get("value"):
                continue
            by_page.setdefault(int(field.get("page", 0)) + 1, []).append(field)
        if not by_page:
            print("no field in the value file has a `value` set", file=sys.stderr)
            return 1
        out = Path(args.output)
        rebundler = pdfstudio.TracerRebundler(str(source))
        rebundler.fill_and_rebundle(by_page, str(out))
        requested = sum(len(v) for v in by_page.values())
        missed = getattr(rebundler, "unrendered", [])
        drawn = requested - len(missed)
        print(f"{source.name} -> {out.name}  ({drawn} of {requested} field"
              f"{'' if requested == 1 else 's'} filled, {out.stat().st_size:,} bytes)")
        for row in missed[:5]:
            print(f"  not drawn: {row['id']} ({row['slot_type']}) = {row['value']!r}",
                  file=sys.stderr)
        # A partial fill is not a success: the file looks complete and is not.
        return 0 if not missed else 4

    print(f"unknown pdf command: {args.pdf_command}", file=sys.stderr)
    return 1


def run_engine_command(args) -> int:
    return {"engines": cmd_engines, "trace": cmd_trace, "pdf": cmd_pdf}[args.command](args)
