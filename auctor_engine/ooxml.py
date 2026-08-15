from __future__ import annotations

import copy
import json
import posixpath
import re
import tempfile
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lxml import etree

from .models import Issue, ManuscriptProfile, RevisionProposal

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "xml": "http://www.w3.org/XML/1998/namespace",
    "awe": "urn:auctor:qc:v1",
}

REL_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
REL_CUSTOM_XML = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml"
CONTENT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
EM_DASH_RE = re.compile("[\u2014\u2015]")
XML_PARSER = etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True, recover=False)


def qn(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _xml_space(element: etree._Element, text: str) -> None:
    if text[:1].isspace() or text[-1:].isspace() or "  " in text:
        element.set(qn("xml", "space"), "preserve")


def _ensure_child(parent: etree._Element, tag: str, *, first: bool = False) -> etree._Element:
    child = parent.find(tag)
    if child is not None:
        return child
    child = etree.Element(tag)
    if first:
        parent.insert(0, child)
    else:
        parent.append(child)
    return child


def _child_text(element: etree._Element, *, include_deletions: bool = False) -> str:
    pieces: list[str] = []
    for node in element.iter():
        if node.tag not in {qn("w", "t"), qn("w", "delText"), qn("w", "tab"), qn("w", "br")}:
            continue
        if node.tag == qn("w", "delText") and not include_deletions:
            continue
        if node.tag == qn("w", "t"):
            ancestor = node.getparent()
            deleted = False
            while ancestor is not None and ancestor is not element:
                if ancestor.tag == qn("w", "del"):
                    deleted = True
                    break
                ancestor = ancestor.getparent()
            if deleted and not include_deletions:
                continue
        if node.tag == qn("w", "tab"):
            pieces.append("\t")
        elif node.tag == qn("w", "br"):
            pieces.append("\n")
        else:
            pieces.append(node.text or "")
    return "".join(pieces)


def _clone_rpr(run: etree._Element | None) -> etree._Element | None:
    if run is None:
        return None
    rpr = run.find(qn("w", "rPr"))
    return copy.deepcopy(rpr) if rpr is not None else None


def _new_run(text: str, rpr: etree._Element | None = None, *, deleted: bool = False) -> etree._Element:
    run = etree.Element(qn("w", "r"))
    if rpr is not None:
        run.append(copy.deepcopy(rpr))
    text_tag = qn("w", "delText") if deleted else qn("w", "t")
    text_node = etree.SubElement(run, text_tag)
    text_node.text = text
    _xml_space(text_node, text)
    return run


def _set_rpr_properties(rpr: etree._Element, font: str, size_half_points: int) -> None:
    rfonts = _ensure_child(rpr, qn("w", "rFonts"), first=True)
    for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
        rfonts.set(qn("w", attr), font)
    sz = _ensure_child(rpr, qn("w", "sz"))
    sz.set(qn("w", "val"), str(size_half_points))
    szcs = _ensure_child(rpr, qn("w", "szCs"))
    szcs.set(qn("w", "val"), str(size_half_points))
    color = rpr.find(qn("w", "color"))
    if color is None:
        color = etree.SubElement(rpr, qn("w", "color"))
    if color.get(qn("w", "val")) in {None, "auto"}:
        color.set(qn("w", "val"), "000000")


def _set_run_font(run: etree._Element, font: str, size_half_points: int) -> None:
    rpr = run.find(qn("w", "rPr"))
    if rpr is None:
        rpr = etree.Element(qn("w", "rPr"))
        run.insert(0, rpr)
    _set_rpr_properties(rpr, font, size_half_points)


def _set_paragraph_mark_font(paragraph: etree._Element, font: str, size_half_points: int) -> None:
    ppr = paragraph.find(qn("w", "pPr"))
    if ppr is None:
        ppr = etree.Element(qn("w", "pPr"))
        paragraph.insert(0, ppr)
    rpr = ppr.find(qn("w", "rPr"))
    if rpr is None:
        rpr = etree.SubElement(ppr, qn("w", "rPr"))
    _set_rpr_properties(rpr, font, size_half_points)


def _set_style_rpr(style: etree._Element, font: str, size_half_points: int, *, bold: bool | None = None) -> None:
    rpr = style.find(qn("w", "rPr"))
    if rpr is None:
        rpr = etree.SubElement(style, qn("w", "rPr"))
    _set_rpr_properties(rpr, font, size_half_points)
    if bold is True and rpr.find(qn("w", "b")) is None:
        etree.SubElement(rpr, qn("w", "b"))
        etree.SubElement(rpr, qn("w", "bCs"))


def _sanitize_generated_text(text: str) -> str:
    return EM_DASH_RE.sub(" - ", text)


class OOXMLPackage:
    """Direct OOXML package editor with preservation-oriented operations."""

    def __init__(self, source: str | Path):
        self.source = Path(source)
        if not self.source.exists():
            raise FileNotFoundError(self.source)
        with zipfile.ZipFile(self.source, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"Corrupt DOCX member: {bad}")
            self.parts: dict[str, bytes] = {name: archive.read(name) for name in archive.namelist()}
        if "word/document.xml" not in self.parts:
            raise ValueError("The package has no word/document.xml part.")
        self._revision_counter: int | None = None
        self._bookmark_counter: int | None = None
        self._sdt_counter: int | None = None

    def has_part(self, name: str) -> bool:
        return name in self.parts

    def xml(self, name: str) -> etree._Element:
        if name not in self.parts:
            raise KeyError(name)
        return etree.fromstring(self.parts[name], parser=XML_PARSER)

    def set_xml(self, name: str, root: etree._Element) -> None:
        self.parts[name] = etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            standalone=True,
        )

    def write(self, output: str | Path) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=output.stem, suffix=".docx", dir=output.parent, delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, data in self.parts.items():
                    archive.writestr(name, data)
            temp_path.replace(output)
        finally:
            if temp_path.exists() and temp_path != output:
                temp_path.unlink(missing_ok=True)
        return output

    def ensure_content_type_override(self, part_name: str, content_type: str) -> None:
        root = self.xml("[Content_Types].xml")
        normalized = part_name if part_name.startswith("/") else f"/{part_name}"
        found = root.xpath("./ct:Override[@PartName=$part]", namespaces=NS, part=normalized)
        if not found:
            node = etree.SubElement(root, qn("ct", "Override"))
            node.set("PartName", normalized)
            node.set("ContentType", content_type)
        else:
            found[0].set("ContentType", content_type)
        self.set_xml("[Content_Types].xml", root)

    def ensure_default_content_type(self, extension: str, content_type: str) -> None:
        root = self.xml("[Content_Types].xml")
        found = root.xpath("./ct:Default[@Extension=$ext]", namespaces=NS, ext=extension)
        if not found:
            node = etree.SubElement(root, qn("ct", "Default"))
            node.set("Extension", extension)
            node.set("ContentType", content_type)
        self.set_xml("[Content_Types].xml", root)

    def ensure_relationship(self, rels_part: str, rel_type: str, target: str) -> str:
        if rels_part in self.parts:
            root = self.xml(rels_part)
        else:
            root = etree.Element(qn("pr", "Relationships"), nsmap={None: NS["pr"]})
        for rel in root.findall(qn("pr", "Relationship")):
            if rel.get("Type") == rel_type and rel.get("Target") == target:
                return str(rel.get("Id"))
        used: set[int] = set()
        for rel in root.findall(qn("pr", "Relationship")):
            match = re.fullmatch(r"rId(\d+)", str(rel.get("Id", "")))
            if match:
                used.add(int(match.group(1)))
        number = 1
        while number in used:
            number += 1
        rid = f"rId{number}"
        node = etree.SubElement(root, qn("pr", "Relationship"))
        node.set("Id", rid)
        node.set("Type", rel_type)
        node.set("Target", target)
        self.set_xml(rels_part, root)
        return rid

    def document_root(self) -> etree._Element:
        return self.xml("word/document.xml")

    def save_document_root(self, root: etree._Element) -> None:
        self.set_xml("word/document.xml", root)

    def paragraph_text(self, paragraph: etree._Element, *, include_deletions: bool = False) -> str:
        return _child_text(paragraph, include_deletions=include_deletions)

    def paragraphs(self) -> list[etree._Element]:
        return list(self.document_root().xpath("//w:p", namespaces=NS))

    def paragraph_records(self, root: etree._Element | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        current_section = "other"
        root = root if root is not None else self.document_root()
        for index, paragraph in enumerate(root.xpath("//w:p", namespaces=NS)):
            text = self.paragraph_text(paragraph).strip()
            style = ""
            pstyle = paragraph.find("./w:pPr/w:pStyle", namespaces=NS)
            if pstyle is not None:
                style = pstyle.get(qn("w", "val"), "")
            candidate = self._canonical_section(text, style)
            if candidate:
                current_section = candidate
            records.append(
                {
                    "index": index,
                    "paragraph": paragraph,
                    "text": text,
                    "style": style,
                    "section": current_section,
                    "in_table": bool(paragraph.xpath("ancestor::w:tbl", namespaces=NS)),
                }
            )
        return records

    @staticmethod
    def _canonical_section(text: str, style: str) -> str | None:
        cleaned = re.sub(r"\s+", " ", text.strip().lower()).rstrip(":")
        aliases = {
            "title": "title",
            "abstract": "abstract",
            "summary": "abstract",
            "background": "introduction",
            "introduction": "introduction",
            "methods": "methods",
            "methodology": "methods",
            "materials and methods": "methods",
            "results": "results",
            "findings": "results",
            "discussion": "discussion",
            "interpretation": "discussion",
            "limitations": "limitations",
            "strengths and limitations": "limitations",
            "conclusion": "conclusion",
            "conclusions": "conclusion",
            "references": "references",
            "bibliography": "references",
        }
        if cleaned in aliases:
            return aliases[cleaned]
        if style.lower().startswith("heading") and cleaned in aliases:
            return aliases[cleaned]
        if style.lower() == "title":
            return "title"
        return None

    def semantic_reference_state(self) -> dict[str, Any]:
        """Return semantic citation, bibliography, bookmark, and REF integrity state."""

        document = self.document_root()
        tags = [node.get(qn("w", "val"), "") for node in document.xpath("//w:sdtPr/w:tag", namespaces=NS)]
        citation_prefix = "AUCTOR:CITATION:"
        reference_prefix = "AUCTOR:REFERENCE:"
        citation_keys = [value[len(citation_prefix) :] for value in tags if value.startswith(citation_prefix)]
        reference_keys = [value[len(reference_prefix) :] for value in tags if value.startswith(reference_prefix)]
        valid_key = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
        invalid_tags = [
            value
            for value in tags
            if value.startswith("AUCTOR:")
            and not (
                (value.startswith(citation_prefix) and valid_key.fullmatch(value[len(citation_prefix) :]))
                or (value.startswith(reference_prefix) and valid_key.fullmatch(value[len(reference_prefix) :]))
                or value.startswith("AUCTOR:SECTION:")
                or value.startswith("AUCTOR:FORMAT:")
            )
        ]

        citation_set = set(citation_keys)
        reference_set = set(reference_keys)
        reference_counts = {key: reference_keys.count(key) for key in sorted(reference_set)}
        duplicate_reference_keys = [key for key, count in reference_counts.items() if count > 1]

        bookmark_names = [
            node.get(qn("w", "name"), "")
            for node in document.xpath("//w:bookmarkStart", namespaces=NS)
            if node.get(qn("w", "name"), "")
        ]
        duplicate_bookmark_names = sorted({name for name in bookmark_names if bookmark_names.count(name) > 1})
        bookmark_set = set(bookmark_names)

        ref_targets: list[str] = []
        ref_pattern = re.compile(r"\bREF\s+\"?([A-Za-z_][A-Za-z0-9_.-]*)\"?", re.IGNORECASE)
        for field in document.xpath("//w:fldSimple", namespaces=NS):
            instruction = field.get(qn("w", "instr"), "")
            match = ref_pattern.search(instruction)
            if match:
                ref_targets.append(match.group(1))
        for instruction in document.xpath("//w:instrText", namespaces=NS):
            match = ref_pattern.search(instruction.text or "")
            if match:
                ref_targets.append(match.group(1))

        return {
            "citation_keys": citation_keys,
            "reference_keys": reference_keys,
            "orphan_citation_keys": sorted(citation_set - reference_set),
            "uncited_reference_keys": sorted(reference_set - citation_set),
            "duplicate_reference_keys": duplicate_reference_keys,
            "invalid_semantic_tags": sorted(set(invalid_tags)),
            "bookmark_names": bookmark_names,
            "duplicate_bookmark_names": duplicate_bookmark_names,
            "ref_targets": ref_targets,
            "missing_ref_targets": sorted(set(ref_targets) - bookmark_set),
        }

    def inventory(self) -> dict[str, Any]:
        document = self.document_root()
        comments = 0
        unresolved = 0
        if "word/comments.xml" in self.parts:
            comments_root = self.xml("word/comments.xml")
            comments = len(comments_root.findall(qn("w", "comment")))
            unresolved = comments
            if "word/commentsExtended.xml" in self.parts:
                ext_root = self.xml("word/commentsExtended.xml")
                done = len(ext_root.xpath("//w15:commentEx[@w15:done='1']", namespaces=NS))
                unresolved = max(0, comments - done)
        tag_nodes = document.xpath("//w:sdtPr/w:tag", namespaces=NS)
        citation_tags = [node.get(qn("w", "val"), "") for node in tag_nodes if "CITATION:" in node.get(qn("w", "val"), "")]
        reference_tags = [node.get(qn("w", "val"), "") for node in tag_nodes if "REFERENCE:" in node.get(qn("w", "val"), "")]
        return {
            "parts": len(self.parts),
            "paragraphs": len(document.xpath("//w:p", namespaces=NS)),
            "tables": len(document.xpath("//w:tbl", namespaces=NS)),
            "comments": comments,
            "unresolved_comments": unresolved,
            "tracked_insertions": len(document.xpath("//w:ins", namespaces=NS)),
            "tracked_deletions": len(document.xpath("//w:del", namespaces=NS)),
            "bookmarks": len(document.xpath("//w:bookmarkStart", namespaces=NS)),
            "fields": len(document.xpath("//w:fldSimple | //w:instrText", namespaces=NS)),
            "citation_tags": len(citation_tags),
            "reference_tags": len(reference_tags),
            "citation_keys": citation_tags,
            "reference_keys": reference_tags,
            "semantic_reference_state": self.semantic_reference_state(),
            "footnotes_part": "word/footnotes.xml" in self.parts,
            "endnotes_part": "word/endnotes.xml" in self.parts,
            "headers": len([name for name in self.parts if name.startswith("word/header") and name.endswith(".xml")]),
            "footers": len([name for name in self.parts if name.startswith("word/footer") and name.endswith(".xml")]),
            "em_dash_characters": self.count_em_dashes(),
        }

    def review_state(self) -> dict[str, Any]:
        document = self.document_root()
        comments: list[dict[str, Any]] = []
        if "word/comments.xml" in self.parts:
            comments_root = self.xml("word/comments.xml")
            for node in comments_root.findall(qn("w", "comment")):
                cid = node.get(qn("w", "id"), "")
                comments.append(
                    {
                        "id": cid,
                        "author": node.get(qn("w", "author"), ""),
                        "initials": node.get(qn("w", "initials"), ""),
                        "date": node.get(qn("w", "date"), ""),
                        "text": _child_text(node, include_deletions=True),
                        "anchor_present": bool(
                            document.xpath(
                                "//w:commentRangeStart[@w:id=$cid]",
                                namespaces=NS,
                                cid=cid,
                            )
                        ),
                    }
                )
        revisions: list[dict[str, Any]] = []
        for node in document.xpath("//w:ins | //w:del", namespaces=NS):
            kind = "insertion" if node.tag == qn("w", "ins") else "deletion"
            revisions.append(
                {
                    "id": node.get(qn("w", "id"), ""),
                    "kind": kind,
                    "author": node.get(qn("w", "author"), ""),
                    "date": node.get(qn("w", "date"), ""),
                    "text": _child_text(node, include_deletions=True),
                }
            )
        return {"comments": comments, "revisions": revisions}

    def next_revision_id(self) -> int:
        if self._revision_counter is None:
            maximum = -1
            for part in [name for name in self.parts if name.endswith(".xml") and name.startswith("word/")]:
                try:
                    root = self.xml(part)
                except etree.XMLSyntaxError:
                    continue
                for node in root.xpath("//w:ins | //w:del | //w:moveFrom | //w:moveTo", namespaces=NS):
                    value = node.get(qn("w", "id"))
                    if value and value.lstrip("-").isdigit():
                        maximum = max(maximum, int(value))
            self._revision_counter = maximum + 1
        value = self._revision_counter
        self._revision_counter += 1
        return value

    def next_comment_id(self) -> int:
        if "word/comments.xml" not in self.parts:
            return 0
        root = self.xml("word/comments.xml")
        ids = [int(node.get(qn("w", "id"))) for node in root.findall(qn("w", "comment")) if str(node.get(qn("w", "id"), "")).isdigit()]
        return max(ids, default=-1) + 1

    def next_bookmark_id(self) -> int:
        if self._bookmark_counter is None:
            root = self.document_root()
            ids = [int(node.get(qn("w", "id"))) for node in root.xpath("//w:bookmarkStart", namespaces=NS) if str(node.get(qn("w", "id"), "")).isdigit()]
            self._bookmark_counter = max(ids, default=-1) + 1
        value = self._bookmark_counter
        self._bookmark_counter += 1
        return value

    def next_sdt_id(self) -> int:
        if self._sdt_counter is None:
            root = self.document_root()
            ids = [int(node.get(qn("w", "val"))) for node in root.xpath("//w:sdtPr/w:id", namespaces=NS) if str(node.get(qn("w", "val"), "")).isdigit()]
            self._sdt_counter = max(ids, default=1000000) + 1
        value = self._sdt_counter
        self._sdt_counter += 1
        return value

    def request_field_update(self) -> None:
        if "word/settings.xml" in self.parts:
            root = self.xml("word/settings.xml")
        else:
            root = etree.Element(qn("w", "settings"), nsmap={"w": NS["w"]})
            self.ensure_content_type_override(
                "/word/settings.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
            )
        update = root.find(qn("w", "updateFields"))
        if update is None:
            update = etree.SubElement(root, qn("w", "updateFields"))
        update.set(qn("w", "val"), "true")
        self.set_xml("word/settings.xml", root)

    def enable_track_revisions(self) -> None:
        if "word/settings.xml" in self.parts:
            root = self.xml("word/settings.xml")
        else:
            root = etree.Element(qn("w", "settings"), nsmap={"w": NS["w"]})
            self.ensure_content_type_override(
                "/word/settings.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
            )
        if root.find(qn("w", "trackRevisions")) is None:
            root.insert(0, etree.Element(qn("w", "trackRevisions")))
        update = root.find(qn("w", "updateFields"))
        if update is None:
            update = etree.SubElement(root, qn("w", "updateFields"))
        update.set(qn("w", "val"), "true")
        self.set_xml("word/settings.xml", root)

    def normalize_manuscript_format(self, profile: ManuscriptProfile) -> None:
        self._normalize_styles(profile)
        self._normalize_font_table(profile)
        text_parts = [
            name
            for name in self.parts
            if name == "word/document.xml"
            or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
            or name in {"word/footnotes.xml", "word/endnotes.xml"}
        ]
        for part in text_parts:
            root = self.xml(part)
            table_runs = set(root.xpath("//w:tbl//w:r", namespaces=NS))
            for run in root.xpath("//w:r", namespaces=NS):
                if run in table_runs:
                    _set_run_font(run, profile.table_font, int(round(profile.table_size_pt * 2)))
                else:
                    _set_run_font(run, profile.body_font, int(round(profile.body_size_pt * 2)))
            for paragraph in root.xpath("//w:p", namespaces=NS):
                in_table = bool(paragraph.xpath("ancestor::w:tbl", namespaces=NS))
                _set_paragraph_mark_font(
                    paragraph,
                    profile.table_font if in_table else profile.body_font,
                    int(round((profile.table_size_pt if in_table else profile.body_size_pt) * 2)),
                )
            if part == "word/document.xml":
                self._normalize_sections(root, profile)
            self.set_xml(part, root)

    def _normalize_styles(self, profile: ManuscriptProfile) -> None:
        if "word/styles.xml" not in self.parts:
            return
        root = self.xml("word/styles.xml")
        doc_defaults = root.find(qn("w", "docDefaults"))
        if doc_defaults is None:
            doc_defaults = etree.Element(qn("w", "docDefaults"))
            root.insert(0, doc_defaults)
        rpr_default = _ensure_child(doc_defaults, qn("w", "rPrDefault"))
        rpr = _ensure_child(rpr_default, qn("w", "rPr"))
        _set_rpr_properties(rpr, profile.body_font, int(round(profile.body_size_pt * 2)))

        for style in root.findall(qn("w", "style")):
            style_id = style.get(qn("w", "styleId"), "")
            style_type = style.get(qn("w", "type"), "")
            if style_id in {"Normal", "DefaultParagraphFont"}:
                _set_style_rpr(style, profile.body_font, int(round(profile.body_size_pt * 2)))
            elif style_id.lower().startswith("heading") or style_id in {"Title", "Subtitle"}:
                _set_style_rpr(style, profile.body_font, int(round(profile.body_size_pt * 2)), bold=True)
                if style_id == "Title":
                    ppr = style.find(qn("w", "pPr"))
                    if ppr is not None:
                        border = ppr.find(qn("w", "pBdr"))
                        if border is not None:
                            ppr.remove(border)
            elif style_id in {"TableNormal", "TableGrid"} or style_type == "table":
                _set_style_rpr(style, profile.table_font, int(round(profile.table_size_pt * 2)))

        self._ensure_custom_style(root, "AuctorBody", "Auctor Body", "paragraph", profile.body_font, 22, based_on="Normal")
        self._ensure_custom_style(root, "AuctorHeading1", "Auctor Heading 1", "paragraph", profile.body_font, 22, based_on="Heading1", bold=True)
        self._ensure_custom_style(root, "AuctorHeading2", "Auctor Heading 2", "paragraph", profile.body_font, 22, based_on="Heading2", bold=True)
        self._ensure_custom_style(root, "AuctorTableText", "Auctor Table Text", "paragraph", profile.table_font, 16, based_on="Normal")
        self.set_xml("word/styles.xml", root)

    @staticmethod
    def _ensure_custom_style(
        root: etree._Element,
        style_id: str,
        name: str,
        style_type: str,
        font: str,
        size: int,
        *,
        based_on: str,
        bold: bool = False,
    ) -> None:
        found = root.xpath("./w:style[@w:styleId=$sid]", namespaces=NS, sid=style_id)
        if found:
            style = found[0]
        else:
            style = etree.SubElement(root, qn("w", "style"))
            style.set(qn("w", "type"), style_type)
            style.set(qn("w", "customStyle"), "1")
            style.set(qn("w", "styleId"), style_id)
            name_node = etree.SubElement(style, qn("w", "name"))
            name_node.set(qn("w", "val"), name)
            based_node = etree.SubElement(style, qn("w", "basedOn"))
            based_node.set(qn("w", "val"), based_on)
            next_node = etree.SubElement(style, qn("w", "next"))
            next_node.set(qn("w", "val"), "AuctorBody" if style_id.startswith("AuctorHeading") else style_id)
            etree.SubElement(style, qn("w", "qFormat"))
        _set_style_rpr(style, font, size, bold=bold)
        if style_id.startswith("AuctorHeading"):
            ppr = style.find(qn("w", "pPr"))
            if ppr is None:
                ppr = etree.SubElement(style, qn("w", "pPr"))
            if ppr.find(qn("w", "keepNext")) is None:
                etree.SubElement(ppr, qn("w", "keepNext"))
            if ppr.find(qn("w", "keepLines")) is None:
                etree.SubElement(ppr, qn("w", "keepLines"))

    def _normalize_font_table(self, profile: ManuscriptProfile) -> None:
        if "word/fontTable.xml" not in self.parts:
            return
        root = self.xml("word/fontTable.xml")
        names = {node.get(qn("w", "name")) for node in root.findall(qn("w", "font"))}
        if profile.body_font not in names:
            node = etree.SubElement(root, qn("w", "font"))
            node.set(qn("w", "name"), profile.body_font)
            family = etree.SubElement(node, qn("w", "family"))
            family.set(qn("w", "val"), "roman")
            pitch = etree.SubElement(node, qn("w", "pitch"))
            pitch.set(qn("w", "val"), "variable")
        self.set_xml("word/fontTable.xml", root)

    @staticmethod
    def _normalize_sections(root: etree._Element, profile: ManuscriptProfile) -> None:
        for sect in root.xpath("//w:sectPr", namespaces=NS):
            pgsz = sect.find(qn("w", "pgSz"))
            if pgsz is None:
                pgsz = etree.SubElement(sect, qn("w", "pgSz"))
            if profile.page_size.upper() == "A4":
                pgsz.set(qn("w", "w"), "11906")
                pgsz.set(qn("w", "h"), "16838")
            pgmar = sect.find(qn("w", "pgMar"))
            if pgmar is None:
                pgmar = etree.SubElement(sect, qn("w", "pgMar"))
            margin = str(int(round(profile.margin_cm / 2.54 * 1440)))
            for attr in ("top", "right", "bottom", "left"):
                pgmar.set(qn("w", attr), margin)
            for attr, value in (("header", "720"), ("footer", "720"), ("gutter", "0")):
                if pgmar.get(qn("w", attr)) is None:
                    pgmar.set(qn("w", attr), value)

    def add_comment(self, text: str, *, author: str, initials: str, date: str | None = None) -> int:
        comment_id = self.next_comment_id()
        if "word/comments.xml" in self.parts:
            root = self.xml("word/comments.xml")
        else:
            root = etree.Element(qn("w", "comments"), nsmap={"w": NS["w"]})
        comment = etree.SubElement(root, qn("w", "comment"))
        comment.set(qn("w", "id"), str(comment_id))
        comment.set(qn("w", "author"), _sanitize_generated_text(author))
        comment.set(qn("w", "initials"), _sanitize_generated_text(initials))
        comment.set(qn("w", "date"), date or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        paragraph = etree.SubElement(comment, qn("w", "p"))
        ppr = etree.SubElement(paragraph, qn("w", "pPr"))
        pstyle = etree.SubElement(ppr, qn("w", "pStyle"))
        pstyle.set(qn("w", "val"), "CommentText")
        reference_run = etree.SubElement(paragraph, qn("w", "r"))
        ref_rpr = etree.SubElement(reference_run, qn("w", "rPr"))
        ref_style = etree.SubElement(ref_rpr, qn("w", "rStyle"))
        ref_style.set(qn("w", "val"), "CommentReference")
        etree.SubElement(reference_run, qn("w", "annotationRef"))
        body_run = etree.SubElement(paragraph, qn("w", "r"))
        body_text = etree.SubElement(body_run, qn("w", "t"))
        clean_text = _sanitize_generated_text(text)
        body_text.text = clean_text
        _xml_space(body_text, clean_text)
        self.set_xml("word/comments.xml", root)
        self.ensure_relationship("word/_rels/document.xml.rels", REL_COMMENTS, "comments.xml")
        self.ensure_content_type_override("/word/comments.xml", CONTENT_COMMENTS)
        return comment_id

    @staticmethod
    def _comment_reference_run(comment_id: int) -> etree._Element:
        run = etree.Element(qn("w", "r"))
        rpr = etree.SubElement(run, qn("w", "rPr"))
        style = etree.SubElement(rpr, qn("w", "rStyle"))
        style.set(qn("w", "val"), "CommentReference")
        reference = etree.SubElement(run, qn("w", "commentReference"))
        reference.set(qn("w", "id"), str(comment_id))
        return run

    def replace_text_with_revision(
        self,
        paragraph: etree._Element,
        target: str,
        replacement: str,
        *,
        author: str,
        initials: str,
        reason: str = "",
        start_hint: int | None = None,
        track_changes: bool = True,
        add_comment: bool = True,
        date: str | None = None,
    ) -> dict[str, Any]:
        if not target:
            return {"applied": False, "reason": "empty_target"}
        match = self._locate_safe_run_span(paragraph, target, start_hint=start_hint)
        if match is None:
            return {"applied": False, "reason": "unsafe_or_missing_anchor"}
        group, local_start, local_end, global_start = match
        start_run, start_offset = self._locate_offset(group, local_start)
        end_run, end_offset = self._locate_offset(group, local_end, end=True)
        start_element = group[start_run]["run"]
        end_element = group[end_run]["run"]
        first_rpr = _clone_rpr(start_element)
        last_rpr = _clone_rpr(end_element)
        before = group[start_run]["text"][:start_offset]
        after = group[end_run]["text"][end_offset:]
        parent = paragraph
        start_child_index = parent.index(start_element)
        affected = [item["run"] for item in group[start_run : end_run + 1]]
        for run in affected:
            parent.remove(run)

        sequence: list[etree._Element] = []
        if before:
            sequence.append(_new_run(before, first_rpr))

        revision_ids: list[int] = []
        deleted_element: etree._Element | None = None
        inserted_element: etree._Element | None = None
        timestamp = date or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if track_changes:
            self.enable_track_revisions()
            delete_id = self.next_revision_id()
            insert_id = self.next_revision_id() if replacement else None
            deleted_element = etree.Element(qn("w", "del"))
            deleted_element.set(qn("w", "id"), str(delete_id))
            deleted_element.set(qn("w", "author"), _sanitize_generated_text(author))
            deleted_element.set(qn("w", "date"), timestamp)
            deleted_element.append(_new_run(target, first_rpr, deleted=True))
            sequence.append(deleted_element)
            revision_ids.append(delete_id)
            if replacement:
                inserted_element = etree.Element(qn("w", "ins"))
                inserted_element.set(qn("w", "id"), str(insert_id))
                inserted_element.set(qn("w", "author"), _sanitize_generated_text(author))
                inserted_element.set(qn("w", "date"), timestamp)
                inserted_element.append(_new_run(replacement, first_rpr))
                sequence.append(inserted_element)
                revision_ids.append(int(insert_id))
        elif replacement:
            inserted_element = _new_run(replacement, first_rpr)
            sequence.append(inserted_element)

        if after:
            sequence.append(_new_run(after, last_rpr))

        anchor_element = inserted_element if inserted_element is not None else deleted_element
        comment_id: int | None = None
        if add_comment and reason and anchor_element is not None:
            comment_id = self.add_comment(
                reason,
                author=author,
                initials=initials,
                date=timestamp,
            )
            range_start = etree.Element(qn("w", "commentRangeStart"))
            range_start.set(qn("w", "id"), str(comment_id))
            range_end = etree.Element(qn("w", "commentRangeEnd"))
            range_end.set(qn("w", "id"), str(comment_id))
            augmented: list[etree._Element] = []
            for element in sequence:
                if element is anchor_element:
                    augmented.append(range_start)
                    augmented.append(element)
                    augmented.append(range_end)
                    augmented.append(self._comment_reference_run(comment_id))
                else:
                    augmented.append(element)
            sequence = augmented

        for offset, element in enumerate(sequence):
            parent.insert(start_child_index + offset, element)
        return {
            "applied": True,
            "global_start": global_start,
            "revision_ids": revision_ids,
            "comment_id": comment_id,
            "format_coalesced": start_run != end_run,
        }

    def _locate_safe_run_span(
        self,
        paragraph: etree._Element,
        target: str,
        *,
        start_hint: int | None,
    ) -> tuple[list[dict[str, Any]], int, int, int] | None:
        groups: list[tuple[list[dict[str, Any]], int]] = []
        current: list[dict[str, Any]] = []
        current_global = 0
        group_start = 0
        for child in list(paragraph):
            visible = _child_text(child)
            is_safe_run = child.tag == qn("w", "r") and self._safe_plain_run(child)
            if is_safe_run:
                if not current:
                    group_start = current_global
                current.append({"run": child, "text": visible})
            else:
                if current:
                    groups.append((current, group_start))
                    current = []
            current_global += len(visible)
        if current:
            groups.append((current, group_start))

        candidates: list[tuple[int, list[dict[str, Any]], int, int]] = []
        for group, base in groups:
            group_text = "".join(item["text"] for item in group)
            cursor = 0
            while True:
                position = group_text.find(target, cursor)
                if position < 0:
                    break
                candidates.append((base + position, group, position, position + len(target)))
                cursor = position + max(1, len(target))
        if not candidates:
            return None
        if start_hint is None:
            selected = candidates[0]
        else:
            selected = min(candidates, key=lambda item: abs(item[0] - start_hint))
        global_start, group, local_start, local_end = selected
        return group, local_start, local_end, global_start

    @staticmethod
    def _safe_plain_run(run: etree._Element) -> bool:
        allowed = {qn("w", "rPr"), qn("w", "t")}
        children = list(run)
        if any(child.tag not in allowed for child in children):
            return False
        texts = run.findall(qn("w", "t"))
        return bool(texts)

    @staticmethod
    def _locate_offset(group: Sequence[dict[str, Any]], offset: int, *, end: bool = False) -> tuple[int, int]:
        cursor = 0
        for index, item in enumerate(group):
            length = len(item["text"])
            boundary = cursor + length
            if offset < boundary or (end and offset == boundary and index == len(group) - 1):
                return index, max(0, offset - cursor)
            if end and offset == boundary:
                return index, length
            cursor = boundary
        return len(group) - 1, len(group[-1]["text"])

    def tag_text(self, paragraph: etree._Element, target: str, *, tag: str, alias: str) -> bool:
        match = self._locate_safe_run_span(paragraph, target, start_hint=None)
        if match is None:
            return False
        group, local_start, local_end, _ = match
        start_run, start_offset = self._locate_offset(group, local_start)
        end_run, end_offset = self._locate_offset(group, local_end, end=True)
        if start_run != end_run:
            return False
        run = group[start_run]["run"]
        rpr = _clone_rpr(run)
        text = group[start_run]["text"]
        before, middle, after = text[:start_offset], text[start_offset:end_offset], text[end_offset:]
        parent = paragraph
        index = parent.index(run)
        parent.remove(run)
        sequence: list[etree._Element] = []
        if before:
            sequence.append(_new_run(before, rpr))
        sdt = etree.Element(qn("w", "sdt"))
        sdt_pr = etree.SubElement(sdt, qn("w", "sdtPr"))
        alias_node = etree.SubElement(sdt_pr, qn("w", "alias"))
        alias_node.set(qn("w", "val"), alias)
        tag_node = etree.SubElement(sdt_pr, qn("w", "tag"))
        tag_node.set(qn("w", "val"), tag)
        id_node = etree.SubElement(sdt_pr, qn("w", "id"))
        id_node.set(qn("w", "val"), str(self.next_sdt_id()))
        content = etree.SubElement(sdt, qn("w", "sdtContent"))
        content.append(_new_run(middle, rpr))
        sequence.append(sdt)
        if after:
            sequence.append(_new_run(after, rpr))
        for offset, element in enumerate(sequence):
            parent.insert(index + offset, element)
        return True

    def add_bookmark(self, paragraph: etree._Element, target: str, *, name: str) -> bool:
        match = self._locate_safe_run_span(paragraph, target, start_hint=None)
        if match is None:
            return False
        group, local_start, local_end, _ = match
        start_run, start_offset = self._locate_offset(group, local_start)
        end_run, end_offset = self._locate_offset(group, local_end, end=True)
        if start_run != end_run:
            return False
        run = group[start_run]["run"]
        rpr = _clone_rpr(run)
        text = group[start_run]["text"]
        before, middle, after = text[:start_offset], text[start_offset:end_offset], text[end_offset:]
        parent = paragraph
        index = parent.index(run)
        parent.remove(run)
        bookmark_id = self.next_bookmark_id()
        start = etree.Element(qn("w", "bookmarkStart"))
        start.set(qn("w", "id"), str(bookmark_id))
        start.set(qn("w", "name"), name)
        end = etree.Element(qn("w", "bookmarkEnd"))
        end.set(qn("w", "id"), str(bookmark_id))
        sequence: list[etree._Element] = []
        if before:
            sequence.append(_new_run(before, rpr))
        sequence.extend([start, _new_run(middle, rpr), end])
        if after:
            sequence.append(_new_run(after, rpr))
        for offset, element in enumerate(sequence):
            parent.insert(index + offset, element)
        return True

    def replace_marker_with_ref_field(
        self,
        paragraph: etree._Element,
        marker: str,
        *,
        bookmark: str,
        display_text: str,
    ) -> bool:
        match = self._locate_safe_run_span(paragraph, marker, start_hint=None)
        if match is None:
            return False
        group, local_start, local_end, _ = match
        start_run, start_offset = self._locate_offset(group, local_start)
        end_run, end_offset = self._locate_offset(group, local_end, end=True)
        if start_run != end_run:
            return False
        run = group[start_run]["run"]
        rpr = _clone_rpr(run)
        text = group[start_run]["text"]
        before, after = text[:start_offset], text[end_offset:]
        parent = paragraph
        index = parent.index(run)
        parent.remove(run)
        sequence: list[etree._Element] = []
        if before:
            sequence.append(_new_run(before, rpr))
        field = etree.Element(qn("w", "fldSimple"))
        field.set(qn("w", "instr"), f" REF {bookmark} \\h ")
        field.append(_new_run(display_text, rpr))
        sequence.append(field)
        if after:
            sequence.append(_new_run(after, rpr))
        for offset, element in enumerate(sequence):
            parent.insert(index + offset, element)
        return True

    def write_qc_custom_xml(
        self,
        issues: Sequence[Issue],
        *,
        profile: ManuscriptProfile,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        root = etree.Element(qn("awe", "qualityControl"), nsmap={"awe": NS["awe"]})
        root.set("schemaVersion", "1.0.0")
        root.set("engine", "Auctor Academic Writing Engine")
        root.set("generatedAt", datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        profile_node = etree.SubElement(root, qn("awe", "profile"))
        for key, value in profile.to_dict().items():
            if isinstance(value, (str, int, float, bool)):
                profile_node.set(key, str(value).lower() if isinstance(value, bool) else str(value))
        if metadata:
            metadata_node = etree.SubElement(root, qn("awe", "metadata"))
            metadata_node.text = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        for issue in issues:
            node = etree.SubElement(root, qn("awe", "issue"))
            node.set("code", issue.code)
            node.set("severity", issue.severity)
            node.set("channel", issue.channel)
            node.set("confidence", f"{issue.confidence:.4f}")
            title = etree.SubElement(node, qn("awe", "title"))
            title.text = _sanitize_generated_text(issue.title)
            message = etree.SubElement(node, qn("awe", "message"))
            message.text = _sanitize_generated_text(issue.message)
            evidence = etree.SubElement(node, qn("awe", "evidence"))
            evidence.text = _sanitize_generated_text(issue.evidence)
            action = etree.SubElement(node, qn("awe", "action"))
            action.text = _sanitize_generated_text(issue.action)
            anchor = etree.SubElement(node, qn("awe", "anchor"))
            if issue.anchor.paragraph_index is not None:
                anchor.set("paragraph", str(issue.anchor.paragraph_index))
            if issue.anchor.start is not None:
                anchor.set("start", str(issue.anchor.start))
            if issue.anchor.end is not None:
                anchor.set("end", str(issue.anchor.end))
            anchor.set("section", issue.anchor.section)
        part_name = "customXml/auctor_qc.xml"
        self.parts[part_name] = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
        self.ensure_default_content_type("xml", "application/xml")
        self.ensure_relationship("word/_rels/document.xml.rels", REL_CUSTOM_XML, "../customXml/auctor_qc.xml")

    def resolve_tracked_changes(self, mode: str) -> dict[str, int]:
        if mode not in {"accept", "reject"}:
            raise ValueError("mode must be accept or reject")
        accepted_or_rejected = {"insertions": 0, "deletions": 0}
        parts = [
            name
            for name in self.parts
            if name == "word/document.xml"
            or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
            or name in {"word/footnotes.xml", "word/endnotes.xml"}
        ]
        for part in parts:
            root = self.xml(part)
            nodes = list(root.xpath("//w:ins | //w:del", namespaces=NS))
            for node in reversed(nodes):
                parent = node.getparent()
                if parent is None:
                    continue
                index = parent.index(node)
                if node.tag == qn("w", "ins"):
                    accepted_or_rejected["insertions"] += 1
                    if mode == "accept":
                        for child in list(node):
                            node.remove(child)
                            parent.insert(index, child)
                            index += 1
                    parent.remove(node)
                else:
                    accepted_or_rejected["deletions"] += 1
                    if mode == "reject":
                        for child in list(node):
                            node.remove(child)
                            for text_node in child.xpath(".//w:delText", namespaces=NS):
                                text_node.tag = qn("w", "t")
                            parent.insert(index, child)
                            index += 1
                    parent.remove(node)
            self.set_xml(part, root)
        if "word/settings.xml" in self.parts:
            settings = self.xml("word/settings.xml")
            track = settings.find(qn("w", "trackRevisions"))
            if track is not None:
                settings.remove(track)
            self.set_xml("word/settings.xml", settings)
        return accepted_or_rejected

    def remove_comments(self) -> int:
        count = 0
        if "word/comments.xml" in self.parts:
            root = self.xml("word/comments.xml")
            count = len(root.findall(qn("w", "comment")))
        for part in [name for name in self.parts if name.startswith("word/") and name.endswith(".xml")]:
            root = self.xml(part)
            changed = False
            for node in list(root.xpath("//w:commentRangeStart | //w:commentRangeEnd | //w:commentReference", namespaces=NS)):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                    changed = True
            if changed:
                self.set_xml(part, root)
        for name in list(self.parts):
            if name in {"word/comments.xml", "word/commentsExtended.xml", "word/commentsIds.xml", "word/people.xml"}:
                del self.parts[name]
        if "word/_rels/document.xml.rels" in self.parts:
            rels = self.xml("word/_rels/document.xml.rels")
            for rel in list(rels.findall(qn("pr", "Relationship"))):
                if rel.get("Type") in {
                    REL_COMMENTS,
                    "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
                    "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
                    "http://schemas.microsoft.com/office/2011/relationships/people",
                }:
                    rels.remove(rel)
            self.set_xml("word/_rels/document.xml.rels", rels)
        types = self.xml("[Content_Types].xml")
        comment_parts = {"/word/comments.xml", "/word/commentsExtended.xml", "/word/commentsIds.xml", "/word/people.xml"}
        for node in list(types.findall(qn("ct", "Override"))):
            if node.get("PartName") in comment_parts:
                types.remove(node)
        self.set_xml("[Content_Types].xml", types)
        return count

    def count_em_dashes(self) -> int:
        count = 0
        for name, data in self.parts.items():
            if name.endswith((".xml", ".rels")):
                text = data.decode("utf-8", errors="ignore")
                count += len(EM_DASH_RE.findall(text))
        return count

    def replace_em_dashes_in_nonrevision_parts(self) -> int:
        changed = 0
        for name, data in list(self.parts.items()):
            if not name.endswith((".xml", ".rels")):
                continue
            text = data.decode("utf-8", errors="ignore")
            matches = len(EM_DASH_RE.findall(text))
            if matches:
                self.parts[name] = EM_DASH_RE.sub(" - ", text).encode("utf-8")
                changed += matches
        return changed

    def validate(self, profile: ManuscriptProfile) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(name: str, passed: bool, message: str, *, severity: str = "error") -> None:
            checks.append({"name": name, "passed": bool(passed), "message": message, "severity": severity})

        xml_errors: list[str] = []
        for name, data in self.parts.items():
            if not name.endswith((".xml", ".rels")):
                continue
            try:
                etree.fromstring(data, parser=XML_PARSER)
            except Exception as exc:
                xml_errors.append(f"{name}: {exc}")
        add("all_xml_parts_parse", not xml_errors, "; ".join(xml_errors) if xml_errors else "All XML and relationship parts parse.")

        dash_count = self.count_em_dashes()
        add("zero_em_dash", dash_count == 0, f"Em dash character count: {dash_count}.")

        document = self.document_root()
        body_font_errors: list[str] = []
        table_font_errors: list[str] = []
        for index, run in enumerate(document.xpath("//w:r[w:t or w:delText]", namespaces=NS)):
            in_table = bool(run.xpath("ancestor::w:tbl", namespaces=NS))
            expected_font = profile.table_font if in_table else profile.body_font
            expected_size = str(int(round((profile.table_size_pt if in_table else profile.body_size_pt) * 2)))
            rfonts = run.find("./w:rPr/w:rFonts", namespaces=NS)
            sz = run.find("./w:rPr/w:sz", namespaces=NS)
            actual_font = rfonts.get(qn("w", "ascii")) if rfonts is not None else None
            actual_size = sz.get(qn("w", "val")) if sz is not None else None
            if actual_font != expected_font or actual_size != expected_size:
                item = f"run {index}: font={actual_font}, size={actual_size}, expected={expected_font}/{expected_size}"
                (table_font_errors if in_table else body_font_errors).append(item)
        add(
            "body_font_times_new_roman_11",
            not body_font_errors,
            "All body runs use Times New Roman 11 point." if not body_font_errors else "; ".join(body_font_errors[:20]),
        )
        add(
            "table_font_times_new_roman_8",
            not table_font_errors,
            "All table runs use Times New Roman 8 point." if not table_font_errors else "; ".join(table_font_errors[:20]),
        )

        deletions = document.xpath("//w:del", namespaces=NS)
        invalid_del = [node for node in deletions if not node.xpath(".//w:delText", namespaces=NS) or node.xpath(".//w:t", namespaces=NS)]
        add("tracked_deletions_use_delText", not invalid_del, f"Invalid tracked deletion elements: {len(invalid_del)}.")
        revisions = document.xpath("//w:ins | //w:del", namespaces=NS)
        ids = [node.get(qn("w", "id")) for node in revisions]
        duplicate_ids = sorted({value for value in ids if value is not None and ids.count(value) > 1})
        add("revision_ids_unique", not duplicate_ids, f"Duplicate revision IDs: {duplicate_ids or 'none'}.")
        settings_ok = True
        if revisions:
            if "word/settings.xml" not in self.parts:
                settings_ok = False
            else:
                settings = self.xml("word/settings.xml")
                settings_ok = settings.find(qn("w", "trackRevisions")) is not None
        add("track_revisions_enabled_when_needed", settings_ok, "Track revisions setting is present when revisions exist.")

        comments_ok = True
        comments_message = "No comments part present."
        if "word/comments.xml" in self.parts:
            comments_root = self.xml("word/comments.xml")
            comment_ids = {node.get(qn("w", "id")) for node in comments_root.findall(qn("w", "comment"))}
            starts = {node.get(qn("w", "id")) for node in document.xpath("//w:commentRangeStart", namespaces=NS)}
            ends = {node.get(qn("w", "id")) for node in document.xpath("//w:commentRangeEnd", namespaces=NS)}
            refs = {node.get(qn("w", "id")) for node in document.xpath("//w:commentReference", namespaces=NS)}
            missing = sorted(comment_ids - starts | comment_ids - ends | comment_ids - refs)
            rels = self.xml("word/_rels/document.xml.rels") if "word/_rels/document.xml.rels" in self.parts else None
            rel_ok = bool(rels is not None and rels.xpath("./pr:Relationship[@Type=$t]", namespaces=NS, t=REL_COMMENTS))
            types = self.xml("[Content_Types].xml")
            type_ok = bool(types.xpath("./ct:Override[@PartName='/word/comments.xml']", namespaces=NS))
            comments_ok = not missing and rel_ok and type_ok
            comments_message = f"Missing anchors: {missing or 'none'}; relationship: {rel_ok}; content type: {type_ok}."
        add("comments_plumbing", comments_ok, comments_message)

        semantic_state = self.semantic_reference_state()
        add(
            "semantic_citation_keys_resolve",
            not semantic_state["orphan_citation_keys"],
            f"Orphan citation keys: {semantic_state['orphan_citation_keys'] or 'none'}.",
        )
        add(
            "semantic_reference_keys_unique",
            not semantic_state["duplicate_reference_keys"],
            f"Duplicate reference keys: {semantic_state['duplicate_reference_keys'] or 'none'}.",
        )
        add(
            "semantic_tags_well_formed",
            not semantic_state["invalid_semantic_tags"],
            f"Invalid semantic tags: {semantic_state['invalid_semantic_tags'] or 'none'}.",
        )
        add(
            "bookmark_names_unique",
            not semantic_state["duplicate_bookmark_names"],
            f"Duplicate bookmark names: {semantic_state['duplicate_bookmark_names'] or 'none'}.",
        )
        add(
            "ref_field_targets_resolve",
            not semantic_state["missing_ref_targets"],
            f"Missing REF targets: {semantic_state['missing_ref_targets'] or 'none'}.",
        )
        add(
            "semantic_references_are_cited",
            not semantic_state["uncited_reference_keys"],
            f"Uncited semantic reference keys: {semantic_state['uncited_reference_keys'] or 'none'}.",
            severity="warning",
        )

        relationship_errors = self._relationship_target_errors()
        add(
            "internal_relationship_targets_resolve",
            not relationship_errors,
            "; ".join(relationship_errors[:20]) if relationship_errors else "All internal relationship targets resolve.",
        )

        field_update = False
        if "word/settings.xml" in self.parts:
            settings = self.xml("word/settings.xml")
            node = settings.find(qn("w", "updateFields"))
            field_update = node is not None and node.get(qn("w", "val"), "true") != "false"
        add("field_update_requested", field_update, "Word fields are set to update when the document opens.")

        error_failures = [check for check in checks if not check["passed"] and check["severity"] == "error"]
        return {
            "valid": not error_failures,
            "checks": checks,
            "inventory": self.inventory(),
        }

    def _relationship_target_errors(self) -> list[str]:
        errors: list[str] = []
        for rels_name in [name for name in self.parts if name.endswith(".rels")]:
            root = self.xml(rels_name)
            if rels_name == "_rels/.rels":
                source_dir = ""
            elif "/_rels/" in rels_name:
                prefix, rel_filename = rels_name.split("/_rels/", 1)
                source_name = rel_filename[:-5] if rel_filename.endswith(".rels") else rel_filename
                source_dir = prefix
                _ = source_name
            else:
                source_dir = posixpath.dirname(rels_name)
            for rel in root.findall(qn("pr", "Relationship")):
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target", "")
                resolved = posixpath.normpath(posixpath.join(source_dir, target)).lstrip("/")
                if resolved not in self.parts:
                    errors.append(f"{rels_name} -> {target} ({resolved})")
        return errors
