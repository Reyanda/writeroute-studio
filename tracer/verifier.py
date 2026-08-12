"""Rendering, visual verification, and quality-aware auto-tuning."""

from __future__ import annotations

import hashlib
import io
import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import numpy as np
from PIL import Image

try:
    from skimage.metrics import structural_similarity as _structural_similarity
except ImportError:
    _structural_similarity = None

from .config import TracingConfig, ValidityResult, VerificationResult


def _load_svg_source(svg_source: str | Path) -> str:
    """Return SVG markup from either a path or an in-memory string."""
    is_path = isinstance(svg_source, Path)
    if isinstance(svg_source, str) and not svg_source.lstrip().startswith("<") and len(svg_source) < 4096:
        try:
            is_path = Path(svg_source).exists()
        except OSError:
            is_path = False
    if is_path:
        return Path(svg_source).read_text(encoding="utf-8")
    return str(svg_source)


def render_svg_to_png(svg_source: str | Path, size: Tuple[int, int]) -> Image.Image:
    """
    Render SVG content to an RGBA image at an exact inspection size.

    Resvg is the primary renderer because it is self-contained and matches modern
    browser SVG behaviour closely. Unlike the former implementation, this
    function never returns a fabricated white image when rendering fails.
    """
    svg_content = _load_svg_source(svg_source)
    width, height = (max(1, int(size[0])), max(1, int(size[1])))
    errors: list[str] = []

    def exact_size(png_bytes: bytes) -> Image.Image:
        rendered = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        if rendered.size != (width, height):
            rendered = rendered.resize((width, height), Image.Resampling.LANCZOS)
        return rendered

    try:
        import resvg_py

        png_bytes = resvg_py.svg_to_bytes(
            svg_string=svg_content,
            width=width,
            height=height,
            shape_rendering="geometric_precision",
            text_rendering="optimize_legibility",
            image_rendering="optimize_quality",
        )
        return exact_size(png_bytes)
    except Exception as exc:
        errors.append(f"resvg: {exc}")

    try:
        import cairosvg

        png_bytes = cairosvg.svg2png(
            bytestring=svg_content.encode("utf-8"),
            output_width=width,
            output_height=height,
        )
        return exact_size(png_bytes)
    except Exception as exc:
        errors.append(f"cairosvg: {exc}")

    try:
        result = subprocess.run(
            ["rsvg-convert", "--width", str(width), "--height", str(height)],
            input=svg_content.encode("utf-8"),
            capture_output=True,
            check=True,
            timeout=15,
        )
        return exact_size(result.stdout)
    except Exception as exc:
        errors.append(f"rsvg-convert: {exc}")

    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg

        with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", encoding="utf-8", delete=False) as handle:
            handle.write(svg_content)
            temp_svg = Path(handle.name)
        try:
            drawing = svg2rlg(str(temp_svg))
            if drawing is None:
                raise RuntimeError("SVG parser returned no drawing")
            png_bytes = renderPM.drawToString(drawing, fmt="PNG")
            return exact_size(png_bytes)
        finally:
            temp_svg.unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f"svglib: {exc}")

    detail = "; ".join(errors)
    raise RuntimeError(
        "SVG rendering is unavailable. Install resvg-py or a working Cairo/rsvg renderer. "
        f"Renderer diagnostics: {detail}"
    )


def _composite_rgba(array: np.ndarray, background: int) -> np.ndarray:
    rgb = array[..., :3].astype(np.float32)
    alpha = array[..., 3:4].astype(np.float32) / 255.0
    return rgb * alpha + float(background) * (1.0 - alpha)


def _ssim_score(first: np.ndarray, second: np.ndarray) -> float:
    if np.array_equal(first, second):
        return 1.0
    if _structural_similarity is None:
        arr1 = first.astype(np.float64)
        arr2 = second.astype(np.float64)
        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2
        mu1, mu2 = np.mean(arr1), np.mean(arr2)
        var1, var2 = np.var(arr1), np.var(arr2)
        covariance = np.mean((arr1 - mu1) * (arr2 - mu2))
        score = ((2 * mu1 * mu2 + c1) * (2 * covariance + c2)) / (
            (mu1**2 + mu2**2 + c1) * (var1 + var2 + c2)
        )
        return float(np.clip(score, 0.0, 1.0))

    min_side = min(first.shape[:2])
    kwargs: Dict[str, Any] = {"data_range": 255}
    if min_side < 7:
        win_size = max(3, min_side if min_side % 2 else min_side - 1)
        kwargs["win_size"] = win_size
    return float(np.clip(_structural_similarity(first, second, **kwargs), 0.0, 1.0))


def _luminance(rgb: np.ndarray) -> np.ndarray:
    return np.dot(rgb[..., :3], [0.2126, 0.7152, 0.0722])


def _edge_similarity(first_gray: np.ndarray, second_gray: np.ndarray) -> float:
    first_y, first_x = np.gradient(first_gray.astype(np.float32))
    second_y, second_x = np.gradient(second_gray.astype(np.float32))
    first_edges = np.sqrt(first_x**2 + first_y**2)
    second_edges = np.sqrt(second_x**2 + second_y**2)
    denominator = float(np.sum(first_edges + second_edges))
    if denominator < 1e-8:
        return 1.0
    difference = float(np.sum(np.abs(first_edges - second_edges)))
    return float(np.clip(1.0 - difference / denominator, 0.0, 1.0))


def calculate_quality_metrics(orig_img: Image.Image, rendered_img: Image.Image) -> Dict[str, float]:
    """Measure structure, edges, colour, alpha, and visible pixel error."""
    width, height = orig_img.size
    dimension_match = rendered_img.size == (width, height)
    rendered = rendered_img.resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")
    original = orig_img.convert("RGBA")

    orig_arr = np.asarray(original, dtype=np.float32)
    rend_arr = np.asarray(rendered, dtype=np.float32)
    orig_white = _composite_rgba(orig_arr, 255)
    rend_white = _composite_rgba(rend_arr, 255)
    orig_dark = _composite_rgba(orig_arr, 24)
    rend_dark = _composite_rgba(rend_arr, 24)

    orig_gray = _luminance(orig_white)
    rend_gray = _luminance(rend_white)
    dark_ssim = _ssim_score(_luminance(orig_dark).astype(np.uint8), _luminance(rend_dark).astype(np.uint8))
    light_ssim = _ssim_score(orig_gray.astype(np.uint8), rend_gray.astype(np.uint8))
    structural = (light_ssim + dark_ssim) / 2.0

    rgba_error = orig_arr - rend_arr
    mse = float(np.mean(rgba_error**2))
    psnr = 100.0 if mse == 0 else float(20 * math.log10(255.0 / math.sqrt(mse)))

    colour_delta = np.linalg.norm(orig_white - rend_white, axis=2) / math.sqrt(3 * 255**2)
    color_similarity = float(np.clip(1.0 - np.mean(colour_delta), 0.0, 1.0))
    alpha_similarity = float(
        np.clip(1.0 - np.mean(np.abs(orig_arr[..., 3] - rend_arr[..., 3])) / 255.0, 0.0, 1.0)
    )
    edge_similarity = _edge_similarity(orig_gray, rend_gray)

    visible_delta = np.max(np.abs(orig_white - rend_white), axis=2)
    pixel_diff_ratio = float(np.mean(visible_delta > 15.0))
    quality_score = float(
        np.clip(
            0.42 * structural
            + 0.28 * edge_similarity
            + 0.20 * color_similarity
            + 0.10 * alpha_similarity,
            0.0,
            1.0,
        )
    )

    return {
        "ssim": structural,
        "psnr": psnr,
        "mse": mse,
        "pixel_diff_ratio": pixel_diff_ratio,
        "edge_similarity": edge_similarity,
        "color_similarity": color_similarity,
        "alpha_similarity": alpha_similarity,
        "quality_score": quality_score,
        "dimension_match": 1.0 if dimension_match else 0.0,
    }


def _sample_colour_count(image: Image.Image, sample_size: int = 192) -> int:
    """Return a bounded colour-cardinality signal without scanning every pixel."""
    sample = image.convert("RGBA")
    sample.thumbnail((sample_size, sample_size), Image.Resampling.BILINEAR)
    colours = np.asarray(sample, dtype=np.uint8).reshape(-1, 4)
    return int(np.unique(colours, axis=0).shape[0])


def _premultiplied(pixels: np.ndarray) -> np.ndarray:
    """Return integer premultiplied RGBA using the compositor's rounding.

    Two RGBA buffers that are equal here composite identically over every
    backdrop, which is the operative definition of "the same image". Comparing
    raw channels is wrong in two ways: fully transparent source pixels carry
    arbitrary colour that a conforming renderer discards, and a renderer with an
    8-bit premultiplied pipeline cannot round-trip unpremultiplied colour at low
    alpha. Both produce large raw deltas for pixel-perfect output.
    """
    values = pixels.astype(np.int32)
    alpha = values[..., 3]
    channels = [(values[..., index] * alpha + 127) // 255 for index in range(3)]
    return np.stack(channels + [alpha], axis=-1)


def measure_bit_parity(
    original: Image.Image, rendered: Image.Image
) -> Dict[str, Any]:
    """Compare a render against its source for exact pixel parity."""
    source = np.asarray(original.convert("RGBA"), dtype=np.uint8)
    proof = np.asarray(rendered.convert("RGBA"), dtype=np.uint8)
    if source.shape != proof.shape:
        return {
            "bit_exact": False,
            "bit_exact_rgba": False,
            "mismatched_pixels": int(source.shape[0] * source.shape[1]),
            "max_premultiplied_delta": 255,
            "parity_digest": "",
        }
    source_premultiplied = _premultiplied(source)
    proof_premultiplied = _premultiplied(proof)
    delta = np.abs(source_premultiplied - proof_premultiplied)
    mismatched = int(np.count_nonzero(delta.max(axis=2)))
    return {
        "bit_exact": mismatched == 0,
        "bit_exact_rgba": bool(np.array_equal(source, proof)),
        "mismatched_pixels": mismatched,
        "max_premultiplied_delta": int(delta.max()) if delta.size else 0,
        "parity_digest": hashlib.sha256(
            source_premultiplied.astype(np.uint8).tobytes()
        ).hexdigest(),
    }


def validate_output(
    svg_content: str,
    original: Image.Image,
    rendered: Image.Image,
    *,
    target_quality: float = 0.0,
    max_paths: int = 80_000,
    max_svg_bytes: int = 16_000_000,
    max_path_commands: int | None = None,
    max_images: int | None = None,
    validity_profile: str = "general",
    require_bit_parity: bool = False,
) -> ValidityResult:
    """Apply non-negotiable output gates before an artifact can be called valid."""
    errors: list[str] = []
    warnings: list[str] = []
    source = original.convert("RGBA")
    proof = rendered.convert("RGBA")
    dimension_match = proof.size == source.size
    if not dimension_match:
        errors.append(
            f"Rendered dimensions {proof.size[0]}×{proof.size[1]} do not match "
            f"source {source.size[0]}×{source.size[1]}."
        )

    if "<svg" not in svg_content.lower() or "</svg>" not in svg_content.lower():
        errors.append("Output is not a complete SVG document.")

    path_count = svg_content.count("<path")
    svg_bytes = len(svg_content.encode("utf-8"))
    path_command_count = 0
    image_count = 0
    try:
        parsed = ET.fromstring(svg_content)
        for element in parsed.iter():
            name = element.tag.rsplit("}", 1)[-1]
            if name == "path":
                path_command_count += len(
                    re.findall(r"[AaCcHhLlMmQqSsTtVvZz]", element.get("d", ""))
                )
            elif name == "image":
                image_count += 1
    except ET.ParseError:
        pass
    if path_count > max_paths:
        errors.append(f"Path budget exceeded: {path_count:,} > {max_paths:,}.")
    if max_path_commands is not None and path_command_count > max_path_commands:
        errors.append(
            f"Path-command budget exceeded: {path_command_count:,} > {max_path_commands:,}."
        )
    if max_images is not None and image_count > max_images:
        errors.append(f"Embedded-image budget exceeded: {image_count:,} > {max_images:,}.")
    if svg_bytes > max_svg_bytes:
        errors.append(f"SVG byte budget exceeded: {svg_bytes:,} > {max_svg_bytes:,}.")

    source_arr = np.asarray(source, dtype=np.uint8)
    proof_arr = np.asarray(proof, dtype=np.uint8)
    original_alpha = float(np.mean(source_arr[..., 3] > 8))
    rendered_alpha = float(np.mean(proof_arr[..., 3] > 8))
    minimum_alpha = max(0.005, original_alpha * 0.50)
    if rendered_alpha < minimum_alpha:
        errors.append(
            f"Alpha coverage collapsed from {original_alpha:.3f} to {rendered_alpha:.3f}."
        )

    original_colours = _sample_colour_count(source)
    rendered_colours = _sample_colour_count(proof)
    if original_colours >= 32 and rendered_colours <= 4:
        errors.append(
            f"Colour/entropy collapse detected: {original_colours} sampled source colours "
            f"became {rendered_colours}."
        )

    if rendered_alpha < 0.0001:
        errors.append("Rendered output is empty.")

    parity = measure_bit_parity(source, proof)
    if require_bit_parity and not parity["bit_exact"]:
        errors.append(
            f"Bit parity failed: {parity['mismatched_pixels']:,} pixels differ "
            f"(max premultiplied delta {parity['max_premultiplied_delta']})."
        )

    metrics = calculate_quality_metrics(source, proof)
    # A render proven identical to its source cannot be failed by an
    # approximate similarity score. The float pipeline returns 0.9999999999999999
    # for a perfect match, so a requested target of 1.0 would reject an output
    # with zero differing pixels. Bit parity is the stronger statement and wins.
    if target_quality > 0 and not parity["bit_exact"] and metrics["quality_score"] < target_quality:
        errors.append(
            f"Quality target missed: {metrics['quality_score']:.4f} < {target_quality:.4f}."
        )
    elif target_quality <= 0 and metrics["quality_score"] < 0.70:
        warnings.append(f"Low measured quality: {metrics['quality_score']:.4f}.")

    return ValidityResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        width=proof.size[0],
        height=proof.size[1],
        dimension_match=dimension_match,
        original_alpha_coverage=original_alpha,
        rendered_alpha_coverage=rendered_alpha,
        original_colour_count=original_colours,
        rendered_colour_count=rendered_colours,
        path_count=path_count,
        path_command_count=path_command_count,
        image_count=image_count,
        svg_bytes=svg_bytes,
        validity_profile=validity_profile,
        bit_exact=bool(parity["bit_exact"]),
        bit_exact_rgba=bool(parity["bit_exact_rgba"]),
        mismatched_pixels=int(parity["mismatched_pixels"]),
        max_premultiplied_delta=int(parity["max_premultiplied_delta"]),
        parity_digest=str(parity["parity_digest"]),
    )


def calculate_similarity(orig_img: Image.Image, rendered_img: Image.Image) -> Tuple[float, float, float, float]:
    """Backward-compatible four-metric similarity interface."""
    metrics = calculate_quality_metrics(orig_img, rendered_img)
    return metrics["ssim"], metrics["psnr"], metrics["mse"], metrics["pixel_diff_ratio"]


def create_difference_map(orig_img: Image.Image, rendered_img: Image.Image) -> Image.Image:
    """Create a dark-to-amber visual error map for detailed inspection."""
    size = orig_img.size
    original = np.asarray(orig_img.convert("RGBA"), dtype=np.float32)
    rendered = np.asarray(
        rendered_img.resize(size, Image.Resampling.LANCZOS).convert("RGBA"), dtype=np.float32
    )
    orig_visible = _composite_rgba(original, 255)
    rend_visible = _composite_rgba(rendered, 255)
    rgb_delta = np.linalg.norm(orig_visible - rend_visible, axis=2) / math.sqrt(3 * 255**2)
    alpha_delta = np.abs(original[..., 3] - rendered[..., 3]) / 255.0
    heat = np.clip(np.maximum(rgb_delta, alpha_delta) * 4.0, 0.0, 1.0)

    output = np.empty((*heat.shape, 4), dtype=np.uint8)
    output[..., 0] = (13 + heat * 242).astype(np.uint8)
    output[..., 1] = (18 + heat * (1.0 - heat) * 170).astype(np.uint8)
    output[..., 2] = (28 + (1.0 - heat) * 16).astype(np.uint8)
    output[..., 3] = 255
    return Image.fromarray(output, mode="RGBA")


def _copy_config(config: TracingConfig, **updates: Any) -> TracingConfig:
    values = config.to_dict()
    values.update(updates)
    return TracingConfig.from_dict(values)


def _candidate_configs(initial: TracingConfig, profile: str) -> Iterable[TracingConfig]:
    detail = _copy_config(
        initial,
        color_precision=min(8, initial.color_precision + 1),
        layer_difference=max(1, initial.layer_difference - 4),
        filter_speckle=max(0, initial.filter_speckle - 1),
        length_threshold=max(2.5, initial.length_threshold - 0.5),
        max_iterations=min(40, initial.max_iterations + 6),
        path_precision=min(6, initial.path_precision + 1),
    )
    edge = _copy_config(
        detail,
        corner_threshold=max(5, initial.corner_threshold - 15),
        splice_threshold=max(5, initial.splice_threshold - 12),
        max_iterations=min(40, initial.max_iterations + 10),
    )
    colour = _copy_config(
        initial,
        color_precision=min(8, initial.color_precision + 2),
        layer_difference=max(1, initial.layer_difference // 2),
        filter_speckle=max(0, initial.filter_speckle - 2),
        path_precision=min(6, initial.path_precision + 1),
    )
    compact = _copy_config(
        initial,
        color_precision=max(2, initial.color_precision - 1),
        layer_difference=min(64, initial.layer_difference + 8),
        filter_speckle=min(50, initial.filter_speckle + 2),
        length_threshold=min(10.0, initial.length_threshold + 0.75),
        max_iterations=max(4, initial.max_iterations - 2),
        path_precision=max(1, initial.path_precision - 1),
    )
    compact_plus = _copy_config(
        compact,
        layer_difference=min(96, compact.layer_difference + 8),
        filter_speckle=min(50, compact.filter_speckle + 2),
        length_threshold=min(10.0, compact.length_threshold + 0.75),
    )

    if profile == "fidelity":
        return (initial, detail, edge, colour)
    if profile == "compact":
        return (initial, compact, compact_plus, detail)
    return (initial, detail, compact, edge)


def auto_tune_conversion(
    converter_func,
    input_path: str | Path,
    initial_config: TracingConfig,
    target_ssim: float = 0.90,
    max_iters: int = 4,
    quality_profile: str = "balanced",
    max_paths: int = 80_000,
    max_svg_bytes: int = 16_000_000,
) -> VerificationResult:
    """Evaluate distinct candidates and select for fidelity, balance, or compactness."""
    profile = quality_profile if quality_profile in {"fidelity", "balanced", "compact"} else "balanced"
    input_path = Path(input_path)
    with Image.open(input_path) as source:
        original = source.convert("RGBA")

    records: list[Dict[str, Any]] = []
    candidates = list(_candidate_configs(initial_config, profile))[: max(1, min(4, int(max_iters)))]
    for config in candidates:
        svg_code = converter_func(input_path, config=config)
        path_count = svg_code.count("<path")
        svg_bytes = len(svg_code.encode("utf-8"))
        if path_count > max_paths or svg_bytes > max_svg_bytes:
            records.append(
                {
                    "config": config,
                    "metrics": None,
                    "path_count": path_count,
                    "svg_bytes": svg_bytes,
                    "rejected": True,
                }
            )
            continue
        rendered = render_svg_to_png(svg_code, original.size)
        metrics = calculate_quality_metrics(original, rendered)
        validity = validate_output(
            svg_code,
            original,
            rendered,
            max_paths=max_paths,
            max_svg_bytes=max_svg_bytes,
        )
        records.append(
            {
                "config": config,
                "metrics": metrics,
                "path_count": path_count,
                "svg_bytes": svg_bytes,
                "validity": validity,
                "rejected": not validity.passed,
            }
        )

    eligible = [record for record in records if not record.get("rejected") and record["metrics"]]
    if not eligible:
        raise RuntimeError("Every verification candidate failed a hard validity or complexity gate.")

    complexities = [record["path_count"] + record["svg_bytes"] / 1024.0 for record in eligible]
    low, high = min(complexities), max(complexities)
    penalty = {"fidelity": 0.0, "balanced": 0.025, "compact": 0.09}[profile]
    history: list[Dict[str, Any]] = []
    for record, complexity in zip(eligible, complexities):
        normalized = 0.0 if high == low else (complexity - low) / (high - low)
        record["selection_score"] = record["metrics"]["quality_score"] - penalty * normalized
        history.append(
            {
                "quality_score": round(record["metrics"]["quality_score"], 6),
                "selection_score": round(record["selection_score"], 6),
                "path_count": record["path_count"],
                "svg_bytes": record["svg_bytes"],
                "config": record["config"].to_dict(),
            }
        )

    for record in records:
        if record not in eligible:
            history.append(
                {
                    "rejected": True,
                    "reason": "hard validity or complexity gate",
                    "path_count": record["path_count"],
                    "svg_bytes": record["svg_bytes"],
                    "config": record["config"].to_dict(),
                }
            )

    best = max(eligible, key=lambda record: record["selection_score"])
    metrics = best["metrics"]
    return VerificationResult(
        ssim=metrics["ssim"],
        psnr=metrics["psnr"],
        mse=metrics["mse"],
        pixel_diff_ratio=metrics["pixel_diff_ratio"],
        passed=metrics["quality_score"] >= target_ssim,
        iterations_used=len(records),
        best_config=best["config"],
        quality_score=metrics["quality_score"],
        edge_similarity=metrics["edge_similarity"],
        color_similarity=metrics["color_similarity"],
        alpha_similarity=metrics["alpha_similarity"],
        path_count=best["path_count"],
        svg_bytes=best["svg_bytes"],
        quality_profile=profile,
        candidate_history=history,
        validity=best.get("validity"),
    )
