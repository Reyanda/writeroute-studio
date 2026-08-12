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
PACKAGES = ("writeroute", "aiwd")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def engine_archive(destination: Path) -> dict[str, object]:
    files: list[str] = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
        for package in PACKAGES:
            base = ROOT / package
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in SKIP_DIRS for part in path.parts):
                    continue
                if path.suffix not in {".py", ".json"}:
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

    for name in ("index.html", "studio.html", "app.js", "engine.js", "files.js",
                 "styles.css", "landing.css"):
        source = ROOT / "static" / name
        if source.exists():
            shutil.copy2(source, DOCS / name)

    assets = DOCS / "assets"
    assets.mkdir(exist_ok=True)
    for name in ("logo.png", "logo-mark.png", "logo-wordmark.png", "hero.png",
                 "hero-dark.png", "workspace.png", "workspace-dark.png", "favicon.png"):
        source = ROOT / "assets" / name
        if source.exists():
            shutil.copy2(source, assets / name)

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
