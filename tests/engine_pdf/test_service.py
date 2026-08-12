"""The PDF Studio HTTP surface, mounted under /pdf.

The frontend shipped in the repository for a release without being reachable: the project
it came from served it from its own `server.py`, and nothing here did. These tests pin the
wiring so it cannot go inert again.
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import fitz
from fastapi.testclient import TestClient


def synthetic_form() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Account Opening Form", fontsize=16)
    page.insert_text((72, 140), "Full name:", fontsize=10)
    page.draw_line(fitz.Point(140, 142), fitz.Point(400, 142))
    for index in range(6):
        left = 160 + index * 22
        page.draw_rect(fitz.Rect(left, 158, left + 20, 176))
    data = doc.tobytes()
    doc.close()
    return data


class PdfStudioService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["WRITEROUTE_DATA_DIR"] = cls.tmp.name
        import importlib

        # pdfservice reads the data directory at import time, so reloading only `app`
        # left an earlier import's paths in place and uploads went to the real home
        # directory. Reload the service first, then the app that mounts it.
        import pdfservice
        importlib.reload(pdfservice)
        import app as app_module
        importlib.reload(app_module)
        cls.client = TestClient(app_module.app)
        cls.pdf = synthetic_form()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("WRITEROUTE_DATA_DIR", None)
        cls.tmp.cleanup()

    def test_health_reports_which_engines_are_present(self):
        payload = self.client.get("/api/health").json()
        self.assertIn("engines", payload)
        self.assertTrue(payload["engines"]["prose"])
        self.assertTrue(payload["pdfStudio"], "the PDF sub-app should be mounted here")

    def test_the_editor_is_served(self):
        response = self.client.get("/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<html", response.text.lower())

    def test_the_prose_surfaces_still_work_alongside_it(self):
        for path in ("/", "/studio"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_upload_detect_and_list(self):
        upload = self.client.post(
            "/pdf/api/upload",
            files={"file": ("form.pdf", self.pdf, "application/pdf")})
        self.assertEqual(upload.status_code, 200)
        name = upload.json()["filename"]

        unbundled = self.client.post("/pdf/api/unbundle", json={"filename": name})
        self.assertEqual(unbundled.status_code, 200)
        self.assertEqual(len(unbundled.json()["pages"]), 1)

        listed = self.client.get("/pdf/api/documents").json()
        self.assertIn(name, listed["documents"])

    def test_region_detection_uses_one_based_pages(self):
        name = self.client.post(
            "/pdf/api/upload",
            files={"file": ("regions.pdf", self.pdf, "application/pdf")}).json()["filename"]

        found = self.client.post("/pdf/api/detect-region",
                                 json={"filename": name, "page": 1, "x": 170, "y": 167})
        self.assertEqual(found.status_code, 200)
        self.assertTrue(found.json()["fields"])

        # Page 0 is rejected rather than silently treated as the first page.
        rejected = self.client.post("/pdf/api/detect-region",
                                    json={"filename": name, "page": 0, "x": 170, "y": 167})
        self.assertEqual(rejected.status_code, 400)

    def test_nothing_is_written_into_the_source_tree(self):
        """The original wrote uploads and outputs beside its own code, which is how a
        repository ends up holding somebody's bank forms."""
        self.client.post("/pdf/api/upload",
                         files={"file": ("stray.pdf", self.pdf, "application/pdf")})
        repo = Path(__file__).resolve().parents[2]
        for directory in ("uploads", "outputs", "backups", "scratch", "qc"):
            self.assertFalse((repo / directory).exists(),
                             f"{directory}/ was created inside the repository")
        self.assertTrue(any(Path(self.tmp.name).rglob("stray.pdf")),
                        "the upload should land in the data directory")


if __name__ == "__main__":
    unittest.main()
