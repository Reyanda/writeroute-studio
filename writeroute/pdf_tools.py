from __future__ import annotations
import io
import re
from typing import Any
import fitz  # PyMuPDF


def merge_pdfs(pdf_files: list[bytes]) -> bytes:
    """Merges multiple PDF byte streams into a single PDF document using PyMuPDF."""
    doc_out = fitz.open()
    for pdf_data in pdf_files:
        doc_in = fitz.open(stream=pdf_data, filetype="pdf")
        doc_out.insert_pdf(doc_in)
        doc_in.close()
    output = doc_out.tobytes()
    doc_out.close()
    return output


def split_pdf(pdf_bytes: bytes, page_indices: list[int]) -> bytes:
    """Extracts specified zero-indexed page numbers from a PDF."""
    doc_in = fitz.open(stream=pdf_bytes, filetype="pdf")
    doc_out = fitz.open()
    total = len(doc_in)
    for idx in page_indices:
        if 0 <= idx < total:
            doc_out.insert_pdf(doc_in, from_page=idx, to_page=idx)
    output = doc_out.tobytes()
    doc_out.close()
    doc_in.close()
    return output


def rotate_pdf(pdf_bytes: bytes, angle: int = 90, page_indices: list[int] | None = None) -> bytes:
    """Rotates pages in a PDF by the specified degrees (e.g. 90, 180, 270)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = len(doc)
    target_set = set(page_indices) if page_indices is not None else set(range(total))

    for idx in range(total):
        if idx in target_set:
            doc[idx].set_rotation((doc[idx].rotation + angle) % 360)

    output = doc.tobytes()
    doc.close()
    return output


def watermark_pdf(pdf_bytes: bytes, text: str = "CONFIDENTIAL", opacity: float = 0.25, angle: float = 45.0) -> bytes:
    """Stamps a custom watermark across all pages of a PDF document."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        rect = page.rect
        center = fitz.Point(rect.width * 0.3, rect.height * 0.6)
        page.insert_text(
            center,
            text,
            fontsize=38,
            morph=(center, fitz.Matrix(float(angle))),
            color=(0.5, 0.5, 0.5),
            fill_opacity=opacity,
        )
    output = doc.tobytes()
    doc.close()
    return output



def redact_pdf(pdf_bytes: bytes, terms: list[str]) -> bytes:
    """Permanently redacts (blackouts) matching sensitive terms in a PDF using PyMuPDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        for term in terms:
            if not term.strip():
                continue
            areas = page.search_for(term.strip())
            for rect in areas:
                page.add_redact_annot(rect, fill=(0, 0, 0))
        page.apply_redactions()

    output = doc.tobytes()
    doc.close()
    return output


def extract_pdf_semantic(pdf_bytes: bytes) -> dict[str, Any]:
    """Extracts text, page count, metadata, and structural blocks from a PDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_data = []
    full_text_chunks = []

    for page_idx, page in enumerate(doc, start=1):
        txt = page.get_text()
        full_text_chunks.append(txt)
        pages_data.append({
            "page": page_idx,
            "word_count": len(txt.split()),
            "text": txt,
        })

    metadata = dict(doc.metadata or {})
    total_pages = len(doc)
    doc.close()

    full_text = "\n\n".join(full_text_chunks)
    return {
        "page_count": total_pages,
        "metadata": metadata,
        "pages": pages_data,
        "full_text": full_text,
    }
