"""Generate form PDFs at test time.

The inherited suite asserted against private documents that cannot live in a public
repository, so the behaviours they covered are exercised here against forms built from
scratch: a ruled line, a row of character cells, and a set of
checkboxes. Nothing here contains anything real.
"""
from __future__ import annotations

import fitz


def ruled_line_form(path: str) -> str:
    """A label with a ruled line to write on: the commonest field on a paper form."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Membership application", fontsize=15)
    page.insert_text((72, 140), "Full name:", fontsize=10)
    page.draw_line(fitz.Point(140, 143), fitz.Point(430, 143))
    page.insert_text((72, 175), "Town:", fontsize=10)
    page.draw_line(fitz.Point(140, 178), fitz.Point(430, 178))
    doc.save(path)
    doc.close()
    return path


def character_cell_form(path: str, cells: int = 6) -> str:
    """A row of separate boxes, one character each: a date or reference number."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Reference", fontsize=15)
    page.insert_text((72, 140), "Number:", fontsize=10)
    for index in range(cells):
        left = 140 + index * 24
        page.draw_rect(fitz.Rect(left, 128, left + 22, 148))
    doc.save(path)
    doc.close()
    return path


def date_parts_form(path: str) -> str:
    """Day, month and year as three separate groups, which must not merge into one field."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Date of birth", fontsize=15)
    x = 72
    for label, count in (("Day", 2), ("Month", 2), ("Year", 4)):
        page.insert_text((x, 130), label, fontsize=9)
        for index in range(count):
            left = x + index * 24
            page.draw_rect(fitz.Rect(left, 136, left + 22, 156))
        x += count * 24 + 40
    doc.save(path)
    doc.close()
    return path
