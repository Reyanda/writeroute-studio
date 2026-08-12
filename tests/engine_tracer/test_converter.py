"""
Automated Unit Tests for Tracer PNG -> SVG Converter engine.
"""

import sys
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tracer.config import PRESETS, TracingConfig
from tracer.analyzer import analyze_image, recommend_output_contract, recommend_preset
from tracer.converter import convert, TracerConverter
from tracer.optimizer import optimize_svg
from tracer.logo_postproc import (
    assess_logo_editability,
    build_logo_gradient_model,
    compose_gradient_logo_svg,
    detect_logo_crop,
    postprocess_logo_svg,
    restore_logo_canvas,
)


class TestTracerEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(__file__).parent / "tmp_test"
        self.test_dir.mkdir(parents=True, exist_ok=True)

        # Generate a synthetic test PNG image
        self.test_img_path = self.test_dir / "sample_logo.png"
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        d = ImageDraw.Draw(img)
        d.ellipse([20, 20, 180, 180], fill=(220, 20, 60, 255))
        d.rectangle([60, 60, 140, 140], fill=(30, 144, 255, 255))
        img.save(self.test_img_path)

    def tearDown(self):
        for f in self.test_dir.glob("*"):
            f.unlink()
        self.test_dir.rmdir()

    def test_presets_exist(self):
        self.assertIn("precision_ultra", PRESETS)
        self.assertIn("logo", PRESETS)
        self.assertIn("high_fidelity", PRESETS)
        self.assertIn("poster", PRESETS)
        self.assertIn("lineart", PRESETS)
        self.assertIn("pixel", PRESETS)
        self.assertIn("complex_map_ui", PRESETS)

    def test_analyzer(self):
        stats = analyze_image(self.test_img_path)
        self.assertEqual(stats["width"], 200)
        self.assertFalse(stats["is_monochrome"])
        self.assertEqual(stats["height"], 200)
        preset, _, overrides = recommend_preset(self.test_img_path)
        self.assertEqual(preset, "logo")

    def test_low_saturation_ui_screenshot_never_routes_to_lineart(self):
        ui_path = self.test_dir / "low_saturation_ui.png"
        image = Image.new("RGB", (1200, 800), "white")
        gradient = Image.linear_gradient("L").resize((1200, 150)).convert("RGB")
        image.paste(gradient, (0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (60, 180, 1140, 740),
            radius=20,
            fill=(250, 250, 252),
            outline=(80, 80, 88),
            width=2,
        )
        draw.rectangle((80, 210, 360, 700), fill="white", outline=(180, 180, 186), width=2)
        draw.rectangle((390, 210, 1110, 700), fill=(248, 250, 252), outline=(180, 180, 186), width=2)
        for x in range(420, 1100, 45):
            draw.line((x, 230, x, 680), fill=(205, 215, 225), width=2)
        for y in range(240, 690, 35):
            draw.line((400, y, 1100, y), fill=(205, 215, 225), width=2)
        for index in range(28):
            y = 230 + index * 15
            shade = 60 + (index % 4) * 20
            draw.rectangle((100, y, 310, y + 4), fill=(shade, shade, shade + 5))
        for x, y, colour in (
            (540, 330, (0, 122, 255)),
            (780, 470, (255, 59, 48)),
            (940, 300, (52, 199, 89)),
        ):
            draw.ellipse((x - 12, y - 12, x + 12, y + 12), fill=colour)
        image.save(ui_path)

        preset, stats, overrides = recommend_preset(ui_path)

        self.assertLess(stats["avg_saturation"], 0.08)
        self.assertFalse(stats["is_monochrome"])
        self.assertTrue(stats["is_ui_screenshot"])
        self.assertEqual(stats["scene_class"], "ui_screenshot")
        self.assertEqual(preset, "complex_map_ui")
        self.assertEqual(overrides["color_precision"], 8)
        self.assertEqual(overrides["filter_speckle"], 2)

    def test_true_grayscale_art_still_routes_to_lineart(self):
        lineart_path = self.test_dir / "lineart.png"
        image = Image.new("RGB", (600, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 80, 520, 320), outline="black", width=12)
        draw.line((100, 300, 500, 100), fill="black", width=8)
        image.save(lineart_path)

        preset, stats, _ = recommend_preset(lineart_path)

        self.assertTrue(stats["is_monochrome"])
        self.assertEqual(preset, "lineart")

    def test_gradient_logo_routes_to_logo_but_photo_texture_does_not(self):
        logo_path = self.test_dir / "gradient_logo.png"
        size = 320
        y = np.linspace(0.0, 1.0, size, dtype=np.float32)[:, None]
        rgb = np.empty((size, size, 3), dtype=np.uint8)
        rgb[:, :, 0] = np.clip(255 - 15 * y, 0, 255)
        rgb[:, :, 1] = np.clip(190 - 125 * y, 0, 255)
        rgb[:, :, 2] = np.clip(55 - 45 * y, 0, 255)
        logo = Image.fromarray(rgb, mode="RGB")
        draw = ImageDraw.Draw(logo)
        draw.ellipse((70, 70, 250, 250), outline="white", width=18)
        draw.rectangle((145, 45, 175, 275), fill="white")
        logo.save(logo_path)

        preset, stats, _ = recommend_preset(logo_path)
        self.assertTrue(stats["is_logo_art"])
        self.assertEqual(stats["scene_class"], "logo_art")
        self.assertLessEqual(stats["palette_rmse_16"], 12.0)
        self.assertEqual(preset, "logo")

        converter = TracerConverter()
        svg_code, _, metadata = converter.convert_image(
            logo_path,
            preset="logo",
            auto_preset=False,
            quality_profile="balanced",
        )
        strategy = metadata["logo_strategy"]
        gradient_candidates = [
            candidate
            for candidate in strategy["candidate_history"]
            if candidate["variant"] == "gradient_geometry"
        ]
        self.assertEqual(len(gradient_candidates), 1)
        self.assertFalse(gradient_candidates[0]["rejected"])
        self.assertIsNotNone(gradient_candidates[0]["model"])

        model = build_logo_gradient_model(logo)
        self.assertIsNotNone(model)
        reconstructed = compose_gradient_logo_svg(
            model,
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320">'
            '<path d="M145 45H175V275H145Z" fill="#fff"/></svg>',
        )
        self.assertIn("<linearGradient", reconstructed)
        self.assertIn('id="logo-gradient-plate"', reconstructed)
        self.assertNotIn("<image", reconstructed)
        self.assertNotIn("<image", svg_code)

        photo_path = self.test_dir / "photo_texture.png"
        random = np.random.default_rng(42).integers(0, 256, (320, 320, 3), dtype=np.uint8)
        Image.fromarray(random, mode="RGB").save(photo_path)
        photo_preset, photo_stats, _ = recommend_preset(photo_path)
        self.assertFalse(photo_stats["is_logo_art"])
        self.assertNotEqual(photo_preset, "logo")

    def test_dark_dense_workspace_routes_to_hybrid_ui(self):
        ui_path = self.test_dir / "dark_workspace.png"
        image = Image.new("RGB", (1200, 800), (9, 23, 35))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1199, 62), fill=(6, 18, 29))
        draw.rectangle((0, 62, 170, 799), fill=(12, 31, 47))
        draw.rectangle((980, 62, 1199, 799), fill=(12, 31, 47))
        for y_pos in range(90, 760, 28):
            draw.line((190, y_pos, 960, y_pos), fill=(51, 74, 91), width=2)
        for x_pos in range(210, 950, 44):
            draw.line((x_pos, 110, x_pos, 690), fill=(38, 61, 78), width=1)
        for index in range(28):
            y_pos = 90 + index * 23
            draw.rectangle((20, y_pos, 145, y_pos + 6), fill=(185, 205, 218))
        for index in range(12):
            y_pos = 115 + index * 42
            draw.rectangle((1010, y_pos, 1165, y_pos + 8), fill=(100, 132, 152))
        image.save(ui_path)

        preset, stats, _ = recommend_preset(ui_path)
        representation = recommend_output_contract(stats)

        self.assertGreater(stats["near_dark_ratio"], 0.35)
        self.assertTrue(stats["is_ui_screenshot"])
        self.assertFalse(stats["is_logo_art"])
        self.assertEqual(preset, "complex_map_ui")
        # A dark dense workspace is flat, high-contrast content: it measures
        # around 0.02 horizontal runs per pixel, so exact vector geometry is
        # both pixel-faithful and cheaper than the source PNG. Photographic
        # screens stay on Hybrid Parity, which the run-ratio test below pins.
        self.assertLessEqual(stats["run_ratio"], 0.12)
        self.assertEqual(representation["output_mode"], "absolute_parity")
        self.assertEqual(representation["target_quality"], 1.0)
        self.assertEqual(representation["residual_threshold"], 0)

    def test_photographic_screen_keeps_hybrid_parity(self):
        photo_path = self.test_dir / "photographic.png"
        generator = np.random.default_rng(11)
        pixels = generator.integers(0, 255, (400, 600, 3), dtype=np.uint8)
        Image.fromarray(pixels, mode="RGB").save(photo_path)

        _, stats, _ = recommend_preset(photo_path)
        representation = recommend_output_contract(stats)

        self.assertGreater(stats["run_ratio"], 0.12)
        self.assertEqual(representation["output_mode"], "hybrid_parity")

    def test_logo_cleanup_uses_geometry_and_assigns_semantics(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" '
            'viewBox="0 0 100 100">'
            '<path d="M0 0H100V100H0Z" fill="#f60"/>'
            '<path d="M1 1L1.01 1L1.01 1.01L1 1.01Z" fill="#000"/>'
            '</svg>'
        )
        processed = postprocess_logo_svg(svg)
        root = ET.fromstring(processed)
        paths = [element for element in root.iter() if element.tag.endswith("path")]

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].get("d"), "M0 0H100V100H0Z")
        self.assertEqual(paths[0].get("id"), "logo-shape-0001")
        self.assertEqual(paths[0].get("data-tracer-role"), "logo-shape")
        self.assertIn('id="logo-artwork"', processed)
        self.assertTrue(assess_logo_editability(processed).passed)

    def test_logo_crop_restores_exact_source_canvas(self):
        image = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((90, 50, 150, 110), fill=(20, 120, 240, 255))
        crop = detect_logo_crop(image)
        self.assertTrue(crop.used)
        cropped_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" '
            'viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#1478f0"/></svg>'
        )
        restored = restore_logo_canvas(cropped_svg, crop)
        root = ET.fromstring(restored)
        self.assertEqual(root.get("width"), "240")
        self.assertEqual(root.get("height"), "160")
        self.assertEqual(root.get("viewBox"), "0 0 240 160")
        self.assertIn("translate(", restored)

    def test_path_soup_fails_logo_editability_contract(self):
        paths = "".join(
            f'<path id="p{index}" data-tracer-role="logo-shape" d="M0 0L1 0L1 1Z"/>'
            for index in range(513)
        )
        assessment = assess_logo_editability(
            f'<svg xmlns="http://www.w3.org/2000/svg">{paths}</svg>'
        )
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.path_count, 513)
        self.assertTrue(any("path budget exceeded" in error.lower() for error in assessment.errors))

    def test_conversion(self):
        out_svg = self.test_dir / "result.svg"
        svg_code = convert(self.test_img_path, output_path=out_svg, preset="logo")
        self.assertTrue(out_svg.exists())
        self.assertIn("<svg", svg_code)
        self.assertIn("</svg>", svg_code)

    def test_logo_conversion_selects_an_editable_candidate_frontier(self):
        converter = TracerConverter()
        svg_code, _, metadata = converter.convert_image(
            self.test_img_path,
            preset="logo",
            auto_preset=False,
            quality_profile="balanced",
        )
        strategy = metadata["logo_strategy"]
        editability = strategy["editability"]

        self.assertEqual(strategy["strategy"], "quality_editability_frontier")
        self.assertGreaterEqual(len(strategy["candidate_history"]), 4)
        self.assertTrue(editability["passed"])
        self.assertLessEqual(editability["path_count"], 512)
        self.assertEqual(editability["image_count"], 0)
        self.assertEqual(editability["anonymous_shape_count"], 0)
        self.assertEqual(metadata["validity"]["validity_profile"], "editable_logo")
        self.assertNotIn("<image", svg_code)

    def test_explicit_optimization_values_reach_converter(self):
        converter = TracerConverter()
        _, _, metadata = converter.convert_image(
            self.test_img_path,
            preset="complex_map_ui",
            auto_preset=False,
            color_precision=7,
            filter_speckle=9,
            layer_difference=11,
            max_dim=512,
        )

        self.assertEqual(metadata["preset_used"], "complex_map_ui")
        self.assertEqual(metadata["final_config"]["color_precision"], 7)
        self.assertEqual(metadata["final_config"]["filter_speckle"], 9)
        self.assertEqual(metadata["final_config"]["layer_difference"], 11)

    def test_optimizer(self):
        raw_svg = '<svg><path d="M 10.123456 20.987654 L 30.111111 40.222222 Z"/></svg>'
        opt_svg = optimize_svg(raw_svg, precision=2)
        self.assertIn("10.12", opt_svg)
        self.assertIn("20.99", opt_svg)


if __name__ == "__main__":
    unittest.main()
