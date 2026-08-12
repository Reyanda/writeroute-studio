import fitz
import os
import base64
from typing import Dict, List, Any, Optional

class TracerRebundler:
    """
    Tracer Precision PDF Rebundling Engine (v1.4)
    Re-composes form values into input PDF documents with sub-point precision.
    Supports line baselines, comb box centroids, mark primitives, signature image streams,
    and full font styling (color, bold/italic, alignment).
    """

    FONT_MAP = {
        "helv": "helv",
        "helvetica": "helv",
        "couri": "cour",
        "courier": "cour",
        "times": "times-roman",
        "times-roman": "times-roman"
    }

    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    FONT_CANDIDATES = {
        "helv": [
            os.path.join(PROJECT_ROOT, "stirling-pdf-repo", "app", "core", "src", "main", "resources", "static", "fonts", "LiberationSans-Regular.ttf"),
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ],
        "couri": [
            os.path.join(PROJECT_ROOT, "stirling-pdf-repo", "app", "core", "src", "main", "resources", "static", "fonts", "LiberationMono-Regular.ttf"),
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
        ],
        "times": [
            os.path.join(PROJECT_ROOT, "stirling-pdf-repo", "app", "core", "src", "main", "resources", "static", "fonts", "LiberationSerif-Regular.ttf"),
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        ],
    }

    def __init__(self, original_pdf_path: str):
        if not os.path.exists(original_pdf_path):
            raise FileNotFoundError(f"Source PDF file not found: {original_pdf_path}")
        self.original_pdf_path = original_pdf_path

    def _parse_hex_color(self, hex_str: str) -> tuple:
        """Converts '#05105a' to (r, g, b) float tuple [0.0 - 1.0]."""
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            return (r, g, b)
        return (0.02, 0.06, 0.35)  # Default Tracer Navy

    def _font_name_for_page(self, page: fitz.Page, requested_family: str) -> str:
        """Register an embedded font when available, with a Base-14 fallback."""
        family_key = requested_family.lower()
        if family_key in ("cour", "courier"):
            family_key = "couri"
        elif family_key in ("times-roman", "serif"):
            family_key = "times"
        elif family_key not in self.FONT_CANDIDATES:
            family_key = "helv"

        alias = {"helv": "tracersans", "couri": "tracermono", "times": "tracerserif"}[family_key]
        for candidate in self.FONT_CANDIDATES[family_key]:
            if os.path.isfile(candidate):
                page.insert_font(fontname=alias, fontfile=candidate)
                return alias
        return self.FONT_MAP.get(requested_family.lower(), "helv")

    def fill_and_rebundle(
        self,
        slots_data: Dict[int, List[Dict[str, Any]]],
        output_path: str,
        annotations: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Takes page slots data and renders filled text, comb box characters, vector mark primitives,
        and signature image streams into a new rebundled PDF output.
        """
        doc = fitz.open(self.original_pdf_path)
        self.unrendered: List[Dict[str, Any]] = []

        for page_num_1idx, page_slots in slots_data.items():
            page_idx = page_num_1idx - 1
            if page_idx >= len(doc):
                continue

            page = doc[page_idx]
            # Isolate legacy page graphics state before adding overlay streams. Some source PDFs
            # leave clipping or transform state open; MuPDF may still render additions while
            # stricter viewers (including Poppler) hide them unless the original content is wrapped.
            if not page.is_wrapped:
                page.wrap_contents()
            for slot in page_slots:
                val = str(slot.get("value", "") or "").strip()
                checked = slot.get("checked", False) or val.lower() in ["true", "1", "yes", "x", "✓"]

                requested = bool(val) or checked
                drawn = True
                if slot.get("slot_type") == "signature" and val:
                    self._render_signature(page, slot, val)
                elif slot.get("slot_type") == "line" and val:
                    self._render_line_text(page, slot, val)
                elif slot.get("slot_type") == "comb_box" and val:
                    self._render_comb_box(page, slot, val)
                elif slot.get("slot_type") == "cell" and val:
                    drawn = self._render_cell(page, slot, val[:1])
                elif slot.get("slot_type") == "checkbox" and checked:
                    self._render_checkbox_mark(page, slot)
                else:
                    requested = False
                if requested and not drawn:
                    # A value the caller asked for and the page did not receive. Reporting
                    # it beats a filled-looking form with empty boxes.
                    self.unrendered.append({
                        "page": page_num_1idx,
                        "id": slot.get("id"),
                        "slot_type": slot.get("slot_type"),
                        "value": val,
                    })

        for annotation in annotations or []:
            page_index = int(annotation.get("page", 1)) - 1
            if not 0 <= page_index < len(doc):
                continue
            self._render_highlight(doc[page_index], annotation)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        doc.save(output_path, garbage=4, deflate=True)
        doc.close()
        return output_path

    def _render_signature(self, page: fitz.Page, slot: Dict[str, Any], sig_data_url: str):
        """Renders base64 signature image onto PDF page rect."""
        try:
            if "," in sig_data_url:
                sig_data_url = sig_data_url.split(",")[1]
            img_bytes = base64.b64decode(sig_data_url)
            rect = fitz.Rect(slot["rect"])
            page.insert_image(rect, stream=img_bytes, overlay=True)
        except Exception as e:
            # Fallback text signature
            self._render_line_text(page, slot, f"/Signed: {slot.get('value')[:20]}/")

    def _render_line_text(self, page: fitz.Page, slot: Dict[str, Any], text: str):
        rect = slot["rect"]
        baseline_y = slot.get("baseline_y", rect[3] - 2.0)
        font_name = self._font_name_for_page(page, slot.get("font_family", "helv"))
        font_size = float(slot.get("font_size", 9.5))
        
        color_hex = slot.get("font_color", "#05105a")
        color_tuple = self._parse_hex_color(color_hex)

        align_str = slot.get("align", "left").lower()
        if align_str in ["center", "right"]:
            align_code = 1 if align_str == "center" else 2
            target_rect = fitz.Rect(rect[0], baseline_y - font_size, rect[2], baseline_y + 4.0)
            page.insert_textbox(
                target_rect,
                text,
                fontsize=font_size,
                fontname=font_name,
                color=color_tuple,
                align=align_code,
                overlay=True
            )
        else:
            x_margin = rect[0] + 2.0
            page.insert_text(
                point=(x_margin, baseline_y),
                text=text,
                fontsize=font_size,
                fontname=font_name,
                color=color_tuple,
                overlay=True
            )

    def _render_comb_box(self, page: fitz.Page, slot: Dict[str, Any], text: str):
        centroids = slot.get("box_centroids", [])
        if not centroids:
            return

        font_name = self._font_name_for_page(page, "couri")
        font_size = float(slot.get("font_size", 10.0))
        color_hex = slot.get("font_color", "#05105a")
        color_tuple = self._parse_hex_color(color_hex)
        chars = list(text)[:len(centroids)]

        for idx, char in enumerate(chars):
            cent_x, cent_y = centroids[idx]
            x_pos = cent_x - (font_size * 0.28)
            y_pos = cent_y + (font_size * 0.35)

            page.insert_text(
                point=(x_pos, y_pos),
                text=char,
                fontsize=font_size,
                fontname=font_name,
                color=color_tuple,
                overlay=True
            )

    def _render_cell(self, page: fitz.Page, slot: Dict[str, Any], text: str) -> bool:
        """Render exactly one character in one physical cell.

        Returns whether the character was actually drawn.

        insert_textbox returns the leftover vertical space and draws nothing at all when
        that is negative. The previous version discarded the return, and centring the text
        by cropping the top of the cell left it 0.05 points short on an ordinary 18-point
        cell: every character silently failed to render while the fill reported success.
        The cell is now used at full height, and the font steps down until the glyph fits.
        """
        if not text:
            return False
        rect = fitz.Rect(slot["rect"])
        font_size = float(slot.get("font_size", min(max(rect.height * 0.62, 7.0), 12.0)))
        font_name = self._font_name_for_page(page, "couri")
        color_tuple = self._parse_hex_color(slot.get("font_color", "#05105a"))

        for size in self._fitting_sizes(font_size):
            if page.insert_textbox(rect, text[:1], fontsize=size, fontname=font_name,
                                   color=color_tuple, align=1, overlay=True) >= 0:
                return True
        return False

    @staticmethod
    def _fitting_sizes(preferred: float) -> List[float]:
        """The requested size first, then progressively smaller ones down to 5pt."""
        sizes = [preferred]
        size = preferred
        while size > 5.0:
            size -= 0.5
            sizes.append(round(size, 1))
        return sizes

    def _render_highlight(self, page: fitz.Page, annotation: Dict[str, Any]):
        """Create a true PDF highlight annotation from page-space geometry."""
        rects = annotation.get("rects") or ([annotation["rect"]] if annotation.get("rect") else [])
        palette = {
            "yellow": (1.0, 0.84, 0.12),
            "blue": (0.22, 0.55, 0.96),
            "green": (0.20, 0.72, 0.42),
            "orange": (1.0, 0.55, 0.16),
            "red": (0.92, 0.24, 0.25),
        }
        color_name = str(annotation.get("color", "yellow")).lower()
        color = palette.get(color_name, palette["yellow"])
        note = str(annotation.get("note", "") or "")
        selected_text = str(annotation.get("text", "") or "")
        for rect_values in rects:
            rect = fitz.Rect(rect_values)
            if rect.is_empty or rect.is_infinite:
                continue
            annot = page.add_highlight_annot(rect)
            annot.set_colors(stroke=color)
            annot.set_opacity(0.42)
            annot.set_info(title="Tracer Review", content=note or selected_text[:500])
            annot.update()

    def _render_checkbox_mark(self, page: fitz.Page, slot: Dict[str, Any]):
        rect = slot["rect"]
        centroid = slot.get("centroid", [(rect[0]+rect[2])/2.0, (rect[1]+rect[3])/2.0])
        mark_style = slot.get("mark_style", "tick").lower()

        cx, cy = centroid[0], centroid[1]
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        box_size = min(w, h, 14.0)
        half = box_size / 2.0

        shape = page.new_shape()
        color_hex = slot.get("font_color", "#05105a")
        color_tuple = self._parse_hex_color(color_hex)

        if mark_style == "tick":
            p1 = (cx - half * 0.6, cy)
            p2 = (cx - half * 0.1, cy + half * 0.5)
            p3 = (cx + half * 0.7, cy - half * 0.6)
            shape.draw_line(p1, p2)
            shape.draw_line(p2, p3)
            shape.finish(color=color_tuple, width=1.5, stroke_opacity=1.0)
        elif mark_style == "cross":
            p1 = (cx - half * 0.5, cy - half * 0.5)
            p2 = (cx + half * 0.5, cy + half * 0.5)
            p3 = (cx + half * 0.5, cy - half * 0.5)
            p4 = (cx - half * 0.5, cy + half * 0.5)
            shape.draw_line(p1, p2)
            shape.draw_line(p3, p4)
            shape.finish(color=color_tuple, width=1.5, stroke_opacity=1.0)
        elif mark_style == "shade":
            fill_rect = fitz.Rect(cx - half * 0.6, cy - half * 0.6, cx + half * 0.6, cy + half * 0.6)
            shape.draw_rect(fill_rect)
            shape.finish(color=color_tuple, fill=color_tuple, stroke_opacity=1.0)
        elif mark_style == "dot":
            shape.draw_circle((cx, cy), half * 0.5)
            shape.finish(color=color_tuple, fill=color_tuple, stroke_opacity=1.0)

        shape.commit()
