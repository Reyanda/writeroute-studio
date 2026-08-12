"""Regression tests for SVG rendering and quality telemetry."""

import sys
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tracer.verifier import calculate_quality_metrics, create_difference_map, render_svg_to_png


class TestVerifier(unittest.TestCase):
    SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
        '<rect width="64" height="64" fill="#6d5dfc"/></svg>'
    )

    def test_resvg_renderer_returns_artwork_not_placeholder(self):
        rendered = render_svg_to_png(self.SVG, (64, 64))
        self.assertEqual(rendered.size, (64, 64))
        self.assertEqual(rendered.getpixel((32, 32)), (109, 93, 252, 255))

    def test_exact_render_scores_as_exact(self):
        rendered = render_svg_to_png(self.SVG, (64, 64))
        original = Image.new("RGBA", (64, 64), (109, 93, 252, 255))
        metrics = calculate_quality_metrics(original, rendered)
        self.assertAlmostEqual(metrics["quality_score"], 1.0, places=6)
        self.assertAlmostEqual(metrics["edge_similarity"], 1.0, places=6)
        self.assertAlmostEqual(metrics["color_similarity"], 1.0, places=6)

    def test_difference_map_highlights_changed_pixels(self):
        original = Image.new("RGBA", (16, 16), "white")
        rendered = Image.new("RGBA", (16, 16), "black")
        difference = create_difference_map(original, rendered)
        self.assertGreater(difference.getpixel((8, 8))[0], 240)


if __name__ == "__main__":
    unittest.main()
