#!/usr/bin/env python3
"""Build the static site into docs/ for GitHub Pages.

The engine is not rewritten in JavaScript. It is shipped as a zip of the very Python
package the test suite covers, unpacked into Pyodide in the browser. A reimplementation
would mean the preservation gate — the one layer the PhD-corpus benchmark cleared
without a single failure — became new, unvalidated code. Keeping the Python keeps the
evidence attached to it.

Everything under docs/ is generated. Edit the sources in static/ and re-run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ENGINE_ZIP = "writeroute-engine.zip"

# Only what the browser needs. `app.py`, the FastAPI service and the test suite stay
# out of the payload the visitor downloads.
PACKAGES = (
    "writeroute",
    "aiwd",
    "stats_brain",
    "scientific_pattern_engine",
    "lucid_sci",
    "auctor_engine",
    "pdfstudio",
)
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def engine_archive(destination: Path) -> dict[str, object]:
    files: list[str] = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
        for package in PACKAGES:
            base = ROOT / package
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in SKIP_DIRS for part in path.parts):
                    continue
                if path.suffix not in {".py", ".json", ".yaml", ".yml", ".txt"}:
                    continue
                arc = path.relative_to(ROOT).as_posix()
                zf.write(path, arc)
                files.append(arc)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {"files": len(files), "bytes": destination.stat().st_size, "sha256": digest,
            "members": files}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clean", action="store_true", help="remove docs/ before building")
    args = ap.parse_args()

    if args.clean and DOCS.exists():
        shutil.rmtree(DOCS)
    DOCS.mkdir(parents=True, exist_ok=True)

    manifest = engine_archive(DOCS / ENGINE_ZIP)

    for path in (ROOT / "static").rglob("*"):
        if path.is_file() and not any(p in SKIP_DIRS for p in path.parts):
            rel = path.relative_to(ROOT / "static")
            dest = DOCS / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

    assets_dir = DOCS / "assets"
    assets_dir.mkdir(exist_ok=True)
    if (ROOT / "assets").exists():
        for path in (ROOT / "assets").rglob("*"):
            if path.is_file() and not any(p in SKIP_DIRS for p in path.parts):
                rel = path.relative_to(ROOT / "assets")
                dest = assets_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)

    # Pages would otherwise hand the tree to Jekyll, which drops files beginning with
    # an underscore and rewrites nothing we want rewritten.
    (DOCS / ".nojekyll").write_text("")

    (DOCS / "engine-manifest.json").write_text(json.dumps({
        "archive": ENGINE_ZIP,
        "packages": list(PACKAGES),
        **manifest,
    }, indent=2) + "\n")


    print(f"engine archive: {manifest['files']} files, {manifest['bytes']:,} bytes")
    print(f"sha256: {manifest['sha256']}")
    print(f"docs/ -> {DOCS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
