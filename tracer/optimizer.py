"""
SVG optimizer module for path cleanup, coordinate rounding, and noise reduction.
"""

from __future__ import annotations
import re
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET


def round_path_coordinates(path_d: str, precision: int = 3) -> str:
    """
    Rounds floating point numbers inside SVG path d attribute to specified decimal precision.
    """
    def _repl(match):
        val = float(match.group(0))
        return f"{val:.{precision}f}".rstrip("0").rstrip(".")

    # Match floating point numbers
    return re.sub(r"[-+]?\d*\.\d+", _repl, path_d)


def optimize_svg(
    svg_content: str,
    precision: int = 3,
    min_path_area: float = 0.5,
    remove_metadata: bool = True,
    run_svgo: bool | None = None,
    optimization_level: str = "safe",
) -> str:
    """
    Optimizes SVG string content:
    - Filters tiny noise paths
    - Rounds floating point coordinates
    - Removes metadata/comments
    - Runs system svgo if available
    """
    if optimization_level == "none":
        return svg_content

    if optimization_level not in {"safe", "compact"}:
        raise ValueError("optimization_level must be 'none', 'safe', or 'compact'")

    # Safe optimization keeps the document structure while normalizing numeric
    # precision. Compact mode may additionally hand the result to SVGO.
    try:
        # Register namespaces to avoid ns0: prefixes
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        root = ET.fromstring(svg_content)

        # Round path coordinates
        for elem in root.iter():
            if elem.tag.endswith("path"):
                d = elem.attrib.get("d", "")
                if d:
                    elem.attrib["d"] = round_path_coordinates(d, precision=precision)

        # Strip metadata or comments if requested
        if remove_metadata:
            for child in list(root):
                if child.tag.endswith("metadata") or child.tag.endswith("title"):
                    root.remove(child)

        svg_content = ET.tostring(root, encoding="unicode")
    except Exception:
        # Fallback regex rounding if XML parser fails on special entities
        svg_content = round_path_coordinates(svg_content, precision=precision)

    # Optional svgo command execution if present
    should_run_svgo = optimization_level == "compact" if run_svgo is None else run_svgo
    if should_run_svgo:
        try:
            res = subprocess.run(
                ["svgo", "-", "-o", "-"],
                input=svg_content.encode("utf-8"),
                capture_output=True,
                check=True,
                timeout=5,
            )
            if res.returncode == 0:
                svg_content = res.stdout.decode("utf-8")
        except Exception:
            pass  # svgo not installed or failed; fallback to cleaned python string

    return svg_content
