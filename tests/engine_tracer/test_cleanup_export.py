"""Dead-geometry culling and multi-format export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tracer.cleanup import cull_by_contribution, cull_dead_geometry, measure_contributions
from tracer.export import export_raster, render_at
from tracer.verifier import measure_bit_parity, render_svg_to_png

CANVAS = (120, 90)


def document(body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS[0]}" '
        f'height="{CANVAS[1]}" viewBox="0 0 {CANVAS[0]} {CANVAS[1]}" '
        f'shape-rendering="crispEdges">{body}</svg>'
    )


class DeadGeometryTests(unittest.TestCase):
    def test_geometry_hidden_behind_opaque_paint_is_removed(self) -> None:
        svg = document(
            '<rect x="10" y="10" width="40" height="40" fill="#ff0000"/>'
            '<rect x="0" y="0" width="120" height="90" fill="#0000ff"/>'
        )
        before = render_svg_to_png(svg, CANVAS)
        cleaned, report = cull_dead_geometry(svg, CANVAS)
        self.assertEqual(report.removed, 1)
        self.assertNotIn("#ff0000", cleaned)
        self.assertTrue(report.bit_exact)
        self.assertLess(report.bytes_after, report.bytes_before)
        after = render_svg_to_png(cleaned, CANVAS)
        self.assertTrue(measure_bit_parity(before, after)["bit_exact"])

    def test_offscreen_geometry_is_removed(self) -> None:
        svg = document(
            '<rect x="0" y="0" width="120" height="90" fill="#123456"/>'
            '<rect x="900" y="900" width="40" height="40" fill="#abcdef"/>'
        )
        cleaned, report = cull_dead_geometry(svg, CANVAS)
        self.assertEqual(report.removed, 1)
        self.assertNotIn("#abcdef", cleaned)

    def test_visible_geometry_is_never_removed(self) -> None:
        svg = document(
            '<rect x="0" y="0" width="120" height="90" fill="#0000ff"/>'
            '<rect x="10" y="10" width="40" height="40" fill="#ff0000"/>'
        )
        cleaned, report = cull_dead_geometry(svg, CANVAS)
        self.assertEqual(report.removed, 0)
        self.assertIn("#ff0000", cleaned)

    def test_a_single_visible_pixel_survives(self) -> None:
        svg = document(
            '<rect x="0" y="0" width="120" height="90" fill="#0000ff"/>'
            '<rect x="5" y="5" width="1" height="1" fill="#00ff00"/>'
        )
        contributions = measure_contributions(svg, CANVAS)
        self.assertIn(1, contributions)
        _, report = cull_dead_geometry(svg, CANVAS)
        self.assertEqual(report.removed, 0)

    def test_definition_content_is_not_culled_on_its_own_evidence(self) -> None:
        # A gradient stop paints nothing directly but the document depends on it.
        svg = document(
            '<defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/>'
            '<stop offset="1" stop-color="#000"/></linearGradient></defs>'
            '<rect x="0" y="0" width="120" height="90" fill="url(#g)"/>'
        )
        before = render_svg_to_png(svg, CANVAS)
        cleaned, report = cull_dead_geometry(svg, CANVAS)
        self.assertTrue(report.bit_exact)
        self.assertIn("linearGradient", cleaned)
        self.assertTrue(
            measure_bit_parity(before, render_svg_to_png(cleaned, CANVAS))["bit_exact"]
        )

    def test_contribution_cull_reverts_when_it_costs_too_much(self) -> None:
        pixels = np.random.default_rng(4).integers(0, 255, (90, 120, 4), dtype=np.uint8)
        pixels[..., 3] = 255
        original = Image.fromarray(pixels, mode="RGBA")
        body = "".join(
            f'<rect x="{x}" y="{y}" width="1" height="1" fill="#204080"/>'
            for y in range(0, 90, 3)
            for x in range(0, 120, 3)
        )
        svg = document(f'<rect width="120" height="90" fill="#ffffff"/>{body}')
        _, report = cull_by_contribution(svg, original, threshold=8, max_quality_loss=0.0)
        self.assertEqual(report.removed, 0)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svg = document('<rect x="0" y="0" width="120" height="90" fill="#2050c0"/>')
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_vector_is_rerendered_not_resampled(self) -> None:
        large = render_at(self.svg, CANVAS, scale=4)
        self.assertEqual(large.size, (480, 360))
        # A re-render of a flat fill has exactly one colour; a resample of a
        # small raster would introduce interpolated edge values.
        colours = {tuple(p) for p in np.asarray(large).reshape(-1, 4)}
        self.assertEqual(len(colours), 1)

    def test_lossless_formats_round_trip_exactly(self) -> None:
        reference = render_svg_to_png(self.svg, CANVAS)
        for image_format in ("png", "tiff", "webp"):
            result = export_raster(
                self.svg, self.root / "out", CANVAS, image_format=image_format
            )
            self.assertTrue(result.lossless, image_format)
            written = Image.open(result.path).convert("RGBA")
            self.assertTrue(
                measure_bit_parity(reference, written)["bit_exact"], image_format
            )

    def test_jpeg_has_no_alpha_and_is_flattened(self) -> None:
        transparent = document('<rect x="10" y="10" width="20" height="20" fill="#ff0000"/>')
        result = export_raster(
            transparent, self.root / "flat", CANVAS, image_format="jpeg", background="#00ff00"
        )
        self.assertTrue(result.flattened)
        self.assertFalse(result.lossless)
        written = Image.open(result.path)
        self.assertEqual(written.mode, "RGB")
        self.assertEqual(written.getpixel((0, 0))[1], 255)  # background showed through

    def test_dimensions_follow_width_height_and_scale(self) -> None:
        self.assertEqual(render_at(self.svg, CANVAS, width=240).size, (240, 180))
        self.assertEqual(render_at(self.svg, CANVAS, height=45).size, (60, 45))
        self.assertEqual(render_at(self.svg, CANVAS, scale=0.5).size, (60, 45))

    def test_absurd_export_sizes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            render_at(self.svg, CANVAS, scale=10_000)

    def test_unknown_format_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            export_raster(self.svg, self.root / "x", CANVAS, image_format="gif")

    def test_suffix_is_corrected_to_match_the_format(self) -> None:
        result = export_raster(self.svg, self.root / "name.png", CANVAS, image_format="jpeg")
        self.assertEqual(result.path.suffix, ".jpg")


if __name__ == "__main__":
    unittest.main()
