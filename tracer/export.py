"""Raster export from vector sources at arbitrary resolution.

A vector document has no native resolution, so output is *rendered* at the
requested size rather than resampled from a fixed raster. Asking for 4× does not
interpolate a 1× render; it rasterises the geometry again at 4×, which is why
vector output is worth having in the first place.

Format choice is not cosmetic and the differences are enforced here rather than
left to the caller:

* **PNG** — lossless, alpha preserved. The default.
* **TIFF** — lossless, alpha preserved, optional Deflate/LZW. Print and archival.
* **WebP** — lossless or lossy, alpha preserved. Smallest lossless in practice.
* **AVIF** — lossy or lossless, alpha preserved, best compression, slowest.
* **JPEG** — lossy and **has no alpha channel**. Transparency must be flattened
  onto a background, so a background colour is required rather than assumed.

Supersampling is offered separately from scale: rendering above the target and
downsampling produces smoother antialiasing for print, which is the opposite of
what the exact modes want, so it is opt-in.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .verifier import render_svg_to_png

#: Formats and whether each keeps an alpha channel.
FORMATS: dict[str, dict[str, Any]] = {
    "png": {"pillow": "PNG", "alpha": True, "lossy": False, "suffix": ".png"},
    "tiff": {"pillow": "TIFF", "alpha": True, "lossy": False, "suffix": ".tiff"},
    "webp": {"pillow": "WEBP", "alpha": True, "lossy": True, "suffix": ".webp"},
    "avif": {"pillow": "AVIF", "alpha": True, "lossy": True, "suffix": ".avif"},
    "jpeg": {"pillow": "JPEG", "alpha": False, "lossy": True, "suffix": ".jpg"},
    "bmp": {"pillow": "BMP", "alpha": False, "lossy": False, "suffix": ".bmp"},
}

MAX_EXPORT_PIXELS = 400_000_000


@dataclass
class ExportResult:
    path: Path
    format: str
    width: int
    height: int
    bytes_written: int
    lossless: bool
    flattened: bool
    supersample: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes_written,
            "lossless": self.lossless,
            "flattened": self.flattened,
            "supersample": self.supersample,
        }


def _target_size(
    base: tuple[int, int],
    scale: float | None,
    width: int | None,
    height: int | None,
) -> tuple[int, int]:
    source_width, source_height = base
    if width and height:
        return max(1, int(width)), max(1, int(height))
    if width:
        return max(1, int(width)), max(1, round(source_height * width / source_width))
    if height:
        return max(1, round(source_width * height / source_height)), max(1, int(height))
    factor = float(scale or 1.0)
    if factor <= 0:
        raise ValueError("Scale must be positive.")
    return max(1, round(source_width * factor)), max(1, round(source_height * factor))


def _flatten(image: Image.Image, background: str) -> Image.Image:
    backdrop = Image.new("RGBA", image.size, background)
    return Image.alpha_composite(backdrop, image.convert("RGBA")).convert("RGB")


def render_at(
    svg: str,
    base_size: tuple[int, int],
    *,
    scale: float | None = None,
    width: int | None = None,
    height: int | None = None,
    supersample: int = 1,
) -> Image.Image:
    """Rasterise the document at a requested size, optionally supersampled."""
    target = _target_size(base_size, scale, width, height)
    if target[0] * target[1] > MAX_EXPORT_PIXELS:
        raise ValueError(
            f"Requested {target[0]}×{target[1]} exceeds the export budget of "
            f"{MAX_EXPORT_PIXELS:,} pixels."
        )
    factor = max(1, int(supersample))
    if factor > 1:
        oversized = (target[0] * factor, target[1] * factor)
        if oversized[0] * oversized[1] > MAX_EXPORT_PIXELS:
            factor = 1
        else:
            rendered = render_svg_to_png(svg, oversized)
            return rendered.resize(target, Image.Resampling.LANCZOS)
    return render_svg_to_png(svg, target)


def export_raster(
    svg: str,
    destination: str | Path,
    base_size: tuple[int, int],
    *,
    image_format: str = "png",
    scale: float | None = None,
    width: int | None = None,
    height: int | None = None,
    supersample: int = 1,
    quality: int = 92,
    lossless: bool | None = None,
    background: str = "#ffffff",
    dpi: int | None = None,
) -> ExportResult:
    """Render and write one raster file, enforcing each format's real limits."""
    key = image_format.lower().lstrip(".")
    if key == "jpg":
        key = "jpeg"
    if key == "tif":
        key = "tiff"
    if key not in FORMATS:
        raise ValueError(
            f"Unsupported export format {image_format!r}. "
            f"Choose from: {', '.join(sorted(FORMATS))}."
        )
    spec = FORMATS[key]
    path = Path(destination)
    if path.suffix.lower() not in {spec["suffix"], f".{key}"}:
        path = path.with_suffix(spec["suffix"])

    image = render_at(
        svg, base_size, scale=scale, width=width, height=height, supersample=supersample
    )
    flattened = False
    if not spec["alpha"]:
        image = _flatten(image, background)
        flattened = True

    options: dict[str, Any] = {}
    if key == "jpeg":
        options.update(quality=int(quality), optimize=True, subsampling=0)
    elif key == "webp":
        use_lossless = True if lossless is None else bool(lossless)
        options.update(lossless=use_lossless, quality=int(quality), method=6)
    elif key == "avif":
        use_lossless = False if lossless is None else bool(lossless)
        options.update(quality=100 if use_lossless else int(quality))
    elif key == "tiff":
        options.update(compression="tiff_deflate")
    elif key == "png":
        options.update(optimize=True)
    if dpi:
        options["dpi"] = (int(dpi), int(dpi))

    buffer = io.BytesIO()
    image.save(buffer, format=spec["pillow"], **options)
    payload = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

    is_lossless = not spec["lossy"] or bool(options.get("lossless"))
    return ExportResult(
        path=path,
        format=key,
        width=image.width,
        height=image.height,
        bytes_written=len(payload),
        lossless=is_lossless,
        flattened=flattened,
        supersample=max(1, int(supersample)),
    )
