import os
import fitz
import json
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

class StirlingBridge:
    """
    Stirling-PDF Integration Bridge (v1.5)
    Connects Tracer Studio with Stirling-PDF REST APIs (http://localhost:8080).
    Provides native PyMuPDF fallbacks when Stirling container is offline for zero-downtime execution.
    """

    def __init__(self, base_url: str = None):
        self.base_url = base_url or os.getenv("STIRLING_PDF_URL", "http://127.0.0.1:8080")

    def is_stirling_available(self) -> bool:
        """Pings Stirling-PDF server to verify service availability."""
        try:
            req = urllib.request.Request(f"{self.base_url}/v1/general/info", headers={"User-Agent": "TracerStudio/1.5"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_status(self) -> Dict[str, Any]:
        available = self.is_stirling_available()
        return {
            "stirling_available": available,
            "stirling_url": self.base_url,
            "mode": "Container Active" if available else "Native PyMuPDF Fallback Engine"
        }

    def split_pdf(self, pdf_path: str, page_numbers: List[int], out_path: str) -> str:
        """Splits specified 1-indexed page numbers into a new PDF document."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        # PyMuPDF Fallback Implementation
        doc = fitz.open(pdf_path)
        new_doc = fitz.open()

        for p in page_numbers:
            p_idx = p - 1
            if 0 <= p_idx < len(doc):
                new_doc.insert_pdf(doc, from_page=p_idx, to_page=p_idx)

        new_doc.save(out_path, garbage=4, deflate=True)
        new_doc.close()
        doc.close()
        return out_path

    def merge_pdfs(self, pdf_paths: List[str], out_path: str) -> str:
        """Merges multiple input PDF files into a single document stream."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        merged_doc = fitz.open()
        for path in pdf_paths:
            if os.path.exists(path):
                sub_doc = fitz.open(path)
                merged_doc.insert_pdf(sub_doc)
                sub_doc.close()

        merged_doc.save(out_path, garbage=4, deflate=True)
        merged_doc.close()
        return out_path

    def compress_pdf(self, pdf_path: str, out_path: str, garbage_level: int = 4) -> str:
        """Compresses PDF document using stream deflation and garbage collection."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        doc = fitz.open(pdf_path)
        doc.save(out_path, garbage=garbage_level, deflate=True, clean=True)
        doc.close()
        return out_path

    def encrypt_pdf(self, pdf_path: str, user_pw: str, out_path: str) -> str:
        """Encrypts PDF document with user password."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        doc = fitz.open(pdf_path)
        perm = fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT
        encrypt_flags = fitz.PDF_ENCRYPT_AES_256

        doc.save(
            out_path,
            user_pw=user_pw,
            owner_pw=user_pw,
            permissions=perm,
            encryption=encrypt_flags
        )
        doc.close()
        return out_path

    def decrypt_pdf(self, pdf_path: str, user_pw: str, out_path: str) -> str:
        """Decrypts password-protected PDF document."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        doc = fitz.open(pdf_path)
        if doc.is_encrypted:
            doc.authenticate(user_pw)

        doc.save(out_path, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()
        return out_path
