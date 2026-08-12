"""Shared bounded SVG validation for paste, import, API, and project flows."""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET


def _parse_length(value: str | None) -> float | None:
    if not value or "%" in value:
        return None
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", value)
    return float(match.group(1)) if match else None


def validate_svg_code(svg_code: str, *, max_bytes: int = 5_000_000) -> tuple[str, int, int, int]:
    """Validate and normalize a self-contained, non-active SVG document."""
    if not svg_code or not svg_code.strip():
        raise ValueError("Paste or import SVG markup before rendering.")
    if len(svg_code.encode("utf-8")) > max_bytes:
        raise ValueError(f"SVG exceeds the {max_bytes / 1_000_000:.0f} MB safety limit.")
    if re.search(r"<!DOCTYPE|<!ENTITY", svg_code, flags=re.IGNORECASE):
        raise ValueError("DTD and entity declarations are not allowed in SVG documents.")

    try:
        root = ET.fromstring(svg_code)
    except ET.ParseError as exc:
        raise ValueError(f"SVG markup is not valid XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("The document must have an <svg> root element.")

    forbidden = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video"}
    external = re.compile(
        r"(?:javascript:|file:|https?://|@import|url\(\s*['\"]?\s*(?:javascript:|file:|https?:))",
        flags=re.IGNORECASE,
    )
    element_count = 0
    for element in root.iter():
        element_count += 1
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name in forbidden:
            raise ValueError(f"Active <{local_name}> content is not allowed in SVG documents.")
        if element.text and external.search(element.text):
            raise ValueError("External stylesheet or resource references are not allowed.")
        for attribute, value in element.attrib.items():
            local_attribute = attribute.rsplit("}", 1)[-1].lower()
            if local_attribute.startswith("on"):
                raise ValueError(f"Event handler '{local_attribute}' is not allowed.")
            if external.search(value):
                raise ValueError("External resource references are not allowed.")

    width = _parse_length(root.attrib.get("width"))
    height = _parse_length(root.attrib.get("height"))
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if (width is None or height is None) and view_box:
        try:
            _, _, view_width, view_height = [
                float(part) for part in re.split(r"[\s,]+", view_box.strip())
            ]
            width = width or abs(view_width)
            height = height or abs(view_height)
        except (TypeError, ValueError):
            pass
    width = width or 1024.0
    height = height or 1024.0
    if width <= 0 or height <= 0:
        raise ValueError("SVG width and height must be greater than zero.")
    if max(width, height) > 32_768:
        raise ValueError("SVG canvas exceeds the 32,768 px safety limit.")

    preview_scale = min(1.0, 4096.0 / max(width, height))
    preview_width = max(1, int(round(width * preview_scale)))
    preview_height = max(1, int(round(height * preview_scale)))
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode"), preview_width, preview_height, element_count
