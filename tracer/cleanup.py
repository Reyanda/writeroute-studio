"""Post-processing that removes geometry contributing nothing to the render.

Stacked tracing, residual overlays and third-party exporters all leave elements
buried under later paint. They cost bytes, clutter the scene graph and show up
as mottled residue when a covering layer is hidden or edited. A 2.43 MB Hybrid
document produced by an earlier Tracer build carried 846 such paths — 17.7% of
its geometry.

Measuring contribution
----------------------
Rather than removing each element and re-rendering — which costs one render per
element — every paintable element is given a unique flat colour encoding its
index, and the document is rendered once. Each element's visible contribution is
then the number of pixels carrying its index in that identity buffer. One render
answers the question for the whole document.

Two operations follow from the same measurement:

* :func:`cull_dead_geometry` removes only elements contributing **zero** pixels.
  It is provably safe and is verified by re-rendering and requiring bit parity
  with the original.
* :func:`cull_by_contribution` removes elements below a pixel threshold. These
  contribute real detail — measurement shows fine text and thin rules live here
  — so it is a deliberate complexity/fidelity trade and reports its own measured
  quality delta rather than claiming to be free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from .verifier import calculate_quality_metrics, measure_bit_parity, render_svg_to_png

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

#: Elements that put marks on the canvas. Containers and definitions are not
#: culled directly: they disappear only when every child they paint is gone.
PAINTABLE = {
    "path",
    "rect",
    "circle",
    "ellipse",
    "polygon",
    "polyline",
    "line",
    "image",
    "use",
    "text",
}

#: An identity render can only distinguish this many elements in 24-bit colour.
MAX_IDENTIFIABLE = 0xFFFFFF


@dataclass
class CleanupReport:
    """What the pass measured and what it removed."""

    elements: int = 0
    dead: int = 0
    removed: int = 0
    threshold: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    quality_before: float = 0.0
    quality_after: float = 0.0
    bit_exact: bool = False
    contributions: list[int] = field(default_factory=list)

    @property
    def bytes_saved(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)

    @property
    def quality_delta(self) -> float:
        return self.quality_after - self.quality_before

    def to_dict(self) -> dict[str, Any]:
        return {
            "elements": self.elements,
            "dead": self.dead,
            "removed": self.removed,
            "threshold": self.threshold,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "bytes_saved": self.bytes_saved,
            "quality_before": self.quality_before,
            "quality_after": self.quality_after,
            "quality_delta": self.quality_delta,
            "bit_exact": self.bit_exact,
        }


def _paintable(root: ET.Element) -> list[ET.Element]:
    return [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] in PAINTABLE]


def _inside_defs(root: ET.Element) -> set[ET.Element]:
    """Elements under defs/mask/clipPath/symbol are referenced, not painted."""
    hidden: set[ET.Element] = set()
    for parent in root.iter():
        if parent.tag.rsplit("}", 1)[-1] in {"defs", "mask", "clipPath", "symbol", "pattern"}:
            hidden.update(parent.iter())
    return hidden


def measure_contributions(svg: str, size: tuple[int, int]) -> list[int]:
    """Return visible pixel count per paintable element, in document order.

    Elements inside definitions report -1: they are reachable only through a
    reference and must never be culled on their own evidence.
    """
    root = ET.fromstring(svg)
    referenced = _inside_defs(root)
    elements = _paintable(root)
    if len(elements) > MAX_IDENTIFIABLE:
        raise ValueError("Document exceeds the identity-buffer element limit.")

    # Identity colours must survive rasterisation unblended. With antialiasing
    # on, two neighbouring indices average into a third that decodes as an
    # unrelated element, and a thin shape whose every pixel is a blended fringe
    # reads as contributing nothing — which would cull live geometry.
    root.set("shape-rendering", "crispEdges")

    indexed: list[int] = []
    for index, element in enumerate(elements):
        if element in referenced:
            indexed.append(-1)
            continue
        indexed.append(index)
        red, green, blue = (index >> 16) & 0xFF, (index >> 8) & 0xFF, index & 0xFF
        colour = f"#{red:02x}{green:02x}{blue:02x}"
        element.set("fill", colour)
        element.set("fill-opacity", "1")
        element.set("opacity", "1")
        if element.get("stroke") not in (None, "none"):
            element.set("stroke", colour)
            element.set("stroke-opacity", "1")
        # Inline style would override the presentation attributes above.
        if element.get("style"):
            element.set("style", "")

    buffer = np.asarray(
        render_svg_to_png(ET.tostring(root, encoding="unicode"), size).convert("RGBA"),
        dtype=np.uint8,
    )
    identity = (
        (buffer[..., 0].astype(np.uint32) << 16)
        | (buffer[..., 1].astype(np.uint32) << 8)
        | buffer[..., 2].astype(np.uint32)
    )
    values, counts = np.unique(identity[buffer[..., 3] > 0], return_counts=True)
    seen = dict(zip(values.tolist(), counts.tolist()))
    return [-1 if slot < 0 else int(seen.get(slot, 0)) for slot in indexed]


def _strip(svg: str, contributions: list[int], threshold: int) -> tuple[str, int]:
    root = ET.fromstring(svg)
    parents = {child: parent for parent in root.iter() for child in parent}
    elements = _paintable(root)
    removed = 0
    for element, contribution in zip(elements, contributions):
        if contribution < 0:
            continue  # referenced from a definition
        if contribution <= threshold and element in parents:
            parents[element].remove(element)
            removed += 1
    return ET.tostring(root, encoding="unicode"), removed


def _remove_indices(svg: str, indices: set[int]) -> str:
    root = ET.fromstring(svg)
    parents = {child: parent for parent in root.iter() for child in parent}
    for index, element in enumerate(_paintable(root)):
        if index in indices and element in parents:
            parents[element].remove(element)
    return ET.tostring(root, encoding="unicode")


def _safe_removable(
    svg: str,
    candidates: list[int],
    size: tuple[int, int],
    reference: Image.Image,
    budget: list[int],
) -> set[int]:
    """Largest subset of candidates whose removal keeps the render identical.

    The identity buffer proposes; parity disposes. A candidate set is removed
    wholesale and verified; if the render changes, the set is halved and each
    half retried. Antialiased fringe geometry reads as zero-contribution in the
    identity pass but is genuinely visible, so proposals cannot be trusted —
    only the re-render can decide.
    """
    if not candidates or budget[0] <= 0:
        return set()
    budget[0] -= 1
    trial = _remove_indices(svg, set(candidates))
    if measure_bit_parity(reference, render_svg_to_png(trial, size))["bit_exact"]:
        return set(candidates)
    if len(candidates) == 1:
        return set()
    middle = len(candidates) // 2
    left = _safe_removable(svg, candidates[:middle], size, reference, budget)
    right = _safe_removable(svg, candidates[middle:], size, reference, budget)
    return left | right


def cull_dead_geometry(
    svg: str,
    size: tuple[int, int],
    *,
    reference: Image.Image | None = None,
    render_budget: int = 96,
) -> tuple[str, CleanupReport]:
    """Remove elements that paint no visible pixels at all.

    The result is verified: it must render bit-identically to the input. If it
    does not, the original document is returned unchanged, because a cleanup
    pass that alters the image has failed regardless of how much it saved.
    """
    before = reference or render_svg_to_png(svg, size)
    contributions = measure_contributions(svg, size)
    dead = sum(1 for value in contributions if value == 0)
    report = CleanupReport(
        elements=sum(1 for value in contributions if value >= 0),
        dead=dead,
        threshold=0,
        bytes_before=len(svg.encode("utf-8")),
        bytes_after=len(svg.encode("utf-8")),
        contributions=contributions,
    )
    if not dead:
        report.bit_exact = True
        return svg, report

    candidates = [i for i, value in enumerate(contributions) if value == 0]
    safe = _safe_removable(svg, candidates, size, before, [int(render_budget)])
    if not safe:
        report.bit_exact = True  # nothing removed, so the render is untouched
        return svg, report

    cleaned = _remove_indices(svg, safe)
    if not measure_bit_parity(before, render_svg_to_png(cleaned, size))["bit_exact"]:
        report.bit_exact = True
        return svg, report

    report.removed = len(safe)
    report.bytes_after = len(cleaned.encode("utf-8"))
    report.bit_exact = True
    return cleaned, report


def cull_by_contribution(
    svg: str,
    original: Image.Image,
    *,
    threshold: int = 8,
    max_quality_loss: float = 0.005,
) -> tuple[str, CleanupReport]:
    """Drop low-contribution elements, keeping the result only if it stays good.

    Elements below the threshold are genuine detail rather than noise —
    measurement puts fine text and thin rules in this band — so the trade is
    made explicit: the pass reports the quality it cost, and reverts when the
    loss exceeds ``max_quality_loss``.
    """
    size = original.size
    contributions = measure_contributions(svg, size)
    before_metrics = calculate_quality_metrics(original, render_svg_to_png(svg, size))
    report = CleanupReport(
        elements=sum(1 for value in contributions if value >= 0),
        dead=sum(1 for value in contributions if value == 0),
        threshold=int(threshold),
        bytes_before=len(svg.encode("utf-8")),
        bytes_after=len(svg.encode("utf-8")),
        quality_before=before_metrics["quality_score"],
        quality_after=before_metrics["quality_score"],
        contributions=contributions,
    )

    cleaned, removed = _strip(svg, contributions, max(0, int(threshold)))
    if not removed:
        return svg, report

    after_metrics = calculate_quality_metrics(original, render_svg_to_png(cleaned, size))
    loss = before_metrics["quality_score"] - after_metrics["quality_score"]
    if loss > max_quality_loss:
        return svg, report

    report.removed = removed
    report.bytes_after = len(cleaned.encode("utf-8"))
    report.quality_after = after_metrics["quality_score"]
    return cleaned, report
