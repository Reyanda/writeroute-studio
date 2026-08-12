import fitz
import os
import json
import base64
from typing import Dict, List, Any, Optional

class TracerUnbundler:
    """
    Decomposes PDF documents into vector drawing primitives, text spans,
    font metrics, line rects, and bitmap image layers.
    """
    
    def __init__(self, pdf_path: str):
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found at {pdf_path}")
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)

    def unbundle_document(self) -> Dict[str, Any]:
        """
        Unbundles all pages in the PDF document into a structured JSON-serializable representation.
        """
        pages_data = []
        for i in range(len(self.doc)):
            page_info = self.unbundle_page(i)
            pages_data.append(page_info)

        return {
            "file_name": os.path.basename(self.pdf_path),
            "file_path": os.path.abspath(self.pdf_path),
            "page_count": len(self.doc),
            "metadata": self.doc.metadata,
            "pages": pages_data
        }

    def unbundle_page(self, page_index: int) -> Dict[str, Any]:
        """
        Unbundles a single page into vector primitives, text runs, and image assets.
        """
        page = self.doc[page_index]
        rect = page.rect
        page_width = rect.width
        page_height = rect.height

        # 1. Extract Vector Drawings
        drawings = page.get_drawings()
        vector_primitives = []
        drawing_segments = []
        drawing_rectangles = []
        curve_bounds = []

        def point_xy(point):
            return [round(float(point.x), 3), round(float(point.y), 3)]

        def append_segment(drawing_id, item_index, start, end, source_type):
            drawing_segments.append({
                "id": f"{drawing_id}_s{item_index}_{len(drawing_segments)}",
                "drawing_id": drawing_id,
                "start": point_xy(start),
                "end": point_xy(end),
                "source_type": source_type,
            })
        for idx, d in enumerate(drawings):
            d_rect = d.get("rect")
            rect_coords = [d_rect.x0, d_rect.y0, d_rect.x1, d_rect.y1] if d_rect else [0, 0, 0, 0]
            
            # Identify primitive geometry type
            items = d.get("items", [])
            item_types = [item[0] for item in items]
            
            # Check if this drawing represents a horizontal form line
            w = rect_coords[2] - rect_coords[0]
            h = rect_coords[3] - rect_coords[1]
            is_horizontal_line = (h <= 3.0 and w >= 15.0)
            is_comb_candidate = (0.7 <= (w / (h or 1.0)) <= 1.3 and 8.0 <= w <= 40.0)

            drawing_id = f"p{page_index+1}_v{idx}"
            for item_index, item in enumerate(items):
                item_type = item[0]
                if item_type == "l" and len(item) >= 3:
                    append_segment(drawing_id, item_index, item[1], item[2], "line")
                elif item_type == "re" and len(item) >= 2:
                    item_rect = item[1]
                    points = [item_rect.tl, item_rect.tr, item_rect.br, item_rect.bl]
                    for edge_index in range(4):
                        append_segment(drawing_id, f"{item_index}_{edge_index}", points[edge_index], points[(edge_index + 1) % 4], "rectangle")
                    drawing_rectangles.append({
                        "id": f"{drawing_id}_r{item_index}",
                        "drawing_id": drawing_id,
                        "rect": [round(item_rect.x0, 3), round(item_rect.y0, 3), round(item_rect.x1, 3), round(item_rect.y1, 3)],
                    })
                elif item_type == "qu" and len(item) >= 2:
                    quad = item[1]
                    points = [quad.ul, quad.ur, quad.lr, quad.ll]
                    for edge_index in range(4):
                        append_segment(drawing_id, f"{item_index}_{edge_index}", points[edge_index], points[(edge_index + 1) % 4], "quad")
                elif item_type == "c" and len(item) >= 5:
                    xs = [float(point.x) for point in item[1:5]]
                    ys = [float(point.y) for point in item[1:5]]
                    curve_bounds.append({
                        "id": f"{drawing_id}_c{item_index}",
                        "drawing_id": drawing_id,
                        "rect": [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)],
                    })

            vector_primitives.append({
                "id": drawing_id,
                "rect": rect_coords,
                "width": round(w, 2),
                "height": round(h, 2),
                "fill": d.get("fill"),
                "color": d.get("color"),
                "line_width": d.get("width", 1.0),
                "item_types": item_types,
                "is_horizontal_line": is_horizontal_line,
                "is_comb_candidate": is_comb_candidate
            })

        # 2. Extract Text Spans & Baselines
        text_page = page.get_text("dict")
        text_spans = []
        span_counter = 0
        for block in text_page.get("blocks", []):
            if block.get("type") == 0:  # Text block
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        bbox = span.get("bbox", [0, 0, 0, 0])
                        origin = span.get("origin", [bbox[0], bbox[3]])  # (x, baseline_y)
                        text_spans.append({
                            "id": f"p{page_index+1}_t{span_counter}",
                            "text": span.get("text", ""),
                            "bbox": [round(c, 2) for c in bbox],
                            "origin": [round(c, 2) for c in origin],
                            "font": span.get("font", "Unknown"),
                            "size": round(span.get("size", 10.0), 2),
                            "color": span.get("color", 0),
                            "flags": span.get("flags", 0)
                        })
                        span_counter += 1

        # 3. Extract Form Widgets (if AcroForm present)
        widgets = []
        for idx, w in enumerate(page.widgets()):
            w_rect = w.rect
            widgets.append({
                "id": f"p{page_index+1}_w{idx}",
                "name": w.field_name,
                "type": w.field_type_string,
                "value": w.field_value,
                "rect": [round(w_rect.x0, 2), round(w_rect.y0, 2), round(w_rect.x1, 2), round(w_rect.y1, 2)]
            })

        # 4. Check if Scanned Image Page
        images = page.get_images()
        is_scanned = (len(text_spans) == 0 and len(images) > 0)

        return {
            "page_number": page_index + 1,
            "width": round(page_width, 2),
            "height": round(page_height, 2),
            "is_scanned": is_scanned,
            "vector_count": len(vector_primitives),
            "text_span_count": len(text_spans),
            "widget_count": len(widgets),
            "image_count": len(images),
            "vector_primitives": vector_primitives,
            "drawing_segments": drawing_segments,
            "drawing_rectangles": drawing_rectangles,
            "curve_bounds": curve_bounds,
            "text_spans": text_spans,
            "widgets": widgets
        }

    def close(self):
        if self.doc:
            self.doc.close()
