"""
Image analyzer module for auto-preset detection and image profiling.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Tuple
from PIL import Image
import numpy as np


def analyze_image(image_path: str | Path) -> Dict[str, Any]:
    """
    Analyzes a raster image and extracts visual statistics.
    Returns dictionary with color count, saturation, edge density, B&W tendency, etc.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    with Image.open(image_path) as img:
        width, height = img.size
        has_alpha_channel = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

        # Convert to RGBA for uniform processing
        rgba_img = img.convert("RGBA")

        # Color quantize check (sample down for fast analysis)
        thumb = rgba_img.copy()
        thumb.thumbnail((400, 400), Image.Resampling.LANCZOS)

        # Unique colors count in thumbnail
        colors = thumb.getcolors(maxcolors=400 * 400)
        unique_colors_count = len(colors) if colors is not None else 500

        # Convert thumbnail to numpy array for channel statistics
        arr = np.array(thumb)
        rgb = arr[:, :, :3]
        alpha = arr[:, :, 3]

        # Calculate saturation and local chroma. Global average saturation is not
        # sufficient for screenshots: a white application canvas can dominate the
        # mean even when maps, photographs, markers, and controls are colourful.
        r, g, b = rgb[:, :, 0].astype(float), rgb[:, :, 1].astype(float), rgb[:, :, 2].astype(float)
        max_c = np.maximum(np.maximum(r, g), b)
        min_c = np.minimum(np.minimum(r, g), b)
        delta = max_c - min_c
        saturation = np.zeros_like(delta, dtype=float)
        np.divide(delta, max_c, out=saturation, where=max_c > 0)
        visible = alpha >= 32
        visible_count = max(1, int(np.count_nonzero(visible)))
        avg_saturation = float(np.mean(saturation[visible])) if np.any(visible) else 0.0
        saturation_p95 = float(np.percentile(saturation[visible], 95)) if np.any(visible) else 0.0

        chromatic = visible & (delta >= 16) & (saturation >= 0.10)
        strongly_chromatic = visible & (delta >= 28) & (saturation >= 0.20)
        chromatic_pixel_ratio = float(np.count_nonzero(chromatic) / visible_count)
        strong_chroma_ratio = float(np.count_nonzero(strongly_chromatic) / visible_count)
        near_white_ratio = float(np.count_nonzero(visible & (min_c >= 240)) / visible_count)
        near_dark_ratio = float(np.count_nonzero(visible & (max_c <= 64)) / visible_count)

        # Logos and compact illustrations may contain thousands of antialiased or
        # gradient colours while still being well represented by a small palette.
        # Measure that compressibility directly instead of treating sampled colour
        # cardinality as scene complexity.
        quantized_16 = thumb.convert("RGB").quantize(
            colors=16,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        ).convert("RGB")
        quantized_rgb = np.asarray(quantized_16, dtype=np.float32)
        rgb_error = rgb.astype(np.float32) - quantized_rgb
        palette_rmse_16 = float(
            np.sqrt(np.mean(np.square(rgb_error[visible])))
        ) if np.any(visible) else 0.0

        # Count only materially populated hue groups so anti-aliasing noise does not
        # invent colour evidence. This is a contradiction guard, not segmentation.
        hue_cluster_count = 0
        if np.any(chromatic):
            hue = np.asarray(thumb.convert("HSV"))[:, :, 0]
            hue_counts = np.bincount((hue[chromatic] // 16).astype(np.intp), minlength=16)
            minimum_cluster = max(4, int(np.count_nonzero(chromatic) * 0.025))
            hue_cluster_count = int(np.count_nonzero(hue_counts >= minimum_cluster))

        # A source is monochrome only when global and local evidence agree. This
        # prevents a mostly-white colour screenshot from ever entering binary mode.
        colour_contradiction = (
            chromatic_pixel_ratio >= 0.005
            or strong_chroma_ratio >= 0.002
            or saturation_p95 >= 0.16
            or hue_cluster_count >= 2
        )
        is_monochrome = avg_saturation < 0.08 and not colour_contradiction
        is_low_color = unique_colors_count <= 16
        transparency_ratio = float(np.mean(alpha < 250))

        # Estimate edge density / complexity using gradient magnitude
        gray = np.dot(rgb[..., :3], [0.2989, 0.5870, 0.1140])
        gy, gx = np.gradient(gray)
        grad_mag = np.sqrt(gx**2 + gy**2)
        edge_density = float(np.mean(grad_mag))

        aspect_ratio = width / max(1, height)

        # Large, detailed, mostly opaque sources with either a light application
        # canvas or a broad dark workspace are treated as UI screenshots. Requiring
        # near-white pixels alone misroutes dark creative/research workbenches as
        # gradient artwork.
        is_ui_screenshot = (
            max(width, height) >= 1000
            and unique_colors_count >= 256
            and edge_density >= 4.0
            and transparency_ratio < 0.10
            and (
                near_white_ratio >= 0.20
                or (near_dark_ratio >= 0.35 and aspect_ratio >= 1.20)
            )
        )

        # Check pixel-art characteristics (exact sharp blocky grid)
        is_pixel_art = width <= 256 and height <= 256 and unique_colors_count <= 64 and edge_density > 15.0

        # This is deliberately a conservative artwork classifier, not a claim
        # that arbitrary logos can be found inside arbitrary screenshots. It
        # catches bounded, low-edge, palette-compressible logo/icon inputs while
        # leaving detailed and photographic sources on the general routes.
        is_logo_art = (
            not is_ui_screenshot
            and not is_pixel_art
            and 32 <= min(width, height)
            and max(width, height) <= 1600
            and 0.18 <= aspect_ratio <= 6.0
            and edge_density <= 5.0
            and palette_rmse_16 <= 12.0
        )

        if is_pixel_art:
            scene_class = "pixel_art"
        elif is_ui_screenshot:
            scene_class = "ui_screenshot"
        elif is_monochrome:
            scene_class = "monochrome"
        elif unique_colors_count <= 24:
            scene_class = "flat_art"
        elif is_logo_art:
            scene_class = "logo_art"
        else:
            scene_class = "colour_art"

        return {
            "width": width,
            "height": height,
            "has_alpha": has_alpha_channel,
            "transparency_ratio": round(transparency_ratio, 4),
            "unique_colors": unique_colors_count,
            "avg_saturation": round(avg_saturation, 4),
            "saturation_p95": round(saturation_p95, 4),
            "chromatic_pixel_ratio": round(chromatic_pixel_ratio, 4),
            "strong_chroma_ratio": round(strong_chroma_ratio, 4),
            "near_white_ratio": round(near_white_ratio, 4),
            "near_dark_ratio": round(near_dark_ratio, 4),
            "hue_clusters": hue_cluster_count,
            "edge_density": round(edge_density, 2),
            "palette_rmse_16": round(palette_rmse_16, 2),
            "is_monochrome": is_monochrome,
            "is_ui_screenshot": is_ui_screenshot,
            "is_logo_art": is_logo_art,
            "is_low_color": is_low_color,
            "is_pixel_art": is_pixel_art,
            "scene_class": scene_class,
            "aspect_ratio": round(aspect_ratio, 2),
            "run_ratio": round(_horizontal_run_ratio(rgba_img), 4),
        }


def _horizontal_run_ratio(image: Image.Image, sample_rows: int = 256) -> float:
    """Return horizontal runs per pixel, measured at native resolution.

    This is a direct predictor of exact-codec cost: the encoder spends one
    rectangle per run, so 0.02 means flat content that encodes cheaply while
    0.6 means photographic texture where nearly every pixel is its own shape.
    It must be measured on native pixels because resampling invents new
    intermediate colours and destroys exactly the runs being counted.
    """
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    height, width = pixels.shape[:2]
    if height == 0 or width < 2:
        return 1.0
    if height > sample_rows:
        rows = np.linspace(0, height - 1, sample_rows).astype(int)
        pixels = pixels[rows]
    packed = pixels.view(np.uint32).reshape(pixels.shape[0], width)
    changes = int(np.count_nonzero(packed[:, 1:] != packed[:, :-1]))
    runs = changes + pixels.shape[0]
    return float(runs) / float(pixels.shape[0] * width)


def recommend_preset(image_path: str | Path) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    Analyzes the image and recommends the best preset and parameter tuning.
    Returns tuple: (recommended_preset_name, analysis_info, parameter_overrides)
    """
    stats = analyze_image(image_path)
    overrides: Dict[str, Any] = {}

    if stats["is_pixel_art"]:
        preset = "pixel"
    elif stats["is_ui_screenshot"]:
        preset = "complex_map_ui"
        overrides["color_precision"] = 8
        overrides["filter_speckle"] = 2
    elif stats["is_monochrome"]:
        preset = "lineart"
    elif stats["scene_class"] in {"flat_art", "logo_art"}:
        preset = "logo"
    elif stats["unique_colors"] > 120 and stats["edge_density"] > 20:
        # Complex UI screenshots, maps, and detailed photos
        preset = "precision_ultra"
        overrides["color_precision"] = 8
        overrides["filter_speckle"] = 1
    elif stats["unique_colors"] > 60 or stats["edge_density"] > 15:
        preset = "complex_map_ui"
        overrides["color_precision"] = 8
    else:
        preset = "precision_ultra"

    # Fine-tune based on image dimensions
    max_dim = max(stats["width"], stats["height"])
    if max_dim > 2500 and preset not in ("precision_ultra", "complex_map_ui"):
        overrides["filter_speckle"] = max(2, overrides.get("filter_speckle", 2))
    elif max_dim < 400 and preset != "pixel":
        overrides["filter_speckle"] = 1

    return preset, stats, overrides


#: Scene classes whose pixels are strongly run-compressible. The exact codec
#: encodes flat spans as single rectangles, so on this content bit parity costs
#: little and can compress below the source PNG. Photographic content is the
#: opposite case and is left to Hybrid Parity.
#: Measured horizontal runs per pixel above which exact geometry stops being a
#: competitive representation. Calibrated against the codec probe: flat UI
#: measures well below this and compresses smaller than its source PNG, while
#: photographic content sits far above it.
EXACT_RUN_RATIO_LIMIT = 0.12

_EXACT_FRIENDLY_CLASSES = {
    "ui_screenshot",
    "flat_art",
    "logo_art",
    "monochrome",
    "pixel_art",
}


def recommend_output_contract(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Recommend an honest representation and its fidelity controls."""
    scene_class = str(stats.get("scene_class", "colour_art"))
    run_ratio = float(stats.get("run_ratio", 1.0))

    # Absolute Parity is exact by construction, so it is preferred wherever its
    # byte cost is competitive. The decision uses measured run compressibility
    # rather than the scene label alone, because cost is a property of the
    # pixels: flat and UI content encodes cheaply at any edge density, and
    # photographic texture is expensive whatever the scene was classified as.
    if scene_class in _EXACT_FRIENDLY_CLASSES and run_ratio <= EXACT_RUN_RATIO_LIMIT:
        return {
            "output_mode": "absolute_parity",
            "target_quality": 1.0,
            "residual_threshold": 0,
            "residual_expansion": 0,
            "reason": (
                "Run-compressible content reaches exact pixel parity as pure "
                "vector geometry without embedding any raster image."
            ),
        }
    if scene_class in {"ui_screenshot", "colour_art"}:
        return {
            "output_mode": "hybrid_parity",
            "target_quality": 0.985,
            "residual_threshold": 4,
            "residual_expansion": 1,
            "reason": (
                "Photographic and high-texture content is cheaper to repair "
                "with a lossless raster residual than with exact geometry."
            ),
        }
    return {
        "output_mode": "pure_vector",
        "target_quality": 0.80 if scene_class in {"logo_art", "flat_art"} else 0.90,
        "residual_threshold": 4,
        "residual_expansion": 1,
        "reason": "Compact artwork is suitable for an editable vector-only result.",
    }
