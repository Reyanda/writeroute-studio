"""Absolute Parity: exact pure-vector reconstruction and its hard gate."""

from __future__ import annotations

import re
import unittest

import numpy as np
from PIL import Image, ImageDraw

from tracer.analyzer import recommend_output_contract
from tracer.config import OutputMode
from tracer.lvc import encode_document, encode_pixels, extract_rectangles
from tracer.parity import absolute_parity_svg, build_parity_result, hybrid_parity_svg
from tracer.verifier import measure_bit_parity, render_svg_to_png, validate_output


def flat_ui_fixture(width: int = 240, height: int = 180) -> Image.Image:
    """Flat UI content: large runs, hard edges, fully opaque."""
    image = Image.new("RGBA", (width, height), (250, 250, 252, 255))
    draw = ImageDraw.Draw(image)
    for index in range(4):
        top = 16 + index * 38
        draw.rectangle([12, top, width - 12, top + 28], fill=(255, 255, 255, 255), outline=(210, 212, 220, 255))
        draw.rectangle([20, top + 8, 60, top + 20], fill=(20, 90, 200, 255))
    return image


def gradient_alpha_fixture(width: int = 96, height: int = 72) -> Image.Image:
    """Continuous colour with a full alpha ramp, including alpha 0 and 255."""
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    columns = np.arange(width)
    rows = np.arange(height)
    pixels[..., 0] = (columns * 255 // max(1, width - 1))[None, :]
    pixels[..., 1] = (rows * 255 // max(1, height - 1))[:, None]
    pixels[..., 2] = 128
    pixels[..., 3] = (columns * 255 // max(1, width - 1))[None, :]
    return Image.fromarray(pixels, mode="RGBA")


def repeated_block_fixture(size: int = 512, block: int = 16) -> Image.Image:
    """Grid-aligned repeated blocks, the case symbol mining is built for."""
    pixels = np.full((size, size, 4), 255, dtype=np.uint8)
    generator = np.random.default_rng(7)
    tile = generator.integers(0, 255, (block, block, 3), dtype=np.uint8)
    for top in range(0, size, block):
        for left in range(0, size, block):
            if ((top // block) + (left // block)) % 2 == 0:
                pixels[top : top + block, left : left + block, :3] = tile
    return Image.fromarray(pixels, mode="RGBA")


def repeated_motif_fixture() -> np.ndarray:
    """A detailed motif repeated at offsets that share no lattice."""
    generator = np.random.default_rng(5)
    motif = generator.integers(0, 255, (22, 26, 3), dtype=np.uint8)
    pixels = np.zeros((1400, 2000, 4), dtype=np.uint8)
    pixels[..., :3] = (250, 250, 252)
    pixels[..., 3] = 255
    for row in range(24):
        for column in range(13):
            top, left = 17 + row * 57, 23 + column * 151
            pixels[top : top + 22, left : left + 26, :3] = motif
    return pixels


def photo_fixture(width: int = 120, height: int = 90) -> Image.Image:
    """High-entropy content that defeats run-length encoding."""
    generator = np.random.default_rng(3)
    pixels = generator.integers(0, 255, (height, width, 4), dtype=np.uint8)
    pixels[..., 3] = 255
    return Image.fromarray(pixels, mode="RGBA")


class LosslessVectorCodecTests(unittest.TestCase):
    def assert_exact(self, image: Image.Image, svg: str) -> None:
        rendered = render_svg_to_png(svg, image.size)
        parity = measure_bit_parity(image, rendered)
        self.assertTrue(
            parity["bit_exact"],
            f"{parity['mismatched_pixels']} pixels differ; "
            f"max premultiplied delta {parity['max_premultiplied_delta']}",
        )

    def test_flat_ui_round_trips_exactly(self) -> None:
        image = flat_ui_fixture()
        svg, stats = encode_document(image)
        self.assert_exact(image, svg)
        self.assertEqual(stats.width, image.width)
        self.assertNotIn("<image", svg)

    def test_alpha_gradient_round_trips_exactly(self) -> None:
        image = gradient_alpha_fixture()
        svg, _ = encode_document(image)
        self.assert_exact(image, svg)

    def test_photographic_content_round_trips_exactly(self) -> None:
        image = photo_fixture()
        svg, _ = encode_document(image)
        self.assert_exact(image, svg)

    def test_tiling_does_not_change_the_result(self) -> None:
        image = flat_ui_fixture()
        banded, stats = encode_document(image, tile_pixels=2_000)
        self.assertGreater(stats.tiles, 1)
        self.assert_exact(image, banded)

    def test_symbol_mining_preserves_parity_and_shrinks_output(self) -> None:
        image = repeated_block_fixture()
        mined, mined_stats = encode_document(image, mine_symbols=True)
        plain, plain_stats = encode_document(image, mine_symbols=False)
        self.assertGreater(mined_stats.symbols, 0, "mining should engage on aligned repeats")
        self.assertLess(mined_stats.svg_bytes, plain_stats.svg_bytes)
        self.assert_exact(image, mined)
        self.assert_exact(image, plain)

    def test_mining_is_declined_when_it_does_not_help(self) -> None:
        # Deflate already exploits repetition, so mining must prove its value
        # rather than being applied on the assumption that it helps.
        image = flat_ui_fixture(600, 400)
        _, stats = encode_document(image, mine_symbols=True, symbol_repeats=2)
        self.assertEqual(stats.symbols, 0)

    def test_fully_transparent_pixels_are_not_painted(self) -> None:
        pixels = np.zeros((8, 8, 4), dtype=np.uint8)
        pixels[..., :3] = 200  # colour beneath zero alpha must be discarded
        image = Image.fromarray(pixels, mode="RGBA")
        svg, stats = encode_document(image)
        self.assertEqual(stats.rectangles, 0)
        self.assert_exact(image, svg)

    def test_rectangles_are_merged_vertically(self) -> None:
        pixels = np.zeros((10, 10, 4), dtype=np.uint8)
        pixels[..., :] = (10, 20, 30, 255)
        rectangles = extract_rectangles(pixels)
        self.assertEqual(len(rectangles), 1)
        self.assertEqual(int(rectangles.width[0]), 10)
        self.assertEqual(int(rectangles.height[0]), 10)

    def test_flat_regions_become_contours_not_rectangle_piles(self) -> None:
        image = flat_ui_fixture()
        svg, stats = encode_document(image)
        self.assertEqual(stats.geometry, "contour")
        # Regions become whole polygons rather than one shape per run.
        self.assertGreater(stats.rectangles, stats.contours)
        self.assertLess(len(svg), len(encode_document(image, trace_contours=False)[0]))
        self.assert_exact(image, svg)

    def test_nested_holes_render_exactly(self) -> None:
        # Contour winding must make holes work through the nonzero fill rule.
        image = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse([10, 10, 110, 110], fill=(200, 20, 60, 255))
        draw.ellipse([35, 35, 85, 85], fill=(255, 255, 255, 255))
        draw.ellipse([50, 50, 70, 70], fill=(0, 0, 255, 255))
        svg, stats = encode_document(image)
        self.assertEqual(stats.geometry, "contour")
        self.assert_exact(image, svg)

    def test_pinch_points_render_exactly(self) -> None:
        # A checkerboard is the maximal pinch case: every corner is a saddle
        # where the contour pairing is ambiguous.
        pixels = np.zeros((64, 64, 4), dtype=np.uint8)
        pixels[..., 3] = 255
        rows, columns = np.mgrid[0:64, 0:64]
        pixels[(rows + columns) % 2 == 0, :3] = 255
        image = Image.fromarray(pixels, mode="RGBA")
        svg, _ = encode_document(image)
        self.assert_exact(image, svg)

    def test_transparent_holes_inside_opaque_regions_are_exact(self) -> None:
        pixels = np.zeros((80, 80, 4), dtype=np.uint8)
        pixels[..., :3] = (20, 150, 90)
        pixels[..., 3] = 255
        pixels[20:40, 20:40, 3] = 0
        pixels[50:60, 50:60, 3] = 128
        image = Image.fromarray(pixels, mode="RGBA")
        svg, _ = encode_document(image)
        self.assert_exact(image, svg)

    def test_repeated_glyphs_are_mined_at_arbitrary_offsets(self) -> None:
        # Deliberately placed off any lattice — 57 px and 151 px pitches at a
        # 17/23 origin — so grid-aligned block mining could not find these.
        image = Image.fromarray(repeated_motif_fixture(), mode="RGBA")
        mined, mined_stats = encode_document(image, mine_components=True)
        _, plain_stats = encode_document(image, mine_components=False)
        self.assertEqual(mined_stats.geometry, "component")
        self.assertGreater(mined_stats.symbols, 0)
        self.assertGreater(mined_stats.symbol_instances, mined_stats.symbols)
        self.assertLess(mined_stats.svg_bytes, plain_stats.svg_bytes)
        self.assert_exact(image, mined)

    def test_component_mining_is_declined_when_it_does_not_help(self) -> None:
        image = flat_ui_fixture(600, 400)
        _, stats = encode_document(image, mine_components=True)
        self.assertNotEqual(stats.geometry, "component")

    def test_sparse_mask_encodes_only_selected_pixels(self) -> None:
        image = flat_ui_fixture()
        mask = np.zeros((image.height, image.width), dtype=bool)
        mask[10:20, 10:20] = True
        _, stats = encode_pixels(image, mask)
        self.assertLessEqual(stats.covered_pixels, 100)
        self.assertGreater(stats.covered_pixels, 0)


class AbsoluteParityModeTests(unittest.TestCase):
    def test_mode_is_exact_and_contains_no_raster(self) -> None:
        image = flat_ui_fixture()
        # An empty base forces the codec to carry the entire canvas.
        placeholder = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}"/>'
        )
        result = build_parity_result(
            image, placeholder, mode=OutputMode.ABSOLUTE_PARITY
        )
        self.assertTrue(result.validity.passed, result.validity.errors)
        self.assertTrue(result.validity.bit_exact)
        self.assertEqual(result.validity.image_count, 0)
        self.assertEqual(result.validity.mismatched_pixels, 0)
        self.assertNotIn("<image", result.svg)
        self.assertTrue(result.validity.parity_digest)

    def test_a_bit_exact_result_satisfies_a_quality_target_of_one(self) -> None:
        # The similarity pipeline returns 0.9999999999999999 for a perfect
        # match, so a target of 1.0 — which automatic mode recommends for this
        # very representation — would reject an output with zero differing
        # pixels. Bit parity is the stronger statement and must win.
        image = flat_ui_fixture()
        placeholder = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}"/>'
        )
        result = build_parity_result(
            image, placeholder, mode=OutputMode.ABSOLUTE_PARITY, target_quality=1.0
        )
        self.assertTrue(result.validity.bit_exact)
        self.assertTrue(result.validity.passed, result.validity.errors)

    def test_an_inexact_result_still_fails_its_quality_target(self) -> None:
        image = flat_ui_fixture()
        blank = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}">'
            '<rect width="100%" height="100%" fill="#ffffff"/></svg>'
        )
        rendered = render_svg_to_png(blank, image.size)
        validity = validate_output(blank, image, rendered, target_quality=1.0)
        self.assertFalse(validity.bit_exact)
        self.assertFalse(validity.passed)

    def test_partial_transparency_is_exact(self) -> None:
        image = gradient_alpha_fixture()
        placeholder = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}"/>'
        )
        result = build_parity_result(
            image, placeholder, mode=OutputMode.ABSOLUTE_PARITY
        )
        self.assertTrue(result.validity.bit_exact, result.validity.errors)
        self.assertEqual(result.validity.image_count, 0)

    def test_an_accurate_base_leaves_almost_nothing_to_repair(self) -> None:
        image = flat_ui_fixture()
        exact_base, _ = encode_document(image)
        _, _, report = absolute_parity_svg(image, exact_base)
        self.assertEqual(report["exact_repair_pixels"], 0)
        self.assertEqual(report["compositing"], "non_destructive_overlay")

    def test_opaque_repair_preserves_the_editable_base(self) -> None:
        image = flat_ui_fixture()
        placeholder = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}">'
            '<rect width="100%" height="100%" fill="#ffffff"/></svg>'
        )
        svg, _, report = absolute_parity_svg(image, placeholder)
        self.assertEqual(report["compositing"], "non_destructive_overlay")
        self.assertNotIn("tracer-exact-cutout", svg)
        self.assertIn('data-tracer-integrity="complete"', svg)

    def test_a_useless_base_is_dropped_when_measurably_cheaper(self) -> None:
        # A base that reproduces almost nothing costs bytes and render time for
        # very little repair saving. The engine must measure that rather than
        # keep the base on principle.
        image = flat_ui_fixture(400, 300)
        useless_base = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}">'
            '<rect width="100%" height="100%" fill="#010203"/></svg>'
        )
        svg, _, report = absolute_parity_svg(image, useless_base)
        self.assertTrue(report["base_superseded"])
        self.assertEqual(report["compositing"], "codec_only")
        self.assertNotIn("tracer-vector-base", svg)
        rendered = render_svg_to_png(svg, image.size)
        self.assertTrue(measure_bit_parity(image, rendered)["bit_exact"])

    def test_a_contributing_base_is_retained(self) -> None:
        image = flat_ui_fixture()
        good_base, _ = encode_document(image)
        svg, _, report = absolute_parity_svg(image, good_base)
        self.assertFalse(report["base_superseded"])
        self.assertIn("tracer-vector-base", svg)

    def test_corrupted_geometry_fails_only_the_parity_gate(self) -> None:
        # Dropping one colour layer leaves a structurally valid document that
        # satisfies dimensions, path and byte budgets, alpha coverage and colour
        # entropy. Only per-pixel parity detects it, which is why the gate is a
        # boolean rather than another weighted quality term.
        image = flat_ui_fixture()
        svg, _ = encode_document(image)
        corrupted = re.sub(r"<path[^>]*/>", "", svg, count=1)
        self.assertNotEqual(corrupted, svg)
        rendered = render_svg_to_png(corrupted, image.size)

        tolerant = validate_output(corrupted, image, rendered)
        self.assertTrue(
            tolerant.passed,
            "the structural gates are expected to miss this defect",
        )
        self.assertFalse(tolerant.bit_exact)

        strict = validate_output(corrupted, image, rendered, require_bit_parity=True)
        self.assertFalse(strict.passed)
        self.assertGreater(strict.mismatched_pixels, 0)
        self.assertTrue(any("Bit parity failed" in message for message in strict.errors))

    def test_parity_ignores_colour_beneath_zero_alpha(self) -> None:
        source = np.zeros((4, 4, 4), dtype=np.uint8)
        source[..., :3] = 180
        rendered = np.zeros((4, 4, 4), dtype=np.uint8)
        parity = measure_bit_parity(
            Image.fromarray(source, mode="RGBA"), Image.fromarray(rendered, mode="RGBA")
        )
        self.assertTrue(parity["bit_exact"])
        self.assertFalse(parity["bit_exact_rgba"])


class HybridDegenerationTests(unittest.TestCase):
    """Large photographic sources drove Hybrid into a failing degenerate state."""

    def test_complexity_budgets_scale_with_source_size(self) -> None:
        # Fixed ceilings rejected a 13.5 Mpx portrait at quality 0.999 purely
        # for being large. Budgets are per-megapixel with a floor.
        from tracer.verifier import validate_output

        small = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        large = Image.new("RGBA", (4000, 3000), (255, 255, 255, 255))
        placeholder = '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"/>'
        for image in (small, large):
            svg = placeholder.format(w=image.width, h=image.height)
            result = build_parity_result(image, svg, mode=OutputMode.ABSOLUTE_PARITY)
            self.assertTrue(result.validity.passed, result.validity.errors)
        del validate_output

    def test_occluded_vector_base_is_dropped_and_reported(self) -> None:
        # Noise cannot be traced, so repair covers the whole canvas and the
        # base becomes invisible weight. It must be removed, reported, and the
        # result must become exact rather than merely near-exact.
        image = photo_fixture(160, 120)
        base = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}">'
            '<rect width="100%" height="100%" fill="#808080"/></svg>'
        )
        svg, coverage = hybrid_parity_svg(image, base, threshold=4)
        self.assertEqual(coverage, 1.0)
        self.assertIn('data-tracer-base="removed"', svg)
        self.assertNotIn("tracer-vector-base", svg)
        rendered = render_svg_to_png(svg, image.size)
        self.assertTrue(measure_bit_parity(image, rendered)["bit_exact"])

    def test_a_contributing_hybrid_base_is_kept(self) -> None:
        image = flat_ui_fixture()
        base = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" viewBox="0 0 {image.width} {image.height}">'
            '<rect width="100%" height="100%" fill="#fafafc"/></svg>'
        )
        svg, coverage = hybrid_parity_svg(image, base, threshold=4)
        self.assertLess(coverage, 0.99)
        self.assertIn("tracer-vector-base", svg)
        self.assertNotIn('data-tracer-base="removed"', svg)


class RepresentationRecommendationTests(unittest.TestCase):
    def test_flat_and_ui_content_is_recommended_absolute_parity(self) -> None:
        for scene_class in ("ui_screenshot", "flat_art", "logo_art", "pixel_art"):
            with self.subTest(scene_class=scene_class):
                contract = recommend_output_contract(
                    {"scene_class": scene_class, "run_ratio": 0.03}
                )
                self.assertEqual(contract["output_mode"], "absolute_parity")
                self.assertEqual(contract["target_quality"], 1.0)
                self.assertEqual(contract["residual_threshold"], 0)

    def test_photographic_content_keeps_hybrid_parity(self) -> None:
        contract = recommend_output_contract(
            {"scene_class": "colour_art", "run_ratio": 0.9}
        )
        self.assertEqual(contract["output_mode"], "hybrid_parity")

    def test_dense_texture_is_not_pushed_into_exact_geometry(self) -> None:
        # A UI-classified screen full of photographic texture is expensive to
        # encode exactly, so the measured run ratio overrides the scene label.
        contract = recommend_output_contract(
            {"scene_class": "ui_screenshot", "run_ratio": 0.85}
        )
        self.assertEqual(contract["output_mode"], "hybrid_parity")


if __name__ == "__main__":
    unittest.main()
