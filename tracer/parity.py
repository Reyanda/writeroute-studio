"""Representation-aware SVG output modes for mixed raster documents."""

from __future__ import annotations

import base64
import gzip
import io
import re
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageFilter

from .config import OutputMode, ValidityResult
from .converter import TracerConverter
from .lvc import encode_pixels
from .verifier import (
    calculate_quality_metrics,
    create_difference_map,
    render_svg_to_png,
    validate_output,
)

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

#: Repair coverage above which the vector base stops obviously earning its
#: bytes and the codec-only alternative is measured against it.
_BASE_REVIEW_COVERAGE = 0.60

#: Hybrid repair coverage above which the vector base is effectively invisible.
#: Completing the residual to the whole canvas is lossless and lets the base be
#: dropped, which is smaller and exact rather than degenerate.
HYBRID_BASE_RETENTION_FLOOR = 0.99


def _deflated(markup: str) -> int:
    """Compressed cost of a fragment, which is what actually ships."""
    return len(zlib.compress(markup.encode("utf-8"), 6))


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _data_uri(payload: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _drop_orphaned_definitions(root: ET.Element) -> None:
    """Remove definitions whose only consumer has been removed.

    Superseding the vector base takes its gradients, masks and symbols out of
    use, but the definitions themselves survive in `defs` and ship as dead
    weight. QC flags them; this removes the cause.
    """
    markup = ET.tostring(root, encoding="unicode")
    referenced = set(re.findall(r"url\(#([^)\"']+)\)", markup)) | set(
        re.findall(r'href="#([^"]+)"', markup)
    )
    for container in [e for e in root.iter() if _local_name(e.tag) == "defs"]:
        for definition in list(container):
            identifier = definition.get("id")
            if identifier and identifier not in referenced:
                container.remove(definition)
        if len(container) == 0:
            for parent in root.iter():
                if container in list(parent):
                    parent.remove(container)
                    break


@dataclass
class ParityResult:
    svg: str
    rendered: Image.Image
    difference: Image.Image
    mode: OutputMode
    metrics: dict[str, float]
    validity: ValidityResult
    vector_coverage: float
    raster_coverage: float
    svgz: bytes
    metadata: dict[str, Any]

    def report(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "metrics": self.metrics,
            "validity": asdict(self.validity),
            "vector_coverage": self.vector_coverage,
            "raster_coverage": self.raster_coverage,
            "svg_bytes": len(self.svg.encode("utf-8")),
            "svgz_bytes": len(self.svgz),
            "metadata": self.metadata,
        }


def exact_wrapper_svg(original: Image.Image) -> str:
    """Return an explicit raster-contained SVG with exact source dimensions."""
    width, height = original.size
    href = _data_uri(_png_bytes(original))
    return (
        f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" data-tracer-mode="exact_wrapper">'
        f'<image width="{width}" height="{height}" href="{href}" '
        'preserveAspectRatio="none" data-tracer-role="exact-source"/>'
        "</svg>"
    )


def _expanded_error_mask(
    original: Image.Image,
    rendered: Image.Image,
    *,
    threshold: int = 4,
    expansion: int = 1,
) -> np.ndarray:
    source = np.asarray(original.convert("RGBA"), dtype=np.int16)
    proof = np.asarray(rendered.convert("RGBA"), dtype=np.int16)
    error = np.max(np.abs(source - proof), axis=2) > max(0, int(threshold))
    if expansion > 0 and np.any(error):
        mask_image = Image.fromarray((error.astype(np.uint8) * 255), mode="L")
        size = max(3, int(expansion) * 2 + 1)
        if size % 2 == 0:
            size += 1
        mask_image = mask_image.filter(ImageFilter.MaxFilter(size=size))
        error = np.asarray(mask_image, dtype=np.uint8) > 0
    return error


def _is_fully_opaque(image: Image.Image) -> bool:
    """Return whether every source pixel is fully opaque."""
    alpha_extrema = image.convert("RGBA").getchannel("A").getextrema()
    return alpha_extrema == (255, 255)


def hybrid_parity_svg(
    original: Image.Image,
    vector_svg: str,
    *,
    threshold: int = 8,
    expansion: int = 1,
    vector_render: Image.Image | None = None,
) -> tuple[str, float]:
    """Overlay a lossless residual plane only where the vector proof fails.

    Opaque documents keep a complete, unmasked vector base beneath the repair
    plane. Sources with partial transparency retain alpha-correct replacement
    cutouts because source-over compositing cannot reduce existing alpha.
    """
    source = original.convert("RGBA")
    width, height = source.size
    vector_render = vector_render or render_svg_to_png(vector_svg, source.size)
    patch_mask = _expanded_error_mask(
        source,
        vector_render,
        threshold=threshold,
        expansion=expansion,
    )
    raster_coverage = float(np.mean(patch_mask))
    if not np.any(patch_mask):
        return vector_svg, 0.0

    root = ET.fromstring(vector_svg)
    root.set("width", str(width))
    root.set("height", str(height))
    root.set("viewBox", f"0 0 {width} {height}")
    root.set("data-tracer-mode", "hybrid_parity")
    non_destructive = _is_fully_opaque(source)
    compositing = "non_destructive_overlay" if non_destructive else "alpha_cutout"
    root.set("data-tracer-compositing", compositing)

    preserved: list[ET.Element] = []
    renderable: list[ET.Element] = []
    for child in list(root):
        root.remove(child)
        if _local_name(child.tag) in {"defs", "style", "metadata", "title", "desc"}:
            preserved.append(child)
        else:
            renderable.append(child)

    for child in preserved:
        root.append(child)

    vector_attributes = {
        "id": "tracer-vector-base",
        "data-tracer-role": "vector-base",
        "data-tracer-integrity": "complete" if non_destructive else "alpha-replacement",
    }
    if not non_destructive:
        definitions = ET.Element(f"{{{SVG_NS}}}defs")
        cutout = ET.SubElement(
            definitions,
            f"{{{SVG_NS}}}mask",
            {
                "id": "tracer-residual-cutout",
                "x": "0",
                "y": "0",
                "width": str(width),
                "height": str(height),
                "maskUnits": "userSpaceOnUse",
                "style": "mask-type:alpha",
            },
        )
        mask_rgba = np.full((height, width, 4), 255, dtype=np.uint8)
        mask_rgba[patch_mask, 3] = 0
        ET.SubElement(
            cutout,
            f"{{{SVG_NS}}}image",
            {
                "x": "0",
                "y": "0",
                "width": str(width),
                "height": str(height),
                "href": _data_uri(_png_bytes(Image.fromarray(mask_rgba, mode="RGBA"))),
                "preserveAspectRatio": "none",
            },
        )
        root.append(definitions)
        vector_attributes["mask"] = "url(#tracer-residual-cutout)"

    # When repair covers essentially the whole canvas the vector base is hidden
    # and contributes almost nothing, while still costing a full trace's paths
    # and bytes. Completing the residual to the full canvas is lossless — it is
    # exact source pixels — and it makes the base removable, turning a
    # degenerate Hybrid into a smaller, exact document. This is reported, not
    # silent: the mode declares raster coverage 1.0 and a removed base.
    fully_occluded = non_destructive and bool(patch_mask.all())
    if non_destructive and not fully_occluded and raster_coverage >= HYBRID_BASE_RETENTION_FLOOR:
        patch_mask = np.ones_like(patch_mask)
        raster_coverage = 1.0
        fully_occluded = True
    if fully_occluded:
        compositing = "occluded_base_removed"
        root.set("data-tracer-compositing", compositing)
        root.set("data-tracer-base", "removed")
    else:
        vector_group = ET.SubElement(
            root,
            f"{{{SVG_NS}}}g",
            vector_attributes,
        )
        for child in renderable:
            vector_group.append(child)

    patch_array = np.asarray(source, dtype=np.uint8).copy()
    patch_array[~patch_mask, 3] = 0
    ET.SubElement(
        root,
        f"{{{SVG_NS}}}image",
        {
            "id": "tracer-residual-plane",
            "x": "0",
            "y": "0",
            "width": str(width),
            "height": str(height),
            "href": _data_uri(_png_bytes(Image.fromarray(patch_array, mode="RGBA"))),
            "preserveAspectRatio": "none",
            "data-tracer-role": "residual-patch",
            "data-tracer-coverage": f"{raster_coverage:.6f}",
            "data-tracer-compositing": compositing,
        },
    )
    return ET.tostring(root, encoding="unicode"), raster_coverage


def absolute_parity_svg(
    original: Image.Image,
    vector_svg: str,
    *,
    vector_render: Image.Image | None = None,
    mine_symbols: bool = True,
) -> tuple[str, float, dict[str, Any]]:
    """Repair a vector base to bit parity using exact vector geometry.

    The editable vector base is preserved unmasked. Every pixel the base does
    not already reproduce exactly is re-stated as integer-aligned vector
    rectangles above it. The result contains no raster image at all, so it is
    simultaneously pixel-identical and purely vector.

    The repair mask uses a zero tolerance by construction: any premultiplied
    difference is a defect, not a perceptual judgement.
    """
    source = original.convert("RGBA")
    width, height = source.size
    vector_render = vector_render or render_svg_to_png(vector_svg, source.size)

    source_pixels = np.asarray(source, dtype=np.int32)
    proof_pixels = np.asarray(vector_render.convert("RGBA"), dtype=np.int32)
    source_alpha = source_pixels[..., 3]
    proof_alpha = proof_pixels[..., 3]
    source_premultiplied = np.stack(
        [(source_pixels[..., i] * source_alpha + 127) // 255 for i in range(3)]
        + [source_alpha],
        axis=-1,
    )
    proof_premultiplied = np.stack(
        [(proof_pixels[..., i] * proof_alpha + 127) // 255 for i in range(3)]
        + [proof_alpha],
        axis=-1,
    )
    repair = np.any(source_premultiplied != proof_premultiplied, axis=2)

    # A repair pixel whose source alpha is not fully opaque cannot cover the
    # approximation beneath it, so the base must be removed there. Opaque repair
    # pixels overlay non-destructively and the editable base stays intact.
    translucent_repair = repair & (source_alpha < 255)
    needs_cutout = bool(np.any(translucent_repair))
    raster_coverage = 0.0
    repair_coverage = float(np.mean(repair))

    root = ET.fromstring(vector_svg)
    root.set("width", str(width))
    root.set("height", str(height))
    root.set("viewBox", f"0 0 {width} {height}")
    root.set("data-tracer-mode", "absolute_parity")
    root.set("data-tracer-codec", "lvc")
    root.set(
        "data-tracer-compositing",
        "exact_cutout" if needs_cutout else "non_destructive_overlay",
    )

    preserved: list[ET.Element] = []
    renderable: list[ET.Element] = []
    for child in list(root):
        root.remove(child)
        if _local_name(child.tag) in {"defs", "style", "metadata", "title", "desc"}:
            preserved.append(child)
        else:
            renderable.append(child)
    for child in preserved:
        root.append(child)

    vector_attributes = {
        "id": "tracer-vector-base",
        "data-tracer-role": "vector-base",
        "data-tracer-integrity": "partial" if needs_cutout else "complete",
    }
    if needs_cutout:
        # The cutout itself is vector geometry, so the document stays raster-free.
        definitions = ET.Element(f"{{{SVG_NS}}}defs")
        mask_element = ET.SubElement(
            definitions,
            f"{{{SVG_NS}}}mask",
            {
                "id": "tracer-exact-cutout",
                "maskUnits": "userSpaceOnUse",
                "x": "0",
                "y": "0",
                "width": str(width),
                "height": str(height),
            },
        )
        ET.SubElement(
            mask_element,
            f"{{{SVG_NS}}}rect",
            {"x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#fff"},
        )
        holes = np.zeros((height, width, 4), dtype=np.uint8)
        holes[translucent_repair] = (0, 0, 0, 255)
        hole_fragment, _ = encode_pixels(
            Image.fromarray(holes, mode="RGBA"),
            mine_symbols=False,
            role="exact-cutout",
        )
        mask_element.append(ET.fromstring(hole_fragment))
        root.append(definitions)
        vector_attributes["mask"] = "url(#tracer-exact-cutout)"

    vector_group = ET.SubElement(root, f"{{{SVG_NS}}}g", vector_attributes)
    for child in renderable:
        vector_group.append(child)

    codec_stats: dict[str, Any] = {}
    coverage = repair_coverage
    base_superseded = False
    if np.any(repair):
        fragment, stats = encode_pixels(
            source,
            repair,
            mine_symbols=mine_symbols,
            element_id="tracer-exact-residual",
            role="exact-residual",
        )
        codec_stats = stats.to_dict()

        # When the approximation reproduces very little, keeping it costs bytes
        # and render time while saving few repair pixels. Above the review
        # threshold the alternative is measured, not assumed: encode the whole
        # canvas exactly and keep whichever document is smaller compressed.
        if repair_coverage >= _BASE_REVIEW_COVERAGE and not needs_cutout:
            standalone, standalone_stats = encode_pixels(
                source,
                mine_symbols=mine_symbols,
                element_id="tracer-exact-residual",
                role="exact-residual",
            )
            base_markup = ET.tostring(vector_group, encoding="unicode")
            if _deflated(standalone) < _deflated(base_markup) + _deflated(fragment):
                base_superseded = True
                fragment = standalone
                codec_stats = standalone_stats.to_dict()
                coverage = 1.0
                root.remove(vector_group)
                root.set("data-tracer-base", "superseded")
                _drop_orphaned_definitions(root)

        element = ET.fromstring(fragment)
        element.set("data-tracer-coverage", f"{coverage:.6f}")
        root.append(element)

    compositing = "exact_cutout" if needs_cutout else "non_destructive_overlay"
    return (
        ET.tostring(root, encoding="unicode"),
        raster_coverage,
        {
            "repair_coverage": coverage,
            "exact_repair_pixels": width * height
            if base_superseded
            else int(np.count_nonzero(repair)),
            "compositing": "codec_only" if base_superseded else compositing,
            "base_superseded": base_superseded,
            "base_repair_coverage": repair_coverage,
            "codec": codec_stats,
        },
    )


def build_parity_result(
    original: Image.Image,
    vector_svg: str,
    *,
    mode: OutputMode | str = OutputMode.PURE_VECTOR,
    target_quality: float = 0.0,
    residual_threshold: int = 4,
    residual_expansion: int = 1,
    metadata: dict[str, Any] | None = None,
) -> ParityResult:
    """Create and verify one of Tracer's three explicit representations."""
    selected = mode if isinstance(mode, OutputMode) else OutputMode(mode)
    source = original.convert("RGBA")
    raster_coverage = 0.0
    rendered: Image.Image | None = None
    metrics: dict[str, float] | None = None
    result_metadata = dict(metadata or {})
    if selected is OutputMode.EXACT_WRAPPER:
        svg = exact_wrapper_svg(source)
        raster_coverage = 1.0
    elif selected is OutputMode.ABSOLUTE_PARITY:
        svg, raster_coverage, repair_report = absolute_parity_svg(source, vector_svg)
        result_metadata["exact_repair"] = repair_report
    elif selected is OutputMode.HYBRID_PARITY:
        requested_threshold = max(0, int(residual_threshold))
        thresholds = [requested_threshold]
        thresholds.extend(
            value
            for value in (8, 6, 4, 2, 1, 0)
            if value < requested_threshold and value not in thresholds
        )
        vector_render = render_svg_to_png(vector_svg, source.size)
        attempts: list[dict[str, float]] = []
        for threshold in thresholds:
            svg, raster_coverage = hybrid_parity_svg(
                source,
                vector_svg,
                threshold=threshold,
                expansion=residual_expansion,
                vector_render=vector_render,
            )
            rendered = render_svg_to_png(svg, source.size)
            metrics = calculate_quality_metrics(source, rendered)
            attempts.append(
                {
                    "threshold": float(threshold),
                    "quality_score": metrics["quality_score"],
                    "raster_coverage": raster_coverage,
                }
            )
            if target_quality <= 0 or metrics["quality_score"] >= target_quality:
                break
        base_removed = 'data-tracer-base="removed"' in svg
        if base_removed:
            compositing = "occluded_base_removed"
        elif _is_fully_opaque(source):
            compositing = "non_destructive_overlay"
        else:
            compositing = "alpha_cutout"
        result_metadata["hybrid_repair"] = {
            "requested_threshold": requested_threshold,
            "threshold_used": int(attempts[-1]["threshold"]),
            "expansion": int(residual_expansion),
            "compositing": compositing,
            "base_removed": base_removed,
            "attempts": attempts,
        }
    else:
        svg = vector_svg

    if rendered is None:
        rendered = render_svg_to_png(svg, source.size)
    if metrics is None:
        metrics = calculate_quality_metrics(source, rendered)
    absolute = selected is OutputMode.ABSOLUTE_PARITY
    # Complexity budgets are per-megapixel with a floor, not absolute constants.
    # Fixed ceilings calibrated on ~6 Mpx screenshots reject a larger source for
    # being large: a 13.5 Mpx portrait at quality 0.999 failed an 80,000-path
    # limit that has nothing to do with whether the output is correct.
    megapixels = max(1.0, (source.width * source.height) / 1_000_000)
    if absolute:
        # The exact codec emits one compound path per distinct colour, so its
        # path budget tracks palette cardinality rather than shape count.
        max_paths = max(400_000, int(40_000 * megapixels))
        max_svg_bytes = max(192_000_000, int(16_000_000 * megapixels))
    elif selected is OutputMode.PURE_VECTOR:
        max_paths = max(80_000, int(20_000 * megapixels))
        max_svg_bytes = max(16_000_000, int(2_000_000 * megapixels))
    else:
        max_paths = max(80_000, int(20_000 * megapixels))
        max_svg_bytes = max(32_000_000, int(4_000_000 * megapixels))
    validity = validate_output(
        svg,
        source,
        rendered,
        target_quality=target_quality,
        max_paths=max_paths,
        max_svg_bytes=max_svg_bytes,
        max_images=0 if absolute else None,
        validity_profile="absolute_parity" if absolute else "general",
        require_bit_parity=absolute,
    )
    difference = create_difference_map(source, rendered)
    return ParityResult(
        svg=svg,
        rendered=rendered,
        difference=difference,
        mode=selected,
        metrics=metrics,
        validity=validity,
        vector_coverage=max(0.0, 1.0 - raster_coverage),
        raster_coverage=raster_coverage,
        svgz=gzip.compress(svg.encode("utf-8"), compresslevel=9),
        metadata=result_metadata,
    )


def convert_with_parity(
    input_path: str | Path,
    *,
    output_mode: OutputMode | str = OutputMode.PURE_VECTOR,
    preset: str | None = None,
    auto_preset: bool = True,
    verify: bool = False,
    target_quality: float = 0.0,
    quality_profile: str = "balanced",
    max_dim: int = 4096,
    residual_threshold: int = 4,
    residual_expansion: int = 1,
    **overrides: Any,
) -> ParityResult:
    """Run the existing tracer and package it through a representation contract."""
    path = Path(input_path)
    selected = output_mode if isinstance(output_mode, OutputMode) else OutputMode(output_mode)
    with Image.open(path) as image:
        original = image.convert("RGBA")
    if selected is OutputMode.EXACT_WRAPPER:
        width, height = original.size
        placeholder = (
            f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}"/>'
        )
        return build_parity_result(
            original,
            placeholder,
            mode=selected,
            target_quality=target_quality,
            metadata={"preset_used": None, "representation": "raster-contained SVG"},
        )
    converter = TracerConverter()
    vector_svg, verification, metadata = converter.convert_image(
        path,
        preset=preset,
        auto_preset=auto_preset,
        verify=verify,
        target_ssim=max(0.0, target_quality),
        quality_profile=quality_profile,
        max_dim=max_dim,
        **overrides,
    )
    if verification is not None:
        metadata["verification"] = {
            "passed": verification.passed,
            "quality_score": verification.quality_score,
            "ssim": verification.ssim,
            "edge_similarity": verification.edge_similarity,
        }
    return build_parity_result(
        original,
        vector_svg,
        mode=selected,
        target_quality=target_quality,
        residual_threshold=residual_threshold,
        residual_expansion=residual_expansion,
        metadata=metadata,
    )
