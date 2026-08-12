"""Integration tests for the local Tracer Studio service."""

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tracer.api import app


class TestStudioApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
            '<rect width="80" height="40" fill="#0a84ff"/></svg>'
        )

    def test_health_and_safe_svg_inspection(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn("hybrid_parity", health.json()["output_modes"])

        response = self.client.post("/api/svg/inspect", json={"svg": self.svg, "name": "API SVG"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("data-tracer-id", data["svg"])
        self.assertEqual(data["document"]["width"], 80)

    def test_svg_inspection_rejects_active_content(self):
        unsafe = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        response = self.client.post("/api/svg/inspect", json={"svg": unsafe})
        self.assertEqual(response.status_code, 422)

    def test_exact_conversion_and_project_round_trip(self):
        sample = PROJECT_ROOT / "samples" / "sample_logo.png"
        with sample.open("rb") as handle:
            converted = self.client.post(
                "/api/convert",
                files={"file": (sample.name, handle, "image/png")},
                data={"output_mode": "exact_wrapper", "target_quality": "0.999"},
            )
        self.assertEqual(converted.status_code, 200)
        conversion = converted.json()
        self.assertAlmostEqual(conversion["report"]["metrics"]["quality_score"], 1.0, places=6)

        saved = self.client.post(
            "/api/project/save",
            json={
                "name": "API Project",
                "svg": conversion["svg"],
                "document": conversion["document"],
                "preview_data_url": conversion["preview_png"],
            },
        )
        self.assertEqual(saved.status_code, 200)
        opened = self.client.post(
            "/api/project/open",
            files={"file": ("api-project.tracer", saved.content, "application/x-tracer-project")},
        )
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.json()["document"]["name"], "API Project")

    def test_analysis_includes_representation_contract(self):
        sample = PROJECT_ROOT / "samples" / "sample_logo.png"
        with sample.open("rb") as handle:
            response = self.client.post(
                "/api/analyze",
                files={"file": (sample.name, handle, "image/png")},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["preset"], "logo")
        # Flat, strongly run-compressible artwork now reaches exact pixel
        # parity as pure vector geometry, so it is recommended over an
        # approximation. Pure Vector remains available as an explicit choice.
        self.assertEqual(data["representation"]["output_mode"], "absolute_parity")
        self.assertEqual(data["representation"]["target_quality"], 1.0)
        self.assertEqual(data["representation"]["residual_threshold"], 0)


if __name__ == "__main__":
    unittest.main()
