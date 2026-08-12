"""Logo-specific isolation, cleanup, semantics, and editability gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

LOGO_MAX_PATHS = 512
LOGO_MAX_PATH_COMMANDS = 50_000
LOGO_MAX_SVG_BYTES = 2_000_000

_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?(?:\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_COMMAND_RE = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]")
_SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
_PRESERVED_TAGS = {"defs", "style", "metadata", "title", "desc"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class LogoCrop:
    """A crop that can be restored to the exact source canvas."""

    bounds: tuple[int, int, int, int]
    canvas_size: tuple[int, int]
    background_fill: str | None = None
    reason: str = "full_canvas"

    @property
    def used(self) -> bool:
        width, height = self.canvas_size
        return self.bounds != (0, 0, width, height)

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["bounds"] = list(self.bounds)
        result["canvas_size"] = list(self.canvas_size)
        result["used"] = self.used
        return result


@dataclass(frozen=True)
class LogoEditability:
    """Structural signals used to reject raster wrappers and path soup."""

    passed: bool
    errors: tuple[str, ...]
    path_count: int
    path_command_count: int
    shape_count: int
    image_count: int
    anonymous_shape_count: int
    svg_bytes: int

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["errors"] = list(self.errors)
        return result


@dataclass(frozen=True)
class LogoGradientModel:
    """A fitted smooth logo plate plus foreground pixels that need tracing."""

    canvas_size: tuple[int, int]
    background_fill: str | None
    plate_bounds: tuple[int, int, int, int]
    corner_radius: float
    gradient_start: tuple[float, float]
    gradient_end: tuple[float, float]
    stops: tuple[tuple[float, str], ...]
    foreground: Image.Image
    plate_iou: float
    inlier_rmse: float
    foreground_coverage: float

    def to_dict(self) -> dict[str, object]:
        return {
            "canvas_size": list(self.canvas_size),
            "background_fill": self.background_fill,
            "plate_bounds": list(self.plate_bounds),
            "corner_radius": round(self.corner_radius, 3),
            "gradient_start": [round(value, 3) for value in self.gradient_start],
            "gradient_end": [round(value, 3) for value in self.gradient_end],
            "stops": [[round(offset, 4), colour] for offset, colour in self.stops],
            "plate_iou": round(self.plate_iou, 6),
            "inlier_rmse": round(self.inlier_rmse, 6),
            "foreground_coverage": round(self.foreground_coverage, 6),
        }


def _border_pixels(array: np.ndarray) -> np.ndarray:
    if array.shape[0] < 2 or array.shape[1] < 2:
        return array.reshape(-1, 4)
    return np.concatenate(
        (array[0], array[-1], array[1:-1, 0], array[1:-1, -1]),
        axis=0,
    )


def _padded_bounds(mask: np.ndarray, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if xs.size == 0 or ys.size == 0:
        return None
    width, height = size
    padding = max(1, int(round(max(width, height) * 0.01)))
    left = max(0, int(xs.min()) - padding)
    top = max(0, int(ys.min()) - padding)
    right = min(width, int(xs.max()) + padding + 1)
    bottom = min(height, int(ys.max()) + padding + 1)
    if right - left < 2 or bottom - top < 2:
        return None
    return left, top, right, bottom


def detect_logo_crop(image: Image.Image) -> LogoCrop:
    """Find safe transparent or uniform-border margin without guessing scene content."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    full = (0, 0, width, height)
    array = np.asarray(rgba, dtype=np.uint8)
    border = _border_pixels(array)

    transparent_border = float(np.mean(border[:, 3] <= 8)) >= 0.80
    if transparent_border:
        bounds = _padded_bounds(array[:, :, 3] > 8, rgba.size)
        reason = "transparent_margin"
        background_fill = None
    else:
        border_rgb = border[:, :3].astype(np.float32)
        median = np.median(border_rgb, axis=0)
        border_distance = np.linalg.norm(border_rgb - median, axis=1)
        dispersion = float(np.percentile(border_distance, 95))
        if dispersion > 10.0 or float(np.mean(border[:, 3] >= 250)) < 0.98:
            return LogoCrop(full, rgba.size)
        rgb = array[:, :, :3].astype(np.float32)
        distance = np.linalg.norm(rgb - median, axis=2)
        threshold = max(12.0, dispersion * 3.0)
        bounds = _padded_bounds(distance > threshold, rgba.size)
        reason = "uniform_border"
        background_fill = "#" + "".join(f"{int(round(channel)):02X}" for channel in median)

    if bounds is None:
        return LogoCrop(full, rgba.size)
    left, top, right, bottom = bounds
    crop_coverage = ((right - left) * (bottom - top)) / max(1, width * height)
    if crop_coverage >= 0.92:
        return LogoCrop(full, rgba.size)
    return LogoCrop(bounds, rgba.size, background_fill, reason)


def prepare_logo_trace_image(
    image: Image.Image,
    crop: LogoCrop,
    *,
    palette_size: int | None = None,
) -> Image.Image:
    """Return a crop-aware, optionally palette-limited logo tracing input."""
    working = image.convert("RGBA").crop(crop.bounds)
    if palette_size is None:
        return working
    palette_size = max(2, min(64, int(palette_size)))
    alpha = working.getchannel("A")
    quantized = working.convert("RGB").quantize(
        colors=palette_size,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    ).convert("RGBA")
    quantized.putalpha(alpha)
    return quantized


def _hex_colour(colour: np.ndarray | tuple[float, ...] | list[float]) -> str:
    values = np.clip(np.rint(colour), 0, 255).astype(np.uint8)
    return "#" + "".join(f"{int(channel):02X}" for channel in values[:3])


def _rounded_rectangle_mask(
    size: tuple[int, int],
    bounds: tuple[int, int, int, int],
    radius: float,
) -> np.ndarray:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left, top, right, bottom = bounds
    draw.rounded_rectangle(
        (left, top, max(left, right - 1), max(top, bottom - 1)),
        radius=max(0, int(round(radius))),
        fill=255,
    )
    return np.asarray(mask, dtype=np.uint8) > 0


def _fit_logo_plate(
    array: np.ndarray,
) -> tuple[tuple[int, int, int, int], float, float, str | None, np.ndarray] | None:
    """Fit a full-canvas or bordered rounded plate without logo-specific constants."""
    height, width = array.shape[:2]
    border = _border_pixels(array)
    border_rgb = border[:, :3].astype(np.float32)
    background = np.median(border_rgb, axis=0)
    dispersion = float(
        np.percentile(np.linalg.norm(border_rgb - background, axis=1), 95)
    )
    if dispersion > 12.0:
        bounds = (0, 0, width, height)
        mask = np.ones((height, width), dtype=bool)
        return bounds, 0.0, 1.0, None, mask

    distance = np.linalg.norm(array[:, :, :3].astype(np.float32) - background, axis=2)
    foreground = distance > max(12.0, dispersion * 3.0)
    ys, xs = np.nonzero(foreground)
    if xs.size == 0 or float(np.mean(foreground)) < 0.18:
        return None
    bounds = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    left, top, right, bottom = bounds
    plate_width, plate_height = right - left, bottom - top
    if plate_width < 8 or plate_height < 8:
        return None

    crop = Image.fromarray((foreground[top:bottom, left:right] * 255).astype(np.uint8), mode="L")
    scale = min(1.0, 192.0 / max(crop.size))
    sample_size = (
        max(8, int(round(crop.width * scale))),
        max(8, int(round(crop.height * scale))),
    )
    sampled = np.asarray(crop.resize(sample_size, Image.Resampling.NEAREST), dtype=np.uint8) > 0
    best_radius = 0
    best_iou = 0.0
    max_radius = max(1, min(sample_size) // 2)
    step = max(1, max_radius // 48)
    for radius in range(0, max_radius + 1, step):
        candidate = _rounded_rectangle_mask(sample_size, (0, 0, *sample_size), radius)
        union = int(np.count_nonzero(candidate | sampled))
        if union == 0:
            continue
        iou = float(np.count_nonzero(candidate & sampled) / union)
        if iou > best_iou:
            best_iou = iou
            best_radius = radius
    if best_iou < 0.72:
        return None
    radius = float(best_radius / max(scale, 1e-6))
    plate_mask = _rounded_rectangle_mask((width, height), bounds, radius)
    return bounds, radius, best_iou, _hex_colour(background), plate_mask


def build_logo_gradient_model(image: Image.Image) -> LogoGradientModel | None:
    """Recover a smooth gradient plate and isolate non-gradient foreground detail."""
    rgba = image.convert("RGBA")
    if rgba.getchannel("A").getextrema() != (255, 255):
        return None
    array = np.asarray(rgba, dtype=np.uint8)
    fitted_plate = _fit_logo_plate(array)
    if fitted_plate is None:
        return None
    bounds, radius, plate_iou, background_fill, plate_mask = fitted_plate
    left, top, right, bottom = bounds
    plate_height, plate_width = bottom - top, right - left
    ys, xs = np.nonzero(plate_mask)
    if xs.size < 256:
        return None

    nx = (xs.astype(np.float32) - left) / max(1.0, plate_width - 1.0)
    ny = (ys.astype(np.float32) - top) / max(1.0, plate_height - 1.0)
    colours = array[ys, xs, :3].astype(np.float32)
    sample_step = max(1, len(xs) // 65_536)
    fit_x = np.column_stack(
        (nx[::sample_step], ny[::sample_step], np.ones_like(nx[::sample_step]))
    )
    fit_colours = colours[::sample_step]
    inliers = np.ones(len(fit_x), dtype=bool)
    coefficients = np.zeros((3, 3), dtype=np.float32)
    for _ in range(4):
        coefficients = np.linalg.lstsq(fit_x[inliers], fit_colours[inliers], rcond=None)[0]
        prediction = fit_x @ coefficients
        residual = np.linalg.norm(fit_colours - prediction, axis=1)
        cutoff = float(np.percentile(residual, 68))
        inliers = residual <= max(6.0, cutoff)
    spatial = coefficients[:2, :]
    eigenvalues, eigenvectors = np.linalg.eigh(spatial @ spatial.T)
    direction = eigenvectors[:, int(np.argmax(eigenvalues))].astype(np.float32)
    if float(np.linalg.norm(direction)) < 1e-6:
        direction = np.array([0.0, 1.0], dtype=np.float32)
    direction /= max(float(np.linalg.norm(direction)), 1e-6)
    projection = nx * direction[0] + ny * direction[1]
    projection_min = float(np.min(projection))
    projection_max = float(np.max(projection))
    projection_range = max(1e-6, projection_max - projection_min)
    t = np.clip((projection - projection_min) / projection_range, 0.0, 1.0)

    stop_count = 9
    stop_offsets = np.linspace(0.0, 1.0, stop_count, dtype=np.float32)
    stop_colours: list[np.ndarray] = []
    half_bin = 0.65 / max(1, stop_count - 1)
    for offset in stop_offsets:
        selected = np.abs(t - offset) <= half_bin
        if not np.any(selected):
            selected = np.abs(t - offset) == np.min(np.abs(t - offset))
        values = colours[selected]
        median = np.median(values, axis=0)
        distances = np.linalg.norm(values - median, axis=1)
        robust_values = values[distances <= np.percentile(distances, 70)]
        stop_colours.append(np.mean(robust_values, axis=0) if len(robust_values) else median)
    stop_array = np.asarray(stop_colours, dtype=np.float32)

    predicted_plate = np.empty_like(colours)
    for channel in range(3):
        predicted_plate[:, channel] = np.interp(t, stop_offsets, stop_array[:, channel])
    plate_residual = np.linalg.norm(colours - predicted_plate, axis=1)
    inlier_cutoff = float(np.percentile(plate_residual, 68))
    inlier_values = plate_residual[plate_residual <= inlier_cutoff]
    inlier_rmse = float(np.sqrt(np.mean(inlier_values**2))) if len(inlier_values) else 255.0
    if inlier_rmse > 18.0:
        return None

    if background_fill is None:
        base = np.zeros_like(array)
        base[:, :, 3] = 255
    else:
        background_rgb = np.array(
            [int(background_fill[index : index + 2], 16) for index in (1, 3, 5)],
            dtype=np.uint8,
        )
        base = np.empty_like(array)
        base[:, :, :3] = background_rgb
        base[:, :, 3] = 255
    base[ys, xs, :3] = np.clip(np.rint(predicted_plate), 0, 255).astype(np.uint8)
    base[ys, xs, 3] = 255

    visible_delta = np.max(
        np.abs(array[:, :, :3].astype(np.int16) - base[:, :, :3].astype(np.int16)),
        axis=2,
    )
    foreground_threshold = int(round(max(10.0, min(22.0, inlier_rmse * 2.4))))
    foreground_mask = visible_delta > foreground_threshold
    foreground_mask = np.asarray(
        Image.fromarray((foreground_mask * 255).astype(np.uint8), mode="L").filter(
            ImageFilter.MaxFilter(3)
        ),
        dtype=np.uint8,
    ) > 0
    foreground_coverage = float(np.mean(foreground_mask))
    if foreground_coverage <= 0.002 or foreground_coverage >= 0.68:
        return None
    foreground_array = array.copy()
    foreground_array[~foreground_mask, 3] = 0

    start_normalized = direction * projection_min
    end_normalized = direction * projection_max
    gradient_start = (
        float(left + start_normalized[0] * max(1, plate_width - 1)),
        float(top + start_normalized[1] * max(1, plate_height - 1)),
    )
    gradient_end = (
        float(left + end_normalized[0] * max(1, plate_width - 1)),
        float(top + end_normalized[1] * max(1, plate_height - 1)),
    )
    stops = tuple(
        (float(offset), _hex_colour(colour))
        for offset, colour in zip(stop_offsets, stop_array)
    )
    return LogoGradientModel(
        canvas_size=rgba.size,
        background_fill=background_fill,
        plate_bounds=bounds,
        corner_radius=radius,
        gradient_start=gradient_start,
        gradient_end=gradient_end,
        stops=stops,
        foreground=Image.fromarray(foreground_array, mode="RGBA"),
        plate_iou=plate_iou,
        inlier_rmse=inlier_rmse,
        foreground_coverage=foreground_coverage,
    )


def compose_gradient_logo_svg(model: LogoGradientModel, foreground_svg: str) -> str:
    """Compose recovered primitives/gradient with a traced foreground layer."""
    width, height = model.canvas_size
    root = ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
            "data-tracer-logo-strategy": "gradient_geometry",
        },
    )
    definitions = ET.SubElement(root, f"{{{SVG_NS}}}defs")
    gradient = ET.SubElement(
        definitions,
        f"{{{SVG_NS}}}linearGradient",
        {
            "id": "logo-gradient-1",
            "gradientUnits": "userSpaceOnUse",
            "x1": f"{model.gradient_start[0]:.3f}",
            "y1": f"{model.gradient_start[1]:.3f}",
            "x2": f"{model.gradient_end[0]:.3f}",
            "y2": f"{model.gradient_end[1]:.3f}",
        },
    )
    for offset, colour in model.stops:
        ET.SubElement(
            gradient,
            f"{{{SVG_NS}}}stop",
            {"offset": f"{offset:.4f}", "stop-color": colour},
        )
    if model.background_fill is not None:
        ET.SubElement(
            root,
            f"{{{SVG_NS}}}rect",
            {
                "id": "logo-background",
                "x": "0",
                "y": "0",
                "width": str(width),
                "height": str(height),
                "fill": model.background_fill,
                "data-tracer-role": "logo-background",
            },
        )
    left, top, right, bottom = model.plate_bounds
    ET.SubElement(
        root,
        f"{{{SVG_NS}}}rect",
        {
            "id": "logo-gradient-plate",
            "x": str(left),
            "y": str(top),
            "width": str(right - left),
            "height": str(bottom - top),
            "rx": f"{model.corner_radius:.3f}",
            "ry": f"{model.corner_radius:.3f}",
            "fill": "url(#logo-gradient-1)",
            "data-tracer-role": "logo-gradient",
        },
    )

    foreground_root = ET.fromstring(foreground_svg)
    foreground_group = ET.SubElement(
        root,
        f"{{{SVG_NS}}}g",
        {"id": "logo-foreground", "data-tracer-role": "logo-foreground"},
    )
    for child in list(foreground_root):
        if _local_name(child.tag) == "defs":
            for definition in list(child):
                definitions.append(definition)
        elif _local_name(child.tag) not in _PRESERVED_TAGS:
            foreground_group.append(child)
    return ET.tostring(root, encoding="unicode")


def restore_logo_canvas(svg_content: str, crop: LogoCrop) -> str:
    """Restore a cropped trace to the exact source coordinate system."""
    if not crop.used:
        return svg_content
    root = ET.fromstring(svg_content)
    width, height = crop.canvas_size
    left, top, _, _ = crop.bounds
    root.set("width", str(width))
    root.set("height", str(height))
    root.set("viewBox", f"0 0 {width} {height}")

    preserved: list[ET.Element] = []
    renderable: list[ET.Element] = []
    for child in list(root):
        root.remove(child)
        if _local_name(child.tag) in _PRESERVED_TAGS:
            preserved.append(child)
        else:
            renderable.append(child)
    for child in preserved:
        root.append(child)
    if crop.background_fill is not None:
        root.append(
            ET.Element(
                f"{{{SVG_NS}}}rect",
                {
                    "x": "0",
                    "y": "0",
                    "width": str(width),
                    "height": str(height),
                    "fill": crop.background_fill,
                    "data-tracer-role": "logo-background",
                },
            )
        )
    group = ET.SubElement(
        root,
        f"{{{SVG_NS}}}g",
        {
            "transform": f"translate({left} {top})",
            "data-tracer-role": "logo-crop",
        },
    )
    for child in renderable:
        group.append(child)
    return ET.tostring(root, encoding="unicode")


def _path_extent(path_data: str) -> tuple[float, float] | None:
    try:
        values = [float(value) for value in _NUMBER_RE.findall(path_data)]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    xs = values[0::2]
    ys = values[1::2]
    if not xs or not ys:
        return None
    return max(xs) - min(xs), max(ys) - min(ys)


def _canvas_area(root: ET.Element) -> float:
    view_box = root.get("viewBox", "").replace(",", " ").split()
    if len(view_box) == 4:
        try:
            return max(1.0, abs(float(view_box[2]) * float(view_box[3])))
        except ValueError:
            pass
    try:
        width = float(re.sub(r"[^0-9.eE+-]", "", root.get("width", "1")) or 1)
        height = float(re.sub(r"[^0-9.eE+-]", "", root.get("height", "1")) or 1)
        return max(1.0, abs(width * height))
    except ValueError:
        return 1.0


def _assign_logo_semantics(root: ET.Element) -> None:
    existing_group = next(
        (
            child
            for child in root
            if _local_name(child.tag) == "g" and child.get("id") == "logo-artwork"
        ),
        None,
    )
    if existing_group is None:
        preserved: list[ET.Element] = []
        renderable: list[ET.Element] = []
        for child in list(root):
            root.remove(child)
            if _local_name(child.tag) in _PRESERVED_TAGS:
                preserved.append(child)
            else:
                renderable.append(child)
        for child in preserved:
            root.append(child)
        existing_group = ET.SubElement(
            root,
            f"{{{SVG_NS}}}g",
            {"id": "logo-artwork", "data-tracer-role": "logo-artwork"},
        )
        for child in renderable:
            existing_group.append(child)

    sequence = 1
    for element in existing_group.iter():
        if _local_name(element.tag) not in _SHAPE_TAGS:
            continue
        if not element.get("id"):
            element.set("id", f"logo-shape-{sequence:04d}")
        if not element.get("data-tracer-role"):
            element.set("data-tracer-role", "logo-shape")
        sequence += 1


def postprocess_logo_svg(
    svg_content: str,
    filter_tiny_paths: bool = True,
    *,
    min_path_area_fraction: float = 1e-6,
) -> str:
    """Remove geometric speckles and create a deterministic editable logo group."""
    try:
        root = ET.fromstring(svg_content)
        if filter_tiny_paths:
            minimum_area = _canvas_area(root) * max(0.0, min_path_area_fraction)
            for parent in root.iter():
                removable: list[ET.Element] = []
                for child in list(parent):
                    if _local_name(child.tag) != "path":
                        continue
                    if child.get("stroke") not in {None, "", "none"}:
                        continue
                    extent = _path_extent(child.get("d", ""))
                    if extent is not None and extent[0] * extent[1] < minimum_area:
                        removable.append(child)
                for child in removable:
                    parent.remove(child)
        _assign_logo_semantics(root)
        return ET.tostring(root, encoding="unicode")
    except (ET.ParseError, ValueError):
        return svg_content


def assess_logo_editability(
    svg_content: str,
    *,
    max_paths: int = LOGO_MAX_PATHS,
    max_path_commands: int = LOGO_MAX_PATH_COMMANDS,
    max_svg_bytes: int = LOGO_MAX_SVG_BYTES,
) -> LogoEditability:
    """Apply a strict structural contract to a pure-vector logo candidate."""
    errors: list[str] = []
    path_count = 0
    path_command_count = 0
    shape_count = 0
    image_count = 0
    anonymous_shape_count = 0
    svg_bytes = len(svg_content.encode("utf-8"))
    try:
        root = ET.fromstring(svg_content)
        for element in root.iter():
            name = _local_name(element.tag)
            if name == "path":
                path_count += 1
                path_command_count += len(_COMMAND_RE.findall(element.get("d", "")))
            if name == "image":
                image_count += 1
            if name in _SHAPE_TAGS:
                shape_count += 1
                if not element.get("id") or not element.get("data-tracer-role"):
                    anonymous_shape_count += 1
    except ET.ParseError:
        errors.append("Logo candidate is not valid XML.")

    if path_count > max_paths:
        errors.append(f"Logo path budget exceeded: {path_count:,} > {max_paths:,}.")
    if path_command_count > max_path_commands:
        errors.append(
            f"Logo path-command budget exceeded: {path_command_count:,} > {max_path_commands:,}."
        )
    if svg_bytes > max_svg_bytes:
        errors.append(f"Logo byte budget exceeded: {svg_bytes:,} > {max_svg_bytes:,}.")
    if image_count:
        errors.append(f"Pure-vector logo contains {image_count} raster image element(s).")
    if anonymous_shape_count:
        errors.append(f"Logo contains {anonymous_shape_count} anonymous shape element(s).")
    return LogoEditability(
        passed=not errors,
        errors=tuple(errors),
        path_count=path_count,
        path_command_count=path_command_count,
        shape_count=shape_count,
        image_count=image_count,
        anonymous_shape_count=anonymous_shape_count,
        svg_bytes=svg_bytes,
    )
