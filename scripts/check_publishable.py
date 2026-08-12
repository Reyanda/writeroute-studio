#!/usr/bin/env python3
"""Refuse to publish anything that looks like a real document.

Two of the engines in this repository process identity and financial documents. Real
ones live in the source projects and must never reach a public remote, where history is
effectively permanent.

`.gitignore` is the first line of defence and it is not enough on its own, because
`git add -f` overrides it and a rule can be edited away without anyone noticing. This
check reads what git actually has staged or committed, which is the thing that gets
pushed, and fails the build on anything that should not be there.

Run directly, or through scripts/validate.sh.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Formats that carry documents. A repository of source code has no reason to track one
# outside a declared fixture directory.
DOCUMENT_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".rtf", ".eml", ".msg",
                     ".odt", ".pages", ".key", ".numbers"}

# Directories the engines write into, or that hold working material.
FORBIDDEN_DIRS = ("uploads/", "outputs/", "backups/", "scratch/", "qc/",
                  "stirling-pdf-repo/", "stirling_native/", "node_modules/", ".venv/")

# Names seen in the source projects, plus the general shapes of identity and banking
# paperwork. Matched case-insensitively against the whole path.
SENSITIVE_NAME = re.compile(
    r"e-?visa|passport|\bkyc\b|account[ _-]?opening|debit[ _-]?card|bank ?net|"
    r"e-?statement|payslip|pay[ _-]?stub|tax[ _-]?return|national[ _-]?id|"
    r"identity[ _-]?document|birth[ _-]?certificate|\bcv\b|resume|"
    r"application[ _-]?form|\bfilled_|\bapplicant\b",
    re.IGNORECASE,
)

# A fixture directory is allowed to hold small synthetic documents.
FIXTURE = re.compile(r"(^|/)tests/fixtures/")
MAX_FIXTURE_BYTES = 256 * 1024


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files"],
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def staged_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only"],
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def problems_for(root: Path, paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        lower = path.lower()
        suffix = Path(path).suffix.lower()
        in_fixtures = bool(FIXTURE.search(path))

        for directory in FORBIDDEN_DIRS:
            if lower.startswith(directory) or f"/{directory}" in lower:
                found.append(f"{path}: lives in {directory}, which is never published")
                break

        if suffix in DOCUMENT_SUFFIXES:
            if not in_fixtures:
                found.append(f"{path}: {suffix} documents are only allowed under tests/fixtures/")
            else:
                full = root / path
                if full.exists() and full.stat().st_size > MAX_FIXTURE_BYTES:
                    found.append(f"{path}: fixture is {full.stat().st_size // 1024} KB; "
                                 f"a synthetic fixture should be under {MAX_FIXTURE_BYTES // 1024} KB")

        if SENSITIVE_NAME.search(path) and not path.startswith("scripts/check_publishable"):
            found.append(f"{path}: the name matches identity or financial paperwork")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--staged-only", action="store_true",
                    help="check only what is staged, for use as a pre-commit hook")
    args = ap.parse_args()
    root = Path(args.root)

    paths = staged_files(root) if args.staged_only else tracked_files(root)
    found = problems_for(root, paths)

    if found:
        print("Refusing to publish. These files must not be in a public repository:",
              file=sys.stderr)
        for problem in found:
            print(f"  {problem}", file=sys.stderr)
        print("\nRemove them with `git rm --cached <path>` and confirm .gitignore covers "
              "them.", file=sys.stderr)
        return 1

    print(f"publishable: {len(paths)} tracked files, no documents or working directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
