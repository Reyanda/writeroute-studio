"""Tracer project container, scene graph, and deterministic SVG annotation."""

from __future__ import annotations

import copy
import io
import json
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
TRACER_PROJECT_VERSION = 1
DRAWABLE_TAGS = {
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "image",
    "use",
}
INSPECTOR_ATTRIBUTES = {
    "x",
    "y",
    "width",
    "height",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "fill",
    "stroke",
    "stroke-width",
    "opacity",
    "transform",
    "display",
    "visibility",
    "style",
    "data-tracer-role",
    "data-tracer-coverage",
    "data-tracer-compositing",
    "data-tracer-integrity",
}
ET.register_namespace("", SVG_NS)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    cleaned = "".join(character for character in value if character in "-+.0123456789eE")
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return default


@dataclass
class SceneNode:
    id: str
    name: str
    type: str
    parent_id: str | None = None
    children: list["SceneNode"] = field(default_factory=list)
    z_order: int = 0
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    blend_mode: str = "normal"
    transform: str = ""
    bounds: dict[str, float] = field(default_factory=dict)
    semantic_role: str = "vector"
    source_region: dict[str, float] = field(default_factory=dict)
    confidence: float = 1.0
    fidelity: float | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class SceneDocument:
    id: str
    name: str
    width: int
    height: int
    root: SceneNode
    version: int = TRACER_PROJECT_VERSION
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    modified_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_name: str | None = None
    source_hash: str | None = None
    output_mode: str = "pure_vector"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneDocument":
        def node_from_dict(payload: dict[str, Any]) -> SceneNode:
            values = dict(payload)
            values["children"] = [node_from_dict(child) for child in payload.get("children", [])]
            return SceneNode(**values)

        values = dict(data)
        values["root"] = node_from_dict(data["root"])
        return cls(**values)

    def iter_nodes(self) -> Iterable[SceneNode]:
        stack = [self.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def find_node(self, node_id: str) -> SceneNode | None:
        return next((node for node in self.iter_nodes() if node.id == node_id), None)


@dataclass
class SceneCommand:
    action: str
    node_id: str
    value: Any = None
    attribute: str | None = None


class SceneHistory:
    """Snapshot-backed command history for deterministic project mutations."""

    def __init__(self, document: SceneDocument, limit: int = 100):
        self.document = document
        self.limit = max(1, int(limit))
        self._undo: list[SceneDocument] = []
        self._redo: list[SceneDocument] = []

    def execute(self, command: SceneCommand) -> SceneDocument:
        self._undo.append(copy.deepcopy(self.document))
        self._undo = self._undo[-self.limit :]
        self._redo.clear()
        apply_scene_command(self.document, command)
        return self.document

    def undo(self) -> SceneDocument:
        if self._undo:
            self._redo.append(copy.deepcopy(self.document))
            self.document = self._undo.pop()
        return self.document

    def redo(self) -> SceneDocument:
        if self._redo:
            self._undo.append(copy.deepcopy(self.document))
            self.document = self._redo.pop()
        return self.document


def apply_scene_command(document: SceneDocument, command: SceneCommand) -> None:
    node = document.find_node(command.node_id)
    if node is None:
        raise KeyError(f"Unknown scene node: {command.node_id}")
    if command.action == "set_visibility":
        node.visible = bool(command.value)
    elif command.action == "set_locked":
        node.locked = bool(command.value)
    elif command.action == "set_opacity":
        node.opacity = min(1.0, max(0.0, float(command.value)))
    elif command.action == "set_attribute" and command.attribute:
        node.attributes[command.attribute] = str(command.value)
    elif command.action == "rename":
        node.name = str(command.value).strip() or node.name
    elif command.action == "reorder":
        parent = document.find_node(node.parent_id or "")
        if parent is None:
            raise ValueError("The document root cannot be reordered.")
        siblings = parent.children
        current = next(index for index, child in enumerate(siblings) if child.id == node.id)
        target = min(len(siblings) - 1, max(0, int(command.value)))
        siblings.insert(target, siblings.pop(current))
        for index, sibling in enumerate(siblings):
            sibling.z_order = index
    elif command.action == "group":
        child_ids = [str(value) for value in (command.value or [])]
        selected = [child for child in node.children if child.id in child_ids]
        if len(selected) < 2:
            raise ValueError("Grouping requires at least two sibling nodes.")
        first_index = min(node.children.index(child) for child in selected)
        node.children = [child for child in node.children if child.id not in child_ids]
        group_id = f"group-{uuid.uuid4().hex[:12]}"
        group = SceneNode(
            id=group_id,
            name="Group",
            type="Group",
            parent_id=node.id,
            children=selected,
            z_order=first_index,
            semantic_role="group",
        )
        for child in selected:
            child.parent_id = group_id
        node.children.insert(first_index, group)
        for index, sibling in enumerate(node.children):
            sibling.z_order = index
    elif command.action == "ungroup":
        if node.type != "Group" or not node.parent_id:
            raise ValueError("Only a non-root group can be ungrouped.")
        parent = document.find_node(node.parent_id)
        if parent is None:
            raise ValueError("Group parent is missing.")
        index = parent.children.index(node)
        parent.children.pop(index)
        for child in reversed(node.children):
            child.parent_id = parent.id
            parent.children.insert(index, child)
        for order, sibling in enumerate(parent.children):
            sibling.z_order = order
    else:
        raise ValueError(f"Unsupported scene command: {command.action}")
    document.modified_at = datetime.now(timezone.utc).isoformat()


def _node_type(tag: str, element: ET.Element) -> tuple[str, str]:
    role = element.get("data-tracer-role", "vector")
    if role == "residual-patch":
        return "ResidualPatch", role
    mapping = {
        "g": "Group",
        "path": "Path",
        "rect": "Primitive",
        "circle": "Primitive",
        "ellipse": "Primitive",
        "line": "Primitive",
        "polyline": "Path",
        "polygon": "Path",
        "text": "Text",
        "image": "Image",
        "use": "Symbol",
    }
    return mapping.get(tag, tag.title()), role


def _bounds(element: ET.Element) -> dict[str, float]:
    return {
        "x": _number(element.get("x"), _number(element.get("cx"))),
        "y": _number(element.get("y"), _number(element.get("cy"))),
        "width": _number(element.get("width"), _number(element.get("r")) * 2),
        "height": _number(element.get("height"), _number(element.get("r")) * 2),
    }


def annotate_svg_scene(
    svg: str,
    *,
    name: str = "Untitled",
    output_mode: str = "pure_vector",
    cluster_size: int = 250,
) -> tuple[str, SceneDocument]:
    """Attach stable selection IDs and produce a virtualizable scene graph."""
    root_element = ET.fromstring(svg)
    width = int(round(_number(root_element.get("width"))))
    height = int(round(_number(root_element.get("height"))))
    view_box = root_element.get("viewBox", "").replace(",", " ").split()
    if (width <= 0 or height <= 0) and len(view_box) == 4:
        width = width or int(round(_number(view_box[2], 1)))
        height = height or int(round(_number(view_box[3], 1)))
    width, height = max(1, width), max(1, height)

    sequence = 0

    def walk(element: ET.Element, parent_id: str | None, z_order: int) -> SceneNode | None:
        nonlocal sequence
        tag = _local_name(element.tag)
        if tag not in DRAWABLE_TAGS:
            return None
        sequence += 1
        node_id = element.get("data-tracer-id") or f"node-{sequence:06d}"
        element.set("data-tracer-id", node_id)
        kind, role = _node_type(tag, element)
        visible = element.get("display") != "none" and element.get("visibility") != "hidden"
        attributes = {
            key: value
            for key, value in element.attrib.items()
            if key in INSPECTOR_ATTRIBUTES
        }
        node = SceneNode(
            id=node_id,
            name=element.get("aria-label") or element.get("id") or f"{kind} {sequence}",
            type=kind,
            parent_id=parent_id,
            z_order=z_order,
            visible=visible,
            opacity=min(1.0, max(0.0, _number(element.get("opacity"), 1.0))),
            transform=element.get("transform", ""),
            bounds=_bounds(element),
            semantic_role=role,
            confidence=1.0 if role == "vector" else 0.75,
            attributes=attributes,
        )
        # The exact codec layer is machine-generated parity payload, not
        # authored artwork. Exposing one compound path per source colour would
        # flood the scene tree and add a selection id to every element for no
        # editing benefit, so it is presented as a single opaque layer.
        if element.get("data-tracer-role") in {"exact-residual", "exact-cutout"}:
            return node
        for child_index, child in enumerate(list(element)):
            child_node = walk(child, node_id, child_index)
            if child_node is not None:
                node.children.append(child_node)
        return node

    top_level: list[SceneNode] = []
    for index, child in enumerate(list(root_element)):
        child_node = walk(child, "artboard-1", index)
        if child_node is not None:
            top_level.append(child_node)

    if len(top_level) > cluster_size:
        clustered: list[SceneNode] = []
        for start in range(0, len(top_level), cluster_size):
            region_nodes = top_level[start : start + cluster_size]
            region_id = f"region-{start // cluster_size + 1:03d}"
            for child in region_nodes:
                child.parent_id = region_id
            clustered.append(
                SceneNode(
                    id=region_id,
                    name=f"Vector region {start // cluster_size + 1}",
                    type="Group",
                    parent_id="artboard-1",
                    children=region_nodes,
                    z_order=start // cluster_size,
                    semantic_role="region",
                )
            )
        top_level = clustered

    artboard = SceneNode(
        id="artboard-1",
        name="Artboard 1",
        type="Artboard",
        parent_id="document-root",
        children=top_level,
        bounds={"x": 0.0, "y": 0.0, "width": float(width), "height": float(height)},
        semantic_role="artboard",
    )
    document = SceneDocument(
        id=str(uuid.uuid4()),
        name=name,
        width=width,
        height=height,
        root=SceneNode(
            id="document-root",
            name=name,
            type="Document",
            children=[artboard],
            semantic_role="document",
        ),
        output_mode=output_mode,
    )
    return ET.tostring(root_element, encoding="unicode"), document


def _image_bytes(image: Image.Image | bytes | None) -> bytes | None:
    if image is None or isinstance(image, bytes):
        return image
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def create_project_archive(
    document: SceneDocument,
    svg: str,
    *,
    source: Image.Image | bytes | None = None,
    preview: Image.Image | bytes | None = None,
) -> bytes:
    """Create an in-memory `.tracer` project without extracting user paths."""
    manifest = {
        "format": "tracer-project",
        "version": TRACER_PROJECT_VERSION,
        "document_id": document.id,
        "created_at": document.created_at,
        "modified_at": document.modified_at,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        archive.writestr("document.json", json.dumps(document.to_dict(), indent=2, sort_keys=True))
        archive.writestr("artwork.svg", svg)
        source_bytes = _image_bytes(source)
        preview_bytes = _image_bytes(preview)
        if source_bytes:
            archive.writestr("source.png", source_bytes)
        if preview_bytes:
            archive.writestr("preview.png", preview_bytes)
    return output.getvalue()


def open_project_archive(payload: bytes | str | Path) -> dict[str, Any]:
    """Read and validate a `.tracer` container without writing archive members."""
    raw = Path(payload).read_bytes() if isinstance(payload, (str, Path)) else payload
    if len(raw) > 128_000_000:
        raise ValueError("Tracer project exceeds the 128 MB safety limit.")
    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        names = set(archive.namelist())
        required = {"manifest.json", "document.json", "artwork.svg"}
        if not required.issubset(names):
            raise ValueError("Tracer project is missing required document members.")
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            raise ValueError("Tracer project contains an unsafe member path.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "tracer-project":
            raise ValueError("Unsupported project container.")
        if int(manifest.get("version", 0)) > TRACER_PROJECT_VERSION:
            raise ValueError("This project was created by a newer Tracer version.")
        document = SceneDocument.from_dict(json.loads(archive.read("document.json")))
        return {
            "manifest": manifest,
            "document": document,
            "svg": archive.read("artwork.svg").decode("utf-8"),
            "source": archive.read("source.png") if "source.png" in names else None,
            "preview": archive.read("preview.png") if "preview.png" in names else None,
        }
