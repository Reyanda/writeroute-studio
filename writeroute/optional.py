"""Optional-dependency handling shared by the three engines.

The prose engine is pure Python and runs in a browser. The other two are not: Tracer needs
vtracer, cairosvg, resvg and rembg's ONNX models, and PDF Studio needs PyMuPDF and OpenCV.
Making those hard requirements would drag hundreds of megabytes of native toolchain into an
install of a text-editing tool, and would break the browser build outright.

So they are extras, and the cost of an extra is that `import tracer` fails with
`ModuleNotFoundError: No module named 'vtracer'` — a message that names a package the
reader never asked for and does not tell them what to do. This module converts that into
the install command.

    from writeroute.optional import require

    vtracer = require("vtracer", extra="tracer")
"""
from __future__ import annotations

import importlib
from types import ModuleType

# Which extra provides which import name, so the message can name the right one.
EXTRAS: dict[str, str] = {
    "vtracer": "tracer",
    "cairosvg": "tracer",
    "resvg_py": "tracer",
    "rembg": "tracer",
    "skimage": "tracer",
    "PIL": "tracer",
    "fitz": "pdf",
    "cv2": "pdf",
    "pikepdf": "pdf",
    "numpy": "tracer",
}

# What the package is actually called on PyPI, where that differs from the import name.
DISTRIBUTION: dict[str, str] = {
    "skimage": "scikit-image",
    "PIL": "pillow",
    "fitz": "pymupdf",
    "cv2": "opencv-python-headless",
    "resvg_py": "resvg-py",
}


class MissingDependency(ImportError):
    """An optional engine was used without its extra installed."""


def require(name: str, *, extra: str | None = None, purpose: str = "") -> ModuleType:
    """Import `name`, or raise an error that says how to install it."""
    try:
        return importlib.import_module(name)
    except Exception as exc:
        group = extra or EXTRAS.get(name)
        package = DISTRIBUTION.get(name, name)
        lines = [f"{package} is required{f' to {purpose}' if purpose else ''} and is not installed."]
        if group:
            lines.append(f"Install it with:  pip install 'writeroute[{group}]'")
        else:
            lines.append(f"Install it with:  pip install {package}")
        raise MissingDependency("\n".join(lines)) from exc


def available(name: str) -> bool:
    """Whether an optional import is usable. A probe must never raise.

    ImportError is not the only failure: cairosvg imports cleanly and then raises OSError
    from cairocffi when the native libcairo is absent, which is the normal state on a Mac
    without Homebrew cairo. Anything that goes wrong here means the dependency is not
    usable, which is all the caller needs to know.
    """
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def engine_status() -> dict[str, dict[str, object]]:
    """What each engine can do in this environment, for `writeroute engines`."""
    return {
        "prose": {
            "available": True,
            "extra": None,
            "needs": [],
            "does": "audit, suggest, repair and verify prose",
        },
        "tracer": {
            "available": available("vtracer"),
            "extra": "tracer",
            "needs": [n for n in ("vtracer", "cairosvg", "PIL", "numpy") if not available(n)],
            "does": "convert raster images to SVG, with parity checks and background removal",
        },
        "pdf": {
            "available": available("fitz"),
            "extra": "pdf",
            "needs": [n for n in ("fitz", "cv2") if not available(n)],
            "does": "detect form fields in PDFs, fill them, and export annotations",
        },
    }
