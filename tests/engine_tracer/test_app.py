"""Smoke tests for the live Preview / Code result contract."""

import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import app


class TestStudioResult(unittest.TestCase):
    def test_result_contains_live_preview_code_and_both_exports(self):
        output = app.run_conversion_studio(
            app.SAMPLE_LOGO_PATH,
            "precision_ultra",
            "Automatic",
            False,
            "auto",
            False,
            0.90,
            "Balanced",
            2,
            "safe",
            8,
            1,
            4,
            30,
            3.0,
            30,
            25,
            4,
            "spline",
            "stacked",
            3000,
        )
        self.assertEqual(len(output), 13)
        self.assertIn("data:image/svg+xml;base64,", output[2])
        self.assertEqual(output[2], output[3])
        self.assertIn("<svg", output[7])
        self.assertNotEqual(output[5].getextrema()[0], (255, 255))
        svg_path, png_path = Path(output[9]), Path(output[10])
        self.assertTrue(svg_path.exists())
        self.assertTrue(png_path.exists())
        self.assertEqual(svg_path, Path(output[11]))
        self.assertEqual(png_path, Path(output[12]))
        shutil.rmtree(svg_path.parent)

    def test_pasted_svg_renders_in_code_and_preview_modes(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
            '<rect width="80" height="40" fill="#43d6b5"/></svg>'
        )
        output = app.render_pasted_svg(svg)
        self.assertEqual(len(output), 13)
        self.assertIn("Code valid", output[0])
        self.assertIn("data:image/svg+xml;base64,", output[2])
        self.assertEqual(output[2], output[3])
        self.assertEqual(output[5].size, (80, 40))
        self.assertIn("<svg", output[7])
        svg_path = Path(output[9])
        self.assertTrue(svg_path.exists())
        self.assertTrue(Path(output[10]).exists())
        shutil.rmtree(svg_path.parent)

    def test_pasted_svg_rejects_active_content(self):
        unsafe_svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        output = app.render_pasted_svg(unsafe_svg)
        self.assertIn("was not rendered", output[0])
        self.assertIsNone(output[5])
        self.assertIn('"validated": false', output[8])


if __name__ == "__main__":
    unittest.main()
