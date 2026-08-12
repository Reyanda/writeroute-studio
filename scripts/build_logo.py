#!/usr/bin/env python3
"""Derive usable logo assets from the supplied raster.

The source is a 1254x1254 RGB PNG with the background baked in, the wordmark and the
tagline all in one image. That is fine for a slide and wrong for a web header: it
cannot sit on a dark background, and the tagline cannot be sized independently of the
mark. So we cut it into parts and add an alpha channel.

The background is knocked out by connectivity, not by a luminance threshold. A plain
threshold looked correct and was not: the design's pale lavender ribbon sits within a
few values of the off-white paper, so keying on brightness alone ate it, and the mark
came out with a dull grey band where the highlight should be. What actually separates
paper from ribbon is that the paper touches the border and the ribbon does not. So the
background is the light region reachable from the edge, and anything enclosed by the
mark stays opaque however pale it is. Edge pixels keep a luminance ramp so the
anti-aliasing survives instead of leaving a hard fringe.

This is a derivation, not a redraw. A vector original would be better than any of it,
and that is recorded in the README as an open item.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

# Fractions of the source height. Measured from the supplied image: the mark occupies
# the upper half, the wordmark the band below it, and the tagline plus the three
# sub-marks the remainder.
MARK_BAND = (0.10, 0.58)
WORDMARK_BAND = (0.57, 0.75)
LOCKUP_BAND = (0.10, 0.78)

PAPER = 232          # luminance at or above which a pixel may be background
INK = 170            # luminance at or below which a pixel is certainly opaque


def knockout(image: Image.Image) -> Image.Image:
    """Replace the baked background with alpha, keeping anti-aliased edges."""
    rgb = image.convert("RGB")
    grey = rgb.convert("L")

    # Candidate paper: light enough to be background anywhere in the image.
    candidate = grey.point(lambda v: 255 if v >= PAPER else 0).convert("L")

    # Keep only the candidate region connected to the border. Flooding from all four
    # corners is done by PIL in C; a Python traversal over 1.5 million pixels is not
    # worth writing. 128 is a marker value the source cannot already contain, because
    # `candidate` is strictly binary.
    flood = candidate.copy()
    drawable = ImageDraw.floodfill
    for seed in ((0, 0), (flood.width - 1, 0), (0, flood.height - 1),
                 (flood.width - 1, flood.height - 1)):
        if flood.getpixel(seed) == 255:
            drawable(flood, seed, 128)
    background = np.asarray(flood) == 128

    # Inside the background region, fade with luminance so edges stay soft. Everywhere
    # else is fully opaque, which is what saves the enclosed pale ribbon.
    lum = np.asarray(grey).astype(np.float32)
    span = float(max(1, PAPER - INK))
    ramp = np.clip((PAPER - lum) / span, 0.0, 1.0) * 255.0
    alpha = np.where(background, ramp, 255.0).astype(np.uint8)

    out = rgb.convert("RGBA")
    out.putalpha(Image.fromarray(alpha, mode="L"))
    return out


def crop_band(image: Image.Image, band: tuple[float, float]) -> Image.Image:
    top = int(image.height * band[0])
    bottom = int(image.height * band[1])
    return image.crop((0, top, image.width, bottom))


def trim(image: Image.Image, padding: int = 12) -> Image.Image:
    box = image.getbbox()
    if not box:
        return image
    left, top, right, bottom = box
    return image.crop((
        max(0, left - padding), max(0, top - padding),
        min(image.width, right + padding), min(image.height, bottom + padding),
    ))


def save(image: Image.Image, name: str, width: int | None = None) -> None:
    out = image
    if width and out.width != width:
        height = round(out.height * width / out.width)
        out = out.resize((width, height), Image.LANCZOS)
    path = ASSETS / name
    out.save(path, optimize=True)
    print(f"{name:22s} {out.width}x{out.height}  {path.stat().st_size:,} bytes")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=str(ASSETS / "logo-source.png"))
    args = ap.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"error: {source_path} not found")
        return 2
    source = Image.open(source_path)
    transparent = knockout(source)

    save(trim(crop_band(transparent, MARK_BAND)), "logo-mark.png", width=512)
    save(trim(crop_band(transparent, WORDMARK_BAND)), "logo-wordmark.png", width=720)
    save(trim(crop_band(transparent, LOCKUP_BAND)), "logo.png", width=960)

    # Favicon: the mark on its own, square, so it survives being 16 px wide.
    mark = trim(crop_band(transparent, MARK_BAND), padding=4)
    side = max(mark.width, mark.height)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2), mark)
    save(square, "favicon.png", width=256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
