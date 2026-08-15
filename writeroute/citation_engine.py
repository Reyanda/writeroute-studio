from __future__ import annotations
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Author:
    family: str
    given: str = ""

    def display_name(self) -> str:
        if self.given:
            return f"{self.family}, {self.given[0]}."
        return self.family

    def full_name(self) -> str:
        if self.given:
            return f"{self.given} {self.family}"
        return self.family


@dataclass
class ReferenceItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cite_key: str = ""
    item_type: str = "article-journal"
    title: str = "Untitled Document"
    authors: list[Author] = field(default_factory=list)
    year: int | str = 2024
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    pmid: str = ""
    url: str = ""
    abstract: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["authors"] = [{"family": a.family, "given": a.given} for a in self.authors]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReferenceItem:
        authors_raw = data.get("authors") or []
        authors = []
        for a in authors_raw:
            if isinstance(a, dict):
                authors.append(Author(family=a.get("family", ""), given=a.get("given", "")))
            elif isinstance(a, str):
                parts = a.split(",", 1)
                if len(parts) == 2:
                    authors.append(Author(family=parts[0].strip(), given=parts[1].strip()))
                else:
                    authors.append(Author(family=a.strip()))
        
        return cls(
            id=data.get("id") or str(uuid.uuid4()),
            cite_key=data.get("cite_key") or cls._generate_cite_key(authors, data.get("year", 2024)),
            item_type=data.get("item_type", "article-journal"),
            title=data.get("title", "Untitled Document"),
            authors=authors,
            year=data.get("year", 2024),
            journal=data.get("journal") or data.get("container_title", ""),
            volume=str(data.get("volume", "")),
            issue=str(data.get("issue", "")),
            pages=str(data.get("pages", data.get("page", ""))),
            doi=data.get("doi", ""),
            pmid=data.get("pmid", ""),
            url=data.get("url", ""),
            abstract=data.get("abstract", ""),
            tags=data.get("tags") or [],
        )

    @staticmethod
    def _generate_cite_key(authors: list[Author], year: Any) -> str:
        author_part = authors[0].family.lower().replace(" ", "") if authors else "source"
        return f"{author_part}{year}"


def parse_bibtex(bibtex_str: str) -> list[ReferenceItem]:
    """Parses BibTeX string into a list of ReferenceItem instances."""
    items = []
    # Match @type{key, ...}
    entries = re.findall(r"@(\w+)\s*\{\s*([^,]+),([\s\S]*?)(?=\n\s*@|\Z)", bibtex_str)
    for entry_type, cite_key, raw_body in entries:
        body = re.sub(r"\s*\}\s*$", "", raw_body.strip())
        fields: dict[str, str] = {}
        for line in body.splitlines():
            m = re.match(r"\s*(\w+)\s*=\s*[\"{](.*?)[\"}],?", line.strip())
            if m:
                fields[m.group(1).lower()] = m.group(2).strip()

        authors = []
        if "author" in fields:
            raw_authors = fields["author"].split(" and ")
            for ra in raw_authors:
                if "," in ra:
                    f, g = ra.split(",", 1)
                    authors.append(Author(family=f.strip(), given=g.strip()))
                else:
                    parts = ra.strip().split(" ")
                    authors.append(Author(family=parts[-1], given=" ".join(parts[:-1])))


        items.append(ReferenceItem(
            id=str(uuid.uuid4()),
            cite_key=cite_key.strip(),
            item_type="article-journal" if entry_type.lower() == "article" else entry_type.lower(),
            title=fields.get("title", "Untitled"),
            authors=authors,
            year=fields.get("year", "2024"),
            journal=fields.get("journal", fields.get("booktitle", "")),
            volume=fields.get("volume", ""),
            issue=fields.get("number", fields.get("issue", "")),
            pages=fields.get("pages", ""),
            doi=fields.get("doi", ""),
            pmid=fields.get("pmid", ""),
            url=fields.get("url", ""),
            abstract=fields.get("abstract", ""),
        ))
    return items


def parse_ris(ris_str: str) -> list[ReferenceItem]:
    """Parses RIS reference format into ReferenceItem instances."""
    items = []
    records = ris_str.strip().split("ER  -")
    for rec in records:
        if not rec.strip():
            continue
        fields: dict[str, list[str]] = {}
        for line in rec.splitlines():
            line = line.strip()
            if len(line) >= 6 and line[2:6] == "  - ":
                tag = line[:2]
                val = line[6:].strip()
                fields.setdefault(tag, []).append(val)

        authors = [Author(family=a.split(",")[0].strip(), given=a.split(",")[1].strip() if "," in a else "")
                   for a in fields.get("AU", fields.get("A1", []))]

        title = " ".join(fields.get("TI", fields.get("T1", ["Untitled"])))
        year = fields.get("PY", fields.get("Y1", ["2024"]))[0][:4]
        journal = " ".join(fields.get("JO", fields.get("JF", fields.get("T2", [""]))))
        doi = fields.get("DO", [""])[0]

        items.append(ReferenceItem(
            id=str(uuid.uuid4()),
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            volume=fields.get("VL", [""])[0],
            issue=fields.get("IS", [""])[0],
            pages=f"{fields.get('SP', [''])[0]}-{fields.get('EP', [''])[0]}".strip("-"),
            doi=doi,
            pmid=fields.get("AN", [""])[0],
        ))
    return items


class CitationFormatter:
    """Formats in-text citations and bibliographies across major scientific styles."""

    @classmethod
    def format_in_text(
        cls,
        items: list[ReferenceItem],
        style: str = "apa",
        indices: list[int] | None = None,
    ) -> str:
        style = style.lower()
        if not items:
            return ""

        if style in ("vancouver", "ieee"):
            idx_list = indices or list(range(1, len(items) + 1))
            if style == "ieee":
                return ", ".join(f"[{i}]" for i in idx_list)
            # Vancouver
            if len(idx_list) > 2 and all(idx_list[i] == idx_list[i-1] + 1 for i in range(1, len(idx_list))):
                return f"[{idx_list[0]}–{idx_list[-1]}]"
            return f"[{', '.join(str(i) for i in idx_list)}]"

        if style == "nature":
            idx_list = indices or list(range(1, len(items) + 1))
            return f"<sup>{', '.join(str(i) for i in idx_list)}</sup>"

        # Author-Year styles (APA, Chicago, Harvard)
        formatted_groups = []
        for item in items:
            authors = item.authors
            year = str(item.year)
            if not authors:
                auth_str = item.title[:20] + "..." if len(item.title) > 20 else item.title
            elif len(authors) == 1:
                auth_str = authors[0].family
            elif len(authors) == 2:
                sep = "&" if style == "apa" else "and"
                auth_str = f"{authors[0].family} {sep} {authors[1].family}"
            else:
                auth_str = f"{authors[0].family} et al."

            if style == "chicago":
                formatted_groups.append(f"{auth_str} {year}")
            else:
                formatted_groups.append(f"{auth_str}, {year}")

        return f"({'; '.join(formatted_groups)})"

    @classmethod
    def format_bibliography_entry(cls, item: ReferenceItem, style: str = "apa", index: int = 1) -> str:
        style = style.lower()
        authors = item.authors
        year = str(item.year)
        title = item.title.rstrip(".")
        journal = item.journal
        vol = item.volume
        iss = item.issue
        pages = item.pages
        doi = item.doi

        if style == "apa":
            # APA 7th: Author, A. A., & Author, B. B. (Year). Title. Journal, Vol(Issue), Pages. https://doi.org/...
            if not authors:
                auth_str = title
            elif len(authors) == 1:
                auth_str = authors[0].display_name()
            elif len(authors) == 2:
                auth_str = f"{authors[0].display_name()}, & {authors[1].display_name()}"
            else:
                auth_str = ", ".join(a.display_name() for a in authors[:6])
                if len(authors) > 6:
                    auth_str += f", ... {authors[-1].display_name()}"

            vol_iss = f"<em>{vol}</em>({iss})" if vol and iss else (f"<em>{vol}</em>" if vol else "")
            page_str = f", {pages}" if pages else ""
            doi_str = f" https://doi.org/{doi}" if doi else ""
            return f"{auth_str} ({year}). {title}. <em>{journal}</em>{', ' + vol_iss if vol_iss else ''}{page_str}.{doi_str}"

        if style in ("vancouver", "nlm"):
            # Vancouver: 1. Author AA, Author BB. Title. Journal. Year;Vol(Issue):Pages.
            auth_str = ", ".join(f"{a.family} {a.given[0] if a.given else ''}".strip() for a in authors) if authors else title
            vol_iss_pages = f"{vol}{f'({iss})' if iss else ''}:{pages}" if vol and pages else (vol or pages)
            return f"{index}. {auth_str}. {title}. <em>{journal}</em>. {year};{vol_iss_pages}."

        if style == "nature":
            # Nature: 1. Author, A. & Author, B. Title. Journal Vol, Pages (Year).
            auth_str = " & ".join(a.display_name() for a in authors[:5]) if authors else title
            if len(authors) > 5:
                auth_str += " <em>et al.</em>"
            vol_pages = f"<strong>{vol}</strong>, {pages}" if vol and pages else (vol or pages)
            return f"{index}. {auth_str} {title}. <em>{journal}</em> {vol_pages} ({year})."

        if style == "ieee":
            # IEEE: [1] A. Author and B. Author, "Title," Journal, vol. X, no. Y, pp. Z, Year.
            auth_str = " and ".join(f"{a.given[0] + '. ' if a.given else ''}{a.family}" for a in authors) if authors else title
            vol_str = f", vol. {vol}" if vol else ""
            iss_str = f", no. {iss}" if iss else ""
            pp_str = f", pp. {pages}" if pages else ""
            return f"[{index}] {auth_str}, \"{title},\" <em>{journal}</em>{vol_str}{iss_str}{pp_str}, {year}."

        # Default Chicago / Generic
        auth_str = ", ".join(a.display_name() for a in authors) if authors else title
        return f"{auth_str}. {year}. \"{title}.\" <em>{journal}</em> {vol}: {pages}."


def export_library_to_bibtex(items: list[ReferenceItem]) -> str:
    """Exports reference items to a valid BibTeX file string."""
    lines = []
    for item in items:
        authors_str = " and ".join(f"{a.family}, {a.given}".strip(", ") for a in item.authors)
        t = item.item_type.lower()
        if t in ("article-journal", "journal-article"):
            t = "article"
        lines.append(f"@{t}{{{item.cite_key},")
        lines.append(f"  title = {{{item.title}}},")

        if authors_str:
            lines.append(f"  author = {{{authors_str}}},")
        lines.append(f"  year = {{{item.year}}},")
        if item.journal:
            lines.append(f"  journal = {{{item.journal}}},")
        if item.volume:
            lines.append(f"  volume = {{{item.volume}}},")
        if item.issue:
            lines.append(f"  number = {{{item.issue}}},")
        if item.pages:
            lines.append(f"  pages = {{{item.pages}}},")
        if item.doi:
            lines.append(f"  doi = {{{item.doi}}},")
        lines.append("}\n")
    return "\n".join(lines)


def export_library_to_ris(items: list[ReferenceItem]) -> str:
    """Exports reference items to an RIS file string."""
    lines = []
    for item in items:
        lines.append("TY  - JOUR")
        lines.append(f"TI  - {item.title}")
        for a in item.authors:
            lines.append(f"AU  - {a.family}, {a.given}".strip(", "))
        lines.append(f"PY  - {item.year}")
        if item.journal:
            lines.append(f"JO  - {item.journal}")
        if item.volume:
            lines.append(f"VL  - {item.volume}")
        if item.issue:
            lines.append(f"IS  - {item.issue}")
        if item.pages:
            parts = item.pages.split("-")
            lines.append(f"SP  - {parts[0]}")
            if len(parts) > 1:
                lines.append(f"EP  - {parts[1]}")
        if item.doi:
            lines.append(f"DO  - {item.doi}")
        lines.append("ER  - \n")
    return "\n".join(lines)
