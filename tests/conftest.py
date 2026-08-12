"""Skip an engine's tests when its optional extra is not installed.

A prose-only checkout should still produce a green suite: the tracer and PDF engines carry
native dependencies that are deliberately not installed by default, and a suite that fails
for a missing optional package trains people to ignore it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from writeroute.optional import available  # noqa: E402

# Named engine_* rather than after the packages themselves: pytest puts the first
# directory without an __init__.py on sys.path, so a tests/pdfstudio package shadowed the
# real pdfstudio and every import inside it resolved to the test directory.
REQUIRES = {
    "tests/engine_tracer": ("vtracer", "tracer"),
    "tests/engine_pdf": ("fitz", "pdf"),
}

# The inherited PDF suite asserts against private documents that are not distributed
# with this repository. It is kept in the source project; test_synthetic_forms.py
# covers the same behaviours against forms generated at test time.
NEEDS_PRIVATE_CORPUS = {"tests/engine_pdf/test_engine.py"}


def pytest_ignore_collect(collection_path, config):
    """Skip at collection, not at run.

    These modules import vtracer and PyMuPDF at module scope, so a missing extra is a
    collection error before any marker can apply. Ignoring the directory is the only point
    early enough to matter.
    """
    path = Path(str(collection_path)).as_posix()
    for directory, (module, _extra) in REQUIRES.items():
        if directory in path and not available(module):
            return True
    return any(path.endswith(name) for name in NEEDS_PRIVATE_CORPUS)


def pytest_report_collectionfinish(config):
    notes = [f"skipping {d}: needs the {extra} extra"
             for d, (module, extra) in REQUIRES.items() if not available(module)]
    notes += [f"skipping {name}: asserts against private documents that are not in this repo"
              for name in sorted(NEEDS_PRIVATE_CORPUS)]
    return notes
