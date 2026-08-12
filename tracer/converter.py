"""
Main Tracer conversion engine orchestrating all translation stages.
"""

from __future__ import annotations
from dataclasses import asdict
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
from PIL import Image
import vtracer

from .config import PRESETS, TracingConfig, VerificationResult
from .analyzer import recommend_preset
from .bg_remover import remove_background
from .optimizer import optimize_svg
from .logo_postproc import (
    LOGO_MAX_PATH_COMMANDS,
    LOGO_MAX_PATHS,
    LOGO_MAX_SVG_BYTES,
    assess_logo_editability,
    build_logo_gradient_model,
    compose_gradient_logo_svg,
    detect_logo_crop,
    postprocess_logo_svg,
    prepare_logo_trace_image,
    restore_logo_canvas,
)
from .verifier import (
    auto_tune_conversion,
    calculate_quality_metrics,
    render_svg_to_png,
    validate_output,
)


def preprocess_image(
    input_path: str | Path,
    max_dim: int = 4000,
    remove_bg: bool = False,
    bg_method: str = "auto",
) -> Tuple[Path, tempfile.TemporaryDirectory | None]:
    """
    Preprocesses raster image prior to vectorization:
    - Performs optional background removal
    - Downscales images larger than max_dim to optimize VTracer performance
    Returns (processed_image_path, temp_dir_handle)
    """
    input_path = Path(input_path)
    img = Image.open(input_path)

    temp_dir = tempfile.TemporaryDirectory()
    tmp_path = Path(temp_dir.name) / "processed_input.png"

    if remove_bg:
        img = remove_background(input_path, method=bg_method)

    # Downscale very large images for faster VTracer curve fitting
    w, h = img.size
    if max(w, h) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    img.convert("RGBA").save(tmp_path, "PNG")
    return tmp_path, temp_dir


def _copy_trace_config(config: TracingConfig, **updates: Any) -> TracingConfig:
    values = config.to_dict()
    values.update(updates)
    return TracingConfig.from_dict(values)


def _logo_candidate_specs(
    initial: TracingConfig,
) -> list[tuple[str, int | None, TracingConfig]]:
    """Return a bounded native/compact/palette frontier for logo artwork."""
    compact = _copy_trace_config(
        initial,
        color_precision=min(4, initial.color_precision),
        layer_difference=max(32, initial.layer_difference),
        filter_speckle=max(8, initial.filter_speckle),
        length_threshold=max(5.5, initial.length_threshold),
        max_iterations=min(12, initial.max_iterations),
        path_precision=min(2, initial.path_precision),
    )
    palette = _copy_trace_config(
        compact,
        layer_difference=24,
        filter_speckle=6,
        length_threshold=5.0,
    )
    return [
        ("native", None, initial),
        ("compact", None, compact),
        ("palette_16", 16, palette),
        ("palette_8", 8, palette),
    ]


class TracerConverter:
    """
    High-level PNG -> SVG Translator class.
    """

    def __init__(self, default_preset: str = "logo"):
        self.default_preset = default_preset

    def convert_image(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        preset: str | None = None,
        auto_preset: bool = False,
        remove_bg: bool = False,
        bg_method: str = "auto",
        verify: bool = False,
        target_ssim: float = 0.90,
        quality_profile: str = "balanced",
        max_tune_iterations: int = 4,
        optimize: bool = True,
        optimization_level: str = "safe",
        max_dim: int = 2000,
        **override_kwargs,
    ) -> Tuple[str, Optional[VerificationResult], Dict[str, Any]]:
        """
        Translates raster PNG/JPG image to vector SVG.
        Returns tuple: (svg_content, verification_result, conversion_metadata)
        """
        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")
        with Image.open(input_path) as source:
            source_image = source.convert("RGBA")

        # 1. Image analysis and preset determination
        analysis_info = {}
        selected_preset = preset or self.default_preset

        if auto_preset or preset is None:
            selected_preset, analysis_info, recommended_overrides = recommend_preset(input_path)
            # Combine recommended overrides with user explicit overrides
            for k, v in recommended_overrides.items():
                if k not in override_kwargs or override_kwargs[k] is None:
                    override_kwargs[k] = v

        preset_cfg = PRESETS.get(selected_preset, PRESETS["default"])
        merged_cfg_dict = {**preset_cfg, **{k: v for k, v in override_kwargs.items() if v is not None}}
        config = TracingConfig.from_dict(merged_cfg_dict)

        # UI screenshots are most faithful at native size. Avoid a near-threshold
        # resize while retaining an explicit 4096 px ceiling for the stable path.
        if (
            selected_preset == "complex_map_ui"
            or analysis_info.get("scene_class") == "ui_screenshot"
        ) and max(source_image.size) <= 4096:
            max_dim = max(max_dim, max(source_image.size))
        if selected_preset == "logo" and max(source_image.size) <= 4096:
            # Resampling gradients and antialiased edges before segmentation can
            # create thousands of false micro-regions. Keep common logo assets at
            # their native size and let the explicit logo frontier control cost.
            max_dim = max(max_dim, max(source_image.size))

        # 2. Preprocess image (BG removal, downscaling)
        proc_path, temp_dir = preprocess_image(
            input_path, max_dim=max_dim, remove_bg=remove_bg, bg_method=bg_method
        )

        verification_res: Optional[VerificationResult] = None
        logo_strategy: Dict[str, Any] | None = None

        def _raw_vtracer_convert(target_img_path: Path, config: TracingConfig | None = None, cfg: TracingConfig | None = None) -> str:
            config = config or cfg or TracingConfig()
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp_svg:
                out_tmp = tmp_svg.name

            vtracer.convert_image_to_svg_py(
                str(target_img_path),
                out_tmp,
                colormode=config.colormode,
                hierarchical=config.hierarchical,
                mode=config.mode,
                filter_speckle=config.filter_speckle,
                color_precision=config.color_precision,
                layer_difference=config.layer_difference,
                corner_threshold=config.corner_threshold,
                length_threshold=config.length_threshold,
                max_iterations=config.max_iterations,
                splice_threshold=config.splice_threshold,
                path_precision=config.path_precision,
            )

            svg_str = Path(out_tmp).read_text(encoding="utf-8")
            Path(out_tmp).unlink(missing_ok=True)
            return svg_str

        # 3. Perform conversion. Logos always inspect a bounded quality/editability
        # frontier; other presets retain the opt-in general verification loop.
        if selected_preset == "logo":
            with Image.open(proc_path) as processed_source:
                processed_logo = processed_source.convert("RGBA")
            crop = detect_logo_crop(processed_logo)
            candidate_records: list[Dict[str, Any]] = []
            trace_directory = Path(temp_dir.name) if temp_dir is not None else proc_path.parent

            gradient_model = build_logo_gradient_model(processed_logo)
            if gradient_model is not None:
                large_logo = max(processed_logo.size) > 768
                gradient_config = _copy_trace_config(
                    config,
                    mode="spline",
                    color_precision=max(6 if large_logo else 7, config.color_precision),
                    layer_difference=16 if large_logo else 12,
                    filter_speckle=6 if large_logo else 4,
                    length_threshold=4.5 if large_logo else 4.0,
                    max_iterations=12 if large_logo else 15,
                    path_precision=max(3, config.path_precision),
                )
                foreground_path = trace_directory / "logo-gradient-foreground.png"
                gradient_model.foreground.save(foreground_path, "PNG")
                foreground_svg = _raw_vtracer_convert(foreground_path, gradient_config)
                candidate_svg = compose_gradient_logo_svg(gradient_model, foreground_svg)
                candidate_svg = postprocess_logo_svg(candidate_svg)
                if optimize:
                    candidate_svg = optimize_svg(
                        candidate_svg,
                        precision=gradient_config.path_precision,
                        optimization_level=optimization_level,
                    )
                editability = assess_logo_editability(candidate_svg)
                rendered = render_svg_to_png(candidate_svg, source_image.size)
                metrics = calculate_quality_metrics(source_image, rendered)
                validity = validate_output(
                    candidate_svg,
                    source_image,
                    rendered,
                    max_paths=LOGO_MAX_PATHS,
                    max_svg_bytes=LOGO_MAX_SVG_BYTES,
                    max_path_commands=LOGO_MAX_PATH_COMMANDS,
                    max_images=0,
                    validity_profile="editable_logo",
                )
                candidate_records.append(
                    {
                        "variant": "gradient_geometry",
                        "palette_size": None,
                        "config": gradient_config,
                        "svg": candidate_svg,
                        "metrics": metrics,
                        "editability": editability,
                        "validity": validity,
                        "model": gradient_model.to_dict(),
                        "rejected": not editability.passed or not validity.passed,
                    }
                )

            for variant_name, palette_size, candidate_config in _logo_candidate_specs(config):
                candidate_image = prepare_logo_trace_image(
                    processed_logo,
                    crop,
                    palette_size=palette_size,
                )
                candidate_path = trace_directory / f"logo-{variant_name}.png"
                candidate_image.save(candidate_path, "PNG")
                candidate_svg = _raw_vtracer_convert(candidate_path, candidate_config)
                candidate_svg = restore_logo_canvas(candidate_svg, crop)
                candidate_svg = postprocess_logo_svg(candidate_svg)
                if optimize:
                    candidate_svg = optimize_svg(
                        candidate_svg,
                        precision=candidate_config.path_precision,
                        optimization_level=optimization_level,
                    )
                editability = assess_logo_editability(candidate_svg)
                rendered = render_svg_to_png(candidate_svg, source_image.size)
                metrics = calculate_quality_metrics(source_image, rendered)
                validity = validate_output(
                    candidate_svg,
                    source_image,
                    rendered,
                    max_paths=LOGO_MAX_PATHS,
                    max_svg_bytes=LOGO_MAX_SVG_BYTES,
                    max_path_commands=LOGO_MAX_PATH_COMMANDS,
                    max_images=0,
                    validity_profile="editable_logo",
                )
                candidate_records.append(
                    {
                        "variant": variant_name,
                        "palette_size": palette_size,
                        "config": candidate_config,
                        "svg": candidate_svg,
                        "metrics": metrics,
                        "editability": editability,
                        "validity": validity,
                        "model": None,
                        "rejected": not editability.passed or not validity.passed,
                    }
                )

            eligible = [record for record in candidate_records if not record["rejected"]]
            if not eligible:
                reasons = sorted(
                    {
                        error
                        for record in candidate_records
                        for error in (
                            list(record["editability"].errors)
                            + list(record["validity"].errors)
                        )
                    }
                )
                raise RuntimeError(
                    "Every logo candidate failed the editability contract: "
                    + "; ".join(reasons)
                )

            complexities = [
                record["editability"].path_count
                + record["editability"].path_command_count / 200.0
                + record["editability"].svg_bytes / 2048.0
                for record in eligible
            ]
            low, high = min(complexities), max(complexities)
            profile = quality_profile if quality_profile in {"fidelity", "balanced", "compact"} else "balanced"
            penalty = {"fidelity": 0.01, "balanced": 0.04, "compact": 0.10}[profile]
            for record, complexity in zip(eligible, complexities):
                normalized = 0.0 if high == low else (complexity - low) / (high - low)
                record["selection_score"] = record["metrics"]["quality_score"] - penalty * normalized
            best_quality = max(record["metrics"]["quality_score"] for record in eligible)
            quality_tolerance = {"fidelity": 0.001, "balanced": 0.0025, "compact": 0.015}[profile]
            visual_shortlist = [
                record
                for record in eligible
                if record["metrics"]["quality_score"] >= best_quality - quality_tolerance
            ]
            structured_tolerance = {"fidelity": 0.004, "balanced": 0.012, "compact": 0.0}[profile]
            structured_shortlist = [
                record
                for record in eligible
                if record["model"] is not None
                and record["metrics"]["quality_score"] >= best_quality - structured_tolerance
            ]
            if structured_shortlist:
                best_logo = max(
                    structured_shortlist,
                    key=lambda record: record["metrics"]["quality_score"],
                )
                selection_reason = "structured_gradient_within_visual_tolerance"
            else:
                best_logo = max(visual_shortlist, key=lambda record: record["selection_score"])
                selection_reason = "quality_editability_frontier"
            svg_content = best_logo["svg"]
            config = best_logo["config"]

            history: list[Dict[str, Any]] = []
            for record in candidate_records:
                history.append(
                    {
                        "variant": record["variant"],
                        "palette_size": record["palette_size"],
                        "quality_score": round(record["metrics"]["quality_score"], 6),
                        "ssim": round(record["metrics"]["ssim"], 6),
                        "edge_similarity": round(record["metrics"]["edge_similarity"], 6),
                        "selection_score": round(record.get("selection_score", -1.0), 6),
                        "rejected": record["rejected"],
                        "editability": record["editability"].to_dict(),
                        "config": record["config"].to_dict(),
                        "model": record["model"],
                    }
                )
            logo_strategy = {
                "strategy": "quality_editability_frontier",
                "selected_variant": best_logo["variant"],
                "selected_palette_size": best_logo["palette_size"],
                "selected_model": best_logo["model"],
                "selection_reason": selection_reason,
                "crop": crop.to_dict(),
                "editability": best_logo["editability"].to_dict(),
                "candidate_history": history,
            }
            if verify:
                best_metrics = best_logo["metrics"]
                verification_res = VerificationResult(
                    ssim=best_metrics["ssim"],
                    psnr=best_metrics["psnr"],
                    mse=best_metrics["mse"],
                    pixel_diff_ratio=best_metrics["pixel_diff_ratio"],
                    passed=best_metrics["quality_score"] >= target_ssim,
                    iterations_used=len(candidate_records),
                    best_config=config,
                    quality_score=best_metrics["quality_score"],
                    edge_similarity=best_metrics["edge_similarity"],
                    color_similarity=best_metrics["color_similarity"],
                    alpha_similarity=best_metrics["alpha_similarity"],
                    path_count=best_logo["editability"].path_count,
                    svg_bytes=best_logo["editability"].svg_bytes,
                    quality_profile=profile,
                    candidate_history=history,
                    validity=best_logo["validity"],
                )
        elif verify:
            verification_res = auto_tune_conversion(
                converter_func=_raw_vtracer_convert,
                input_path=proc_path,
                initial_config=config,
                target_ssim=target_ssim,
                max_iters=max_tune_iterations,
                quality_profile=quality_profile,
                max_paths=80_000,
                max_svg_bytes=16_000_000,
            )
            config = verification_res.best_config
            svg_content = _raw_vtracer_convert(proc_path, config)
        else:
            svg_content = _raw_vtracer_convert(proc_path, config)

        # 4. General SVG optimization. Logo candidates were optimized before
        # ranking so their byte and command budgets describe the delivered file.
        if optimize and selected_preset != "logo":
            svg_content = optimize_svg(
                svg_content,
                precision=config.path_precision,
                optimization_level=optimization_level,
            )

        final_render = render_svg_to_png(svg_content, source_image.size)
        if selected_preset == "logo":
            final_validity = validate_output(
                svg_content,
                source_image,
                final_render,
                target_quality=target_ssim if verify else 0.0,
                max_paths=LOGO_MAX_PATHS,
                max_svg_bytes=LOGO_MAX_SVG_BYTES,
                max_path_commands=LOGO_MAX_PATH_COMMANDS,
                max_images=0,
                validity_profile="editable_logo",
            )
        else:
            final_validity = validate_output(
                svg_content,
                source_image,
                final_render,
                target_quality=target_ssim if verify else 0.0,
                max_paths=80_000,
                max_svg_bytes=16_000_000,
            )
        if verification_res is not None:
            verification_res.validity = final_validity
            verification_res.passed = verification_res.passed and final_validity.passed

        # Cleanup temporary files
        if temp_dir:
            temp_dir.cleanup()

        # 5. Output to file if output_path is specified
        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(svg_content, encoding="utf-8")

        metadata = {
            "preset_used": selected_preset,
            "final_config": config.to_dict(),
            "analysis": analysis_info,
            "remove_bg": remove_bg,
            "verified": verify,
            "quality_profile": quality_profile,
            "optimization_level": optimization_level if optimize else "none",
            "validity": asdict(final_validity),
            "source_dimensions": list(source_image.size),
            "render_dimensions": list(final_render.size),
        }
        if logo_strategy is not None:
            metadata["logo_strategy"] = logo_strategy

        return svg_content, verification_res, metadata


def convert(
    input_path: str | Path,
    output_path: str | Path | None = None,
    preset: str | None = "logo",
    auto_preset: bool = False,
    remove_bg: bool = False,
    bg_method: str = "auto",
    verify: bool = False,
    target_ssim: float = 0.90,
    quality_profile: str = "balanced",
    max_tune_iterations: int = 4,
    optimize: bool = True,
    optimization_level: str = "safe",
    max_dim: int = 2000,
    **kwargs,
) -> str:
    """
    Convenience functional interface for PNG -> SVG conversion.
    Returns generated SVG string content. Write to output_path if provided.
    """
    converter = TracerConverter()
    svg_str, _, _ = converter.convert_image(
        input_path=input_path,
        output_path=output_path,
        preset=preset,
        auto_preset=auto_preset,
        remove_bg=remove_bg,
        bg_method=bg_method,
        verify=verify,
        target_ssim=target_ssim,
        quality_profile=quality_profile,
        max_tune_iterations=max_tune_iterations,
        optimize=optimize,
        optimization_level=optimization_level,
        max_dim=max_dim,
        **kwargs,
    )
    return svg_str
