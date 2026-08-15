from __future__ import annotations
import base64
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

NS = {
    "w": W_NS,
    "r": R_NS,
    "pr": PKG_REL_NS,
    "ct": CT_NS,
}


@dataclass
class DocxPackage:
    parts: dict[str, bytes]

    @classmethod
    def from_bytes(cls, data: bytes) -> DocxPackage:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        return cls(parts=parts)

    def to_bytes(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in self.parts.items():
                zf.writestr(name, payload)
        return out.getvalue()

    def xml_tree(self, part_name: str) -> etree._Element:
        if part_name not in self.parts:
            raise KeyError(f"Missing part: {part_name}")
        return etree.fromstring(self.parts[part_name])

    def set_xml_tree(self, part_name: str, root: etree._Element) -> None:
        self.parts[part_name] = etree.tostring(
            root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone="yes",
        )


def docx_to_rich_html(docx_bytes: bytes) -> dict[str, Any]:
    """Converts a DOCX OOXML package into rich, formatting-preserved HTML,

    extracting headings, bold/italic runs, tables, lists, SDTs, citations,
    and Word comments without loss.
    """
    pkg = DocxPackage.from_bytes(docx_bytes)
    doc_tree = pkg.xml_tree("word/document.xml")
    body = doc_tree.find("w:body", namespaces=NS)
    if body is None:
        return {"html": "<p></p>", "text": "", "citations": [], "comments": []}

    html_parts = []
    plain_parts = []
    citations = []
    comments = []

    # Extract comments if present
    comment_dict = {}
    if "word/comments.xml" in pkg.parts:
        try:
            comments_tree = pkg.xml_tree("word/comments.xml")
            for c in comments_tree.xpath("./w:comment", namespaces=NS):
                cid = c.get(f"{{{W_NS}}}id")
                author = c.get(f"{{{W_NS}}}author", "Reviewer")
                date = c.get(f"{{{W_NS}}}date", "")
                txt = "".join(t.text or "" for t in c.xpath(".//w:t", namespaces=NS))
                comment_dict[cid] = {"author": author, "date": date, "text": txt}
                comments.append({"id": cid, "author": author, "date": date, "text": txt})
        except Exception:
            pass

    def parse_run(r: etree._Element) -> str:
        # Check formatting
        rPr = r.find("w:rPr", namespaces=NS)
        is_bold = rPr is not None and (rPr.find("w:b", namespaces=NS) is not None)
        is_italic = rPr is not None and (rPr.find("w:i", namespaces=NS) is not None)
        is_strike = rPr is not None and (rPr.find("w:strike", namespaces=NS) is not None)
        is_sub = rPr is not None and (rPr.find("w:vertAlign[@w:val='subscript']", namespaces=NS) is not None)
        is_sup = rPr is not None and (rPr.find("w:vertAlign[@w:val='superscript']", namespaces=NS) is not None)

        text_nodes = r.xpath(".//w:t", namespaces=NS)
        run_text = "".join(t.text or "" for t in text_nodes)
        if not run_text:
            if r.find("w:br", namespaces=NS) is not None or r.find("w:cr", namespaces=NS) is not None:
                return "<br>"
            return ""

        escaped = run_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if is_bold:
            escaped = f"<strong>{escaped}</strong>"
        if is_italic:
            escaped = f"<em>{escaped}</em>"
        if is_strike:
            escaped = f"<s>{escaped}</s>"
        if is_sub:
            escaped = f"<sub>{escaped}</sub>"
        if is_sup:
            escaped = f"<sup>{escaped}</sup>"
        return escaped

    def parse_paragraph_content(p: etree._Element) -> str:
        rendered = []
        for child in p:
            tag = etree.QName(child).localname
            if tag == "r":
                rendered.append(parse_run(child))
            elif tag == "sdt":
                # Mendeley or Word SDT citation
                tag_node = child.find(".//w:sdtPr/w:tag", namespaces=NS)
                tag_val = tag_node.get(f"{{{W_NS}}}val", "") if tag_node is not None else ""
                sdt_text = "".join(t.text or "" for t in child.xpath(".//w:t", namespaces=NS))
                if "MENDELEY_CITATION" in tag_val:
                    citations.append({"type": "mendeley_sdt", "tag": tag_val, "rendered": sdt_text})
                    rendered.append(f'<span class="citation-tag" data-sdt="mendeley">{sdt_text or "[Citation]"}</span>')
                else:
                    rendered.append(f'<span class="sdt-field">{sdt_text}</span>')
            elif tag == "hyperlink":
                link_runs = "".join(parse_run(r) for r in child.xpath(".//w:r", namespaces=NS))
                rendered.append(f'<a href="#">{link_runs}</a>')
        return "".join(rendered)

    for child in body:
        tag = etree.QName(child).localname
        if tag == "p":
            pPr = child.find("w:pPr", namespaces=NS)
            pStyle = pPr.find("w:pStyle", namespaces=NS) if pPr is not None else None
            style_val = pStyle.get(f"{{{W_NS}}}val", "").lower() if pStyle is not None else ""

            content = parse_paragraph_content(child)
            plain_txt = "".join(t.text or "" for t in child.xpath(".//w:t", namespaces=NS)).strip()

            if not content.strip():
                continue

            plain_parts.append(plain_txt)

            if "heading1" in style_val or "title" in style_val:
                html_parts.append(f"<h1>{content}</h1>")
            elif "heading2" in style_val:
                html_parts.append(f"<h2>{content}</h2>")
            elif "heading3" in style_val:
                html_parts.append(f"<h3>{content}</h3>")
            elif "heading4" in style_val:
                html_parts.append(f"<h4>{content}</h4>")
            elif "quote" in style_val or "blockquote" in style_val:
                html_parts.append(f"<blockquote>{content}</blockquote>")
            elif "list" in style_val or (pPr is not None and pPr.find("w:numPr", namespaces=NS) is not None):
                html_parts.append(f"<li>{content}</li>")
            else:
                html_parts.append(f"<p>{content}</p>")

        elif tag == "tbl":
            # Table conversion
            tbl_html = ['<table class="scientific-table"><tbody>']
            tbl_plain = []
            for row_idx, tr in enumerate(child.xpath("./w:tr", namespaces=NS)):
                tbl_html.append("<tr>")
                row_cells = []
                for tc in tr.xpath("./w:tc", namespaces=NS):
                    cell_content = "".join(parse_paragraph_content(p) for p in tc.xpath("./w:p", namespaces=NS))
                    cell_plain = "".join(t.text or "" for t in tc.xpath(".//w:t", namespaces=NS)).strip()
                    row_cells.append(cell_plain)
                    if row_idx == 0:
                        tbl_html.append(f"<th>{cell_content}</th>")
                    else:
                        tbl_html.append(f"<td>{cell_content}</td>")
                tbl_html.append("</tr>")
                tbl_plain.append("\t".join(row_cells))
            tbl_html.append("</tbody></table>")
            html_parts.append("".join(tbl_html))
            plain_parts.append("\n".join(tbl_plain))

    full_html = "\n".join(html_parts)
    full_text = "\n\n".join(plain_parts)

    return {
        "html": full_html,
        "text": full_text,
        "citations": citations,
        "comments": comments,
        "sdt_count": len(citations),
    }
