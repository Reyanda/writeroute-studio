"""Regression coverage for representation modes and the Tracer project model."""

import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tracer.config import OutputMode
from tracer.document import (
    SceneCommand,
    SceneHistory,
    annotate_svg_scene,
    create_project_archive,
    open_project_archive,
)
from tracer.parity import build_parity_result
from tracer.verifier import render_svg_to_png, validate_output


class TestParityAndDocument(unittest.TestCase):
    def setUp(self):
        self.original = Image.new("RGBA", (96, 64), "white")
        for x in range(16, 80):
            for y in range(12, 52):
                self.original.putpixel((x, y), (20, 122, 245, 255))
        self.inaccurate_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="64">'
            '<rect width="96" height="64" fill="white"/>'
            '<rect x="18" y="14" width="60" height="36" fill="#2570da"/>'
            "</svg>"
        )

    def test_exact_wrapper_is_pixel_identical(self):
        result = build_parity_result(
            self.original,
            self.inaccurate_svg,
            mode=OutputMode.EXACT_WRAPPER,
            target_quality=0.999,
        )
        self.assertTrue(result.validity.passed)
        self.assertAlmostEqual(result.metrics["quality_score"], 1.0, places=6)
        self.assertEqual(result.vector_coverage, 0.0)

    def test_hybrid_repairs_only_error_regions(self):
        pure = build_parity_result(self.original, self.inaccurate_svg, mode=OutputMode.PURE_VECTOR)
        hybrid = build_parity_result(
            self.original,
            self.inaccurate_svg,
            mode=OutputMode.HYBRID_PARITY,
            residual_threshold=2,
            residual_expansion=1,
        )
        self.assertGreater(hybrid.metrics["quality_score"], pure.metrics["quality_score"])
        self.assertGreater(hybrid.vector_coverage, 0.0)
        self.assertLess(hybrid.vector_coverage, 1.0)
        self.assertIn("tracer-residual-plane", hybrid.svg)
        self.assertNotIn("tracer-residual-cutout", hybrid.svg)
        self.assertIn('data-tracer-compositing="non_destructive_overlay"', hybrid.svg)
        self.assertEqual(
            hybrid.metadata["hybrid_repair"]["compositing"],
            "non_destructive_overlay",
        )

    def test_hiding_opaque_residual_preserves_complete_vector_base(self):
        hybrid = build_parity_result(
            self.original,
            self.inaccurate_svg,
            mode=OutputMode.HYBRID_PARITY,
            residual_threshold=2,
            residual_expansion=1,
        )
        root = ET.fromstring(hybrid.svg)
        vector_group = next(
            node for node in root.iter() if node.attrib.get("id") == "tracer-vector-base"
        )
        self.assertNotIn("mask", vector_group.attrib)
        self.assertEqual(vector_group.attrib["data-tracer-integrity"], "complete")
        for parent in root.iter():
            for child in list(parent):
                if child.attrib.get("id") == "tracer-residual-plane":
                    parent.remove(child)
        vector_only = render_svg_to_png(ET.tostring(root, encoding="unicode"), self.original.size)
        pure_render = render_svg_to_png(self.inaccurate_svg, self.original.size)
        self.assertTrue(
            np.array_equal(
                np.asarray(vector_only.convert("RGBA")),
                np.asarray(pure_render.convert("RGBA")),
            )
        )
        annotated, document = annotate_svg_scene(
            hybrid.svg,
            name="Non-destructive Hybrid",
            output_mode=OutputMode.HYBRID_PARITY.value,
        )
        residual_node = next(
            node for node in document.iter_nodes() if node.type == "ResidualPatch"
        )
        vector_node = next(
            node for node in document.iter_nodes() if node.semantic_role == "vector-base"
        )
        self.assertIn("data-tracer-id", annotated)
        self.assertEqual(
            residual_node.attributes["data-tracer-compositing"],
            "non_destructive_overlay",
        )
        self.assertEqual(vector_node.attributes["data-tracer-integrity"], "complete")

    def test_transparent_hybrid_retains_alpha_correct_replacement(self):
        transparent = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        for x in range(8, 24):
            for y in range(8, 24):
                transparent.putpixel((x, y), (220, 40, 80, 128))
        empty_vector = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" '
            'viewBox="0 0 32 32"></svg>'
        )
        hybrid = build_parity_result(
            transparent,
            empty_vector,
            mode=OutputMode.HYBRID_PARITY,
            target_quality=0.999,
            residual_threshold=0,
            residual_expansion=0,
        )
        self.assertTrue(hybrid.validity.passed)
        self.assertIn("tracer-residual-cutout", hybrid.svg)
        self.assertIn('data-tracer-compositing="alpha_cutout"', hybrid.svg)
        self.assertEqual(hybrid.metadata["hybrid_repair"]["compositing"], "alpha_cutout")
        self.assertGreaterEqual(hybrid.metrics["quality_score"], 0.999)

    def test_hybrid_tightens_residual_threshold_to_meet_quality_target(self):
        near_miss_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="64">'
            '<rect width="96" height="64" fill="white"/>'
            '<rect x="16" y="12" width="64" height="40" fill="#1a80fb"/>'
            "</svg>"
        )
        result = build_parity_result(
            self.original,
            near_miss_svg,
            mode=OutputMode.HYBRID_PARITY,
            target_quality=0.9999,
            residual_threshold=8,
            residual_expansion=1,
        )
        repair = result.metadata["hybrid_repair"]
        self.assertTrue(result.validity.passed)
        self.assertGreaterEqual(result.metrics["quality_score"], 0.9999)
        self.assertLess(repair["threshold_used"], repair["requested_threshold"])
        self.assertGreater(len(repair["attempts"]), 1)

    def test_hard_gate_detects_colour_collapse(self):
        collapsed = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="64">'
            '<rect width="96" height="64" fill="black"/></svg>'
        )
        rendered = render_svg_to_png(collapsed, self.original.size)
        validity = validate_output(collapsed, self.original, rendered, target_quality=0.95)
        self.assertFalse(validity.passed)
        self.assertTrue(any("Quality target missed" in error for error in validity.errors))

    def test_scene_graph_history_and_project_round_trip(self):
        annotated, document = annotate_svg_scene(self.inaccurate_svg, name="Parity Test")
        self.assertIn("data-tracer-id", annotated)
        path_or_shape = next(node for node in document.iter_nodes() if node.type == "Primitive")
        history = SceneHistory(document)
        history.execute(SceneCommand("set_opacity", path_or_shape.id, 0.4))
        self.assertAlmostEqual(document.find_node(path_or_shape.id).opacity, 0.4)
        self.assertAlmostEqual(history.undo().find_node(path_or_shape.id).opacity, 1.0)

        artboard = document.find_node("artboard-1")
        self.assertIsNotNone(artboard)
        if len(artboard.children) >= 2:
            first_id = artboard.children[0].id
            history.document = document
            history.execute(SceneCommand("reorder", first_id, 1))
            self.assertEqual(document.find_node("artboard-1").children[1].id, first_id)

        payload = create_project_archive(document, annotated, preview=self.original)
        opened = open_project_archive(payload)
        self.assertEqual(opened["document"].name, "Parity Test")
        self.assertEqual(opened["svg"], annotated)
        self.assertIsNotNone(opened["preview"])


if __name__ == "__main__":
    unittest.main()
