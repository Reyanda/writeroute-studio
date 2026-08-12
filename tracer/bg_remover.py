"""
Background removal module using rembg with zero-network local fallback algorithm.
"""

from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np


def remove_background_fallback(img: Image.Image, bg_color_tolerance: int = 25) -> Image.Image:
    """
    Fallback background removal algorithm:
    Detects border corner colors and masks homogeneous background areas with transparency.
    """
    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    h, w, _ = arr.shape

    # Sample four corner regions (10x10 px) to find primary background color
    corners = [
        arr[0:10, 0:10, :3],
        arr[0:10, w-10:w, :3],
        arr[h-10:h, 0:10, :3],
        arr[h-10:h, w-10:w, :3],
    ]
    corner_colors = np.vstack([c.reshape(-1, 3) for c in corners])
    bg_color = np.median(corner_colors, axis=0)

    # Compute Euclidean color distance from estimated bg_color
    diff = np.linalg.norm(arr[:, :, :3].astype(float) - bg_color.astype(float), axis=2)

    # Create transparency mask where distance is within tolerance
    bg_mask = diff <= bg_color_tolerance

    # Update alpha channel
    arr[:, :, 3] = np.where(bg_mask, 0, arr[:, :, 3])
    return Image.fromarray(arr, "RGBA")


def remove_background(
    input_path: str | Path,
    output_path: str | Path | None = None,
    method: str = "auto",  # 'auto', 'rembg', 'fallback'
) -> Image.Image:
    """
    Removes background from raster image.
    Uses AI rembg model if installed/available, or local fallback.
    Returns processed PIL Image object.
    """
    input_path = Path(input_path)
    img = Image.open(input_path)

    processed_img: Image.Image | None = None

    if method in ("auto", "rembg"):
        try:
            import rembg
            processed_img = rembg.remove(img)
        except BaseException as e:
            print(f"[Tracer bg_remover] rembg fallback engaged: {e}")
            processed_img = remove_background_fallback(img)
    else:
        processed_img = remove_background_fallback(img)

    if output_path and processed_img:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        processed_img.save(output_path, "PNG")

    return processed_img
