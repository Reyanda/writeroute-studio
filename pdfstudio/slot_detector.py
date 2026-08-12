import fitz
import cv2
import numpy as np
import re
from typing import Dict, List, Any, Tuple, Optional
from pdfstudio.semantic_schema import TracerSemanticMapper

class TracerSlotDetector:
    """
    Form Slot Feature Extractor (v2.1)
    Identifies native PDF widgets, fillable solid and dotted lines, enclosed regions, atomic cells,
    text/vector checkboxes, radio-like controls, scanned contours, and signature slots. The
    renderer primitive remains in ``slot_type`` while ``field_kind`` describes the human input.
    """

    def __init__(self):
        self.semantic_mapper = TracerSemanticMapper()
        self._enclosure_cache: Dict[int, List[Dict[str, Any]]] = {}

    def detect_slots_for_page(self, page_data: Dict[str, Any], doc: Optional[fitz.Document] = None, page_index: int = 0) -> List[Dict[str, Any]]:
        """
        Main entry point for slot detection on a page.
        Combines native widgets, vector analysis, text runs, table grids, and CV detection.
        """
        page_cache_key = id(page_data)
        if getattr(self, "_active_page_cache_key", None) != page_cache_key:
            self._enclosure_cache.clear()
            self._active_page_cache_key = page_cache_key
        slots = []
        page_num = page_data["page_number"]
        page_width = page_data["width"]
        page_height = page_data["height"]

        slots.extend(self._detect_widget_slots(page_data))

        if page_data.get("is_scanned", False) and doc is not None:
            cv_slots = self._detect_cv_slots(doc[page_index], page_width, page_height, page_num)
            slots.extend(cv_slots)
        else:
            # 1. Detect Dotted & Dashed Underline Form Lines (...., …………, ____)
            dotted_slots = self._detect_dotted_line_slots(page_data)
            slots.extend(dotted_slots)

            # 2. Detect Table Cell Grid Intersections (Row & Column intersections)
            table_slots = self._detect_table_intersection_cells(page_data)
            slots.extend(table_slots)

            # 3. Detect Solid Horizontal Line Anchors
            line_slots = self._detect_line_slots(page_data, existing_slots=slots)
            slots.extend(line_slots)

            # 4. Detect physical one-character cells. Each cell is an independent field;
            # grouping metadata expresses order without merging edit or render state.
            cell_slots = self._detect_atomic_cell_slots(page_data)
            slots.extend(cell_slots)

            # 5. Detect Text Glyph Checkboxes (☐, □, [ ]) & Vector Tick Boxes (4.0pt - 22.0pt)
            checkbox_slots = self._detect_checkbox_slots(page_data)
            slots.extend(checkbox_slots)

        slots = self._deduplicate_slots(slots)
        self._label_and_index_slots(slots, page_data.get("text_spans", []), page_num)

        return slots

    def detect_slots_at_point(
        self,
        page_data: Dict[str, Any],
        x: float,
        y: float,
        doc: Optional[fitz.Document] = None,
        page_index: int = 0,
        radius: float = 36.0,
    ) -> List[Dict[str, Any]]:
        """Return the smallest detected field, or its complete local group, at a page point."""
        slots = self.detect_slots_for_page(page_data, doc, page_index)

        def area(slot):
            r = slot["rect"]
            return max(0.1, (r[2] - r[0]) * (r[3] - r[1]))

        containing = [slot for slot in slots if slot["rect"][0] - 2 <= x <= slot["rect"][2] + 2 and slot["rect"][1] - 2 <= y <= slot["rect"][3] + 2]
        if containing:
            chosen = min(containing, key=area)
        else:
            def distance(slot):
                r = slot["rect"]
                dx = max(r[0] - x, 0.0, x - r[2])
                dy = max(r[1] - y, 0.0, y - r[3])
                return (dx * dx + dy * dy) ** 0.5
            nearby = [slot for slot in slots if distance(slot) <= radius]
            if not nearby:
                return []
            chosen = min(nearby, key=lambda slot: (distance(slot), area(slot)))

        group_id = chosen.get("group_id")
        if group_id:
            return [slot for slot in slots if slot.get("group_id") == group_id]
        return [chosen]

    def _detect_widget_slots(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Promote existing AcroForm widgets to editable Tracer fields."""
        slots = []
        for widget in page_data.get("widgets", []):
            widget_type = (widget.get("type") or "Text").lower()
            rect = widget["rect"]
            width = round(rect[2] - rect[0], 2)
            height = round(rect[3] - rect[1], 2)
            field_kind = "text"
            slot_type = "line"

            if "check" in widget_type:
                field_kind, slot_type = "checkbox", "checkbox"
            elif "radio" in widget_type:
                field_kind, slot_type = "radio", "checkbox"
            elif "signature" in widget_type:
                field_kind, slot_type = "signature", "signature"
            elif "combo" in widget_type or "list" in widget_type or "choice" in widget_type:
                field_kind, slot_type = "select", "line"

            value = widget.get("value") or ""
            slot = {
                "slot_type": slot_type,
                "field_kind": field_kind,
                "rect": rect,
                "width": width,
                "height": height,
                "value": str(value),
                "label_hint": widget.get("name") or "",
                "detection_source": "native_widget",
                "detection_confidence": 0.99,
                "native_widget": True,
                "native_widget_name": widget.get("name") or "",
            }
            if slot_type == "checkbox":
                slot.update({
                    "centroid": [round((rect[0] + rect[2]) / 2.0, 2), round((rect[1] + rect[3]) / 2.0, 2)],
                    "checked": str(value).lower() not in ("", "false", "off", "0", "none"),
                    "mark_style": "dot" if field_kind == "radio" else "tick",
                })
            else:
                slot.update({
                    "baseline_y": round(rect[3] - 2.0, 2),
                    "font_size": round(min(max(height * 0.65, 7.0), 12.0), 1),
                    "align": "left",
                })
            slots.append(slot)
        return slots

    def _detect_dotted_line_slots(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extracts fillable form lines created via period/ellipsis/underscore sequences
        (e.g., 'First Names: .........................', 'Signature: _________').
        """
        slots = []
        text_spans = page_data.get("text_spans", [])

        fill_run = re.compile(r"(?:\.{4,}|…{3,}|_{4,})")
        for span in text_spans:
            t = span.get("text", "")
            match = fill_run.search(t)
            if match:
                bbox = span["bbox"]
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]

                if w >= 25.0:
                    # PyMuPDF may return the label and dotted run in one span. Approximate the
                    # run start from its character position so the editable region excludes ink.
                    run_ratio = match.start() / max(len(t), 1)
                    slot_x0 = bbox[0] + (w * run_ratio)
                    label_hint = t[:match.start()].strip(" :.-_")
                    run_width = bbox[2] - slot_x0
                    if run_width < 20.0:
                        slot_x0 = bbox[0]
                    baseline_y = round(bbox[3] - 1.5, 2)
                    is_sig = "signature" in label_hint.lower() or "sig" in label_hint.lower()
                    slot_type = "signature" if is_sig else "line"

                    slots.append({
                        "slot_type": slot_type,
                        "is_dotted_line": True,
                        "rect": [round(slot_x0, 2), round(bbox[1], 2), round(bbox[2], 2), round(bbox[3], 2)],
                        "baseline_y": baseline_y,
                        "width": round(bbox[2] - slot_x0, 2),
                        "height": round(max(h, 12.0), 2),
                        "value": "",
                        "font_size": round(span.get("size", 9.5), 1),
                        "align": "left",
                        "label_hint": label_hint,
                        "detection_source": "dotted_text_run",
                        "detection_confidence": 0.93,
                    })

        return slots

    def _detect_table_intersection_cells(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Reconstruct locally enclosed editable regions from real drawing segments. Small square
        regions are left to atomic-cell / choice classification; this method handles wider cells.
        """
        regions = [r for r in self._locally_enclosed_regions(page_data) if r["width"] >= 15.0 and r["width"] > r["height"] * 1.8]
        rows = self._cluster_rect_rows(regions, max_gap=4.0)
        grouped_ids = {}
        group_counter = 0
        for row in rows:
            if len(row) < 2:
                continue
            group_counter += 1
            group_id = f"p{page_data['page_number']}_region_group_{group_counter}"
            for index, region in enumerate(row):
                grouped_ids[tuple(region["rect"])] = (group_id, index, len(row))

        slots = []
        for region in regions:
            x0, y0, x1, y1 = region["rect"]
            slot = {
                "slot_type": "line",
                "field_kind": "table_cell",
                "is_table_cell": True,
                "boundary_rect": region["rect"],
                "rect": region["rect"],
                "baseline_y": round(y1 - 2.5, 2),
                "width": region["width"],
                "height": region["height"],
                "value": "",
                "font_size": round(min(region["height"] * 0.58, 10.0), 1),
                "align": "left",
                "detection_source": "segment_enclosure",
                "detection_confidence": 0.87,
            }
            group = grouped_ids.get(tuple(region["rect"]))
            if group:
                slot.update({"group_id": group[0], "group_index": group[1], "group_size": group[2], "group_role": "segmented_fields"})
            slots.append(slot)
        return slots

    def _axis_segments(self, page_data: Dict[str, Any]) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
        """Normalize actual drawing items into horizontal and vertical segments."""
        h_segments: List[Dict[str, float]] = []
        v_segments: List[Dict[str, float]] = []
        source = page_data.get("drawing_segments") or []
        if source:
            for segment in source:
                x0, y0 = segment["start"]
                x1, y1 = segment["end"]
                if abs(y1 - y0) <= 1.6 and abs(x1 - x0) >= 4.0:
                    h_segments.append({"coord": (y0 + y1) / 2.0, "start": min(x0, x1), "end": max(x0, x1)})
                elif abs(x1 - x0) <= 1.6 and abs(y1 - y0) >= 4.0:
                    v_segments.append({"coord": (x0 + x1) / 2.0, "start": min(y0, y1), "end": max(y0, y1)})
        else:
            for vector in page_data.get("vector_primitives", []):
                r = vector["rect"]
                width, height = r[2] - r[0], r[3] - r[1]
                if height <= 4.0 and width >= 4.0:
                    h_segments.append({"coord": (r[1] + r[3]) / 2.0, "start": r[0], "end": r[2]})
                elif width <= 4.0 and height >= 4.0:
                    v_segments.append({"coord": (r[0] + r[2]) / 2.0, "start": r[1], "end": r[3]})
        return h_segments, v_segments

    def _locally_enclosed_regions(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find rectangles only where four physical boundaries cover the local interval."""
        cache_key = id(page_data)
        if cache_key in self._enclosure_cache:
            return self._enclosure_cache[cache_key]
        h_segments, v_segments = self._axis_segments(page_data)
        if len(h_segments) < 2 or len(v_segments) < 2:
            self._enclosure_cache[cache_key] = []
            return []

        def grouped(values, tolerance=2.2):
            groups: List[List[float]] = []
            for value in sorted(values):
                if not groups or value - sum(groups[-1]) / len(groups[-1]) > tolerance:
                    groups.append([value])
                else:
                    groups[-1].append(value)
            return [round(sum(group) / len(group), 2) for group in groups]

        xs = grouped([segment["coord"] for segment in v_segments])
        ys = grouped([segment["coord"] for segment in h_segments])

        def covered(segments, coord, start, end, tolerance=2.6):
            intervals = sorted(
                (segment["start"], segment["end"])
                for segment in segments
                if abs(segment["coord"] - coord) <= tolerance and segment["end"] >= start - tolerance and segment["start"] <= end + tolerance
            )
            if not intervals:
                return False
            cursor = start
            for interval_start, interval_end in intervals:
                if interval_start > cursor + tolerance:
                    return False
                cursor = max(cursor, interval_end)
                if cursor >= end - tolerance:
                    return True
            return cursor >= end - tolerance

        regions = []
        for yi, y0 in enumerate(ys[:-1]):
            for y1 in ys[yi + 1:]:
                height = y1 - y0
                if height > 48.0:
                    break
                if height < 6.0:
                    continue
                # Only vertical boundaries that physically span this row participate. This
                # prevents unrelated page-wide X coordinates from breaking a real local grid.
                active_xs = [x for x in xs if covered(v_segments, x, y0, y1)]
                for xi in range(len(active_xs) - 1):
                    x0, x1 = active_xs[xi], active_xs[xi + 1]
                    width = x1 - x0
                    if not 5.0 <= width <= 380.0:
                        continue
                    if covered(h_segments, y0, x0, x1) and covered(h_segments, y1, x0, x1):
                        regions.append({
                            "rect": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                            "width": round(width, 2),
                            "height": round(height, 2),
                        })
        self._enclosure_cache[cache_key] = regions
        return regions

    def _cluster_rect_rows(self, regions: List[Dict[str, Any]], max_gap: float = 6.0) -> List[List[Dict[str, Any]]]:
        rows: List[List[Dict[str, Any]]] = []
        for region in sorted(regions, key=lambda item: (item["rect"][1], item["rect"][0])):
            placed = False
            for row in rows:
                reference = row[0]
                if abs(region["rect"][1] - reference["rect"][1]) <= 3.0 and abs(region["height"] - reference["height"]) <= 4.0:
                    row_sorted = sorted(row, key=lambda item: item["rect"][0])
                    if region["rect"][0] - row_sorted[-1]["rect"][2] <= max_gap:
                        row.append(region)
                        placed = True
                        break
            if not placed:
                rows.append([region])
        return [sorted(row, key=lambda item: item["rect"][0]) for row in rows]

    def _detect_line_slots(self, page_data: Dict[str, Any], existing_slots: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        slots = []
        vectors = page_data.get("vector_primitives", [])
        existing_rects = [s["rect"] for s in (existing_slots or [])]

        for v in vectors:
            rect = v["rect"]
            x0, y0, x1, y1 = rect[0], rect[1], rect[2], rect[3]
            w = x1 - x0
            h = y1 - y0

            if w >= 10.0 and h <= 4.0:
                overlaps = False
                for er in existing_rects:
                    if abs(er[1] - y0) <= 4.0 and er[0] <= x0 and er[2] >= x1:
                        overlaps = True
                        break

                if not overlaps:
                    baseline_y = y0 - 1.5
                    slots.append({
                        "slot_type": "line",
                        "rect": [round(x0, 2), round(y0 - 12.0, 2), round(x1, 2), round(y0, 2)],
                        "line_coords": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                        "baseline_y": round(baseline_y, 2),
                        "width": round(w, 2),
                        "height": 12.0,
                        "value": "",
                        "font_size": 9.5,
                        "align": "left",
                        "detection_source": "vector_line",
                        "detection_confidence": 0.72,
                    })
        return slots

    def _detect_atomic_cell_slots(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Emit one editable field per physical character cell; never emit a merged comb."""
        candidates = [
            region for region in self._locally_enclosed_regions(page_data)
            if 7.0 <= region["width"] <= 32.0 and 7.0 <= region["height"] <= 32.0
            and 0.55 <= region["width"] / max(region["height"], 0.1) <= 1.8
        ]

        # Some producers draw each box as one compound path. Preserve those candidates even
        # when their doubled stroke outline does not reconstruct cleanly from coordinate bands.
        for vector in page_data.get("vector_primitives", []):
            rect = [round(float(value), 2) for value in vector["rect"]]
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            if 7.0 <= width <= 32.0 and 7.0 <= height <= 32.0 and 0.55 <= width / max(height, 0.1) <= 1.8:
                candidates.append({"rect": rect, "width": round(width, 2), "height": round(height, 2)})

        unique = []
        for candidate in sorted(candidates, key=lambda item: (item["rect"][1], item["rect"][0], item["width"] * item["height"])):
            if any(abs(candidate["rect"][0] - other["rect"][0]) <= 2.0 and abs(candidate["rect"][1] - other["rect"][1]) <= 2.0 for other in unique):
                continue
            unique.append(candidate)

        rows = self._cluster_rect_rows(unique, max_gap=8.0)
        slots = []
        group_counter = 0
        for row in rows:
            if len(row) < 2:
                continue
            group_counter += 1
            group_id = f"p{page_data['page_number']}_cell_group_{group_counter}"
            role = self._classify_small_box_row(row, page_data.get("text_spans", []))
            if role == "independent_choices":
                continue
            for index, candidate in enumerate(row):
                rect = candidate["rect"]
                slots.append({
                    "slot_type": "cell",
                    "field_kind": "cell",
                    "rect": rect,
                    "boundary_rect": rect,
                    "centroid": [round((rect[0] + rect[2]) / 2.0, 2), round((rect[1] + rect[3]) / 2.0, 2)],
                    "baseline_y": round(rect[3] - max(2.0, candidate["height"] * 0.2), 2),
                    "width": candidate["width"],
                    "height": candidate["height"],
                    "value": "",
                    "max_length": 1,
                    "font_size": round(min(candidate["height"] * 0.62, 12.0), 1),
                    "align": "center",
                    "group_id": group_id,
                    "group_index": index,
                    "group_size": len(row),
                    "group_role": "character_sequence",
                    "detection_source": "atomic_cell_row",
                    "detection_confidence": 0.91,
                })
        return slots

    def _classify_small_box_row(self, row: List[Dict[str, Any]], text_spans: List[Dict[str, Any]]) -> str:
        """Use local labels and spacing to avoid treating adjacent choices as character cells."""
        option_words = re.compile(r"\b(check|tick|select|choose|yes|no|male|female|other|true|false|one)\b", re.I)
        row_x0 = min(item["rect"][0] for item in row)
        row_x1 = max(item["rect"][2] for item in row)
        row_y0 = min(item["rect"][1] for item in row)
        row_y1 = max(item["rect"][3] for item in row)
        nearby = []
        labels_after_boxes = 0
        for span in text_spans:
            text = span.get("text", "").strip()
            bbox = span.get("bbox", [0, 0, 0, 0])
            center_y = (bbox[1] + bbox[3]) / 2.0
            if text and row_y0 - 18 <= center_y <= row_y1 + 18 and bbox[0] <= row_x1 + 180 and bbox[2] >= row_x0 - 180:
                nearby.append(text)
                for item in row:
                    if item["rect"][2] - 1 <= bbox[0] <= item["rect"][2] + 90:
                        labels_after_boxes += 1
                        break
        combined = " ".join(nearby)
        if option_words.search(combined) or labels_after_boxes >= max(2, len(row) // 2):
            return "independent_choices"
        return "character_sequence"

    def _detect_checkbox_slots(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        slots = []
        text_spans = page_data.get("text_spans", [])

        for span in text_spans:
            t = span.get("text", "")
            if "☐" in t or "□" in t or "[]" in t or "[ ]" in t:
                bbox = span["bbox"]
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                cx = bbox[0] + w / 2.0
                cy = bbox[1] + h / 2.0

                slots.append({
                    "slot_type": "checkbox",
                    "is_text_glyph": True,
                    "rect": [round(bbox[0], 2), round(bbox[1], 2), round(bbox[2], 2), round(bbox[3], 2)],
                    "centroid": [round(cx, 2), round(cy, 2)],
                    "width": round(w, 2),
                    "height": round(h, 2),
                    "checked": False,
                    "mark_style": "tick",
                    "value": "false",
                    "detection_source": "checkbox_glyph",
                    "detection_confidence": 0.96,
                })

        vectors = page_data.get("vector_primitives", [])
        existing_rects = [s["rect"] for s in slots]

        for v in vectors:
            rect = v["rect"]
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]

            if 4.0 <= w <= 22.0 and 4.0 <= h <= 22.0 and 0.6 <= (w / (h or 1.0)) <= 1.6:
                cx = rect[0] + w / 2.0
                cy = rect[1] + h / 2.0

                is_dupe = any(abs(er[0] - rect[0]) <= 5.0 and abs(er[1] - rect[1]) <= 5.0 for er in existing_rects)
                if not is_dupe:
                    field_kind = "radio" if "c" in v.get("item_types", []) else "checkbox"
                    slots.append({
                        "slot_type": "checkbox",
                        "field_kind": field_kind,
                        "rect": [round(rect[0], 2), round(rect[1], 2), round(rect[2], 2), round(rect[3], 2)],
                        "centroid": [round(cx, 2), round(cy, 2)],
                        "width": round(w, 2),
                        "height": round(h, 2),
                        "checked": False,
                        "mark_style": "dot" if field_kind == "radio" else "tick",
                        "value": "false",
                        "detection_source": "vector_control",
                        "detection_confidence": 0.82,
                    })

        return slots

    def _detect_cv_slots(self, page: fitz.Page, page_width: float, page_height: float, page_num: int) -> List[Dict[str, Any]]:
        slots = []
        dpi = 150
        scale = 72.0 / dpi

        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n >= 3:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            gray = img.copy()

        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)

        kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (int(15 * (dpi/72)), 1))
        detect_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_h)
        cnts, _ = cv2.findContours(detect_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            pdf_x0 = round(x * scale, 2)
            pdf_y0 = round(y * scale, 2)
            pdf_w = round(w * scale, 2)
            pdf_h = round(h * scale, 2)

            if pdf_w >= 15.0 and pdf_h <= 6.0:
                baseline_y = round(pdf_y0 - 1.5, 2)
                slots.append({
                    "slot_type": "line",
                    "rect": [pdf_x0, round(pdf_y0 - 12.0, 2), round(pdf_x0 + pdf_w, 2), pdf_y0],
                    "line_coords": [pdf_x0, pdf_y0, round(pdf_x0 + pdf_w, 2), pdf_y0],
                    "baseline_y": baseline_y,
                    "width": pdf_w,
                    "height": 12.0,
                    "value": "",
                    "font_size": 9.5,
                    "align": "left",
                    "is_cv_detected": True,
                    "detection_source": "scanned_line_cv",
                    "detection_confidence": 0.64,
                })

        kernel_box = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        detect_boxes = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_box)
        cnts_b, _ = cv2.findContours(detect_boxes, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for c in cnts_b:
            x, y, w, h = cv2.boundingRect(c)
            pdf_w = round(w * scale, 2)
            pdf_h = round(h * scale, 2)
            if 5.0 <= pdf_w <= 20.0 and 5.0 <= pdf_h <= 20.0 and 0.7 <= (pdf_w/pdf_h) <= 1.4:
                pdf_x0 = round(x * scale, 2)
                pdf_y0 = round(y * scale, 2)
                slots.append({
                    "slot_type": "checkbox",
                    "rect": [pdf_x0, pdf_y0, round(pdf_x0 + pdf_w, 2), round(pdf_y0 + pdf_h, 2)],
                    "centroid": [round(pdf_x0 + pdf_w/2.0, 2), round(pdf_y0 + pdf_h/2.0, 2)],
                    "width": pdf_w,
                    "height": pdf_h,
                    "checked": False,
                    "mark_style": "tick",
                    "value": "false",
                    "is_cv_detected": True,
                    "detection_source": "scanned_box_cv",
                    "detection_confidence": 0.68,
                })

        return slots

    def _deduplicate_slots(self, slots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Prefer strong detections when multiple detectors describe the same geometry."""
        priority = {
            "native_widget": 100,
            "dotted_text_run": 90,
            "atomic_cell_row": 88,
            "comb_array": 85,
            "comb_grid": 84,
            "checkbox_glyph": 82,
            "vector_control": 75,
            "segment_enclosure": 66,
            "table_grid": 60,
            "vector_line": 50,
            "scanned_box_cv": 45,
            "scanned_line_cv": 40,
        }

        def intersection_over_union(a: List[float], b: List[float]) -> float:
            ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
            ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
            area = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            if area == 0.0:
                return 0.0
            a_area = max(0.1, (a[2] - a[0]) * (a[3] - a[1]))
            b_area = max(0.1, (b[2] - b[0]) * (b[3] - b[1]))
            return area / (a_area + b_area - area)

        ordered = sorted(
            slots,
            key=lambda item: (
                priority.get(item.get("detection_source", ""), 0),
                item.get("detection_confidence", 0.0),
            ),
            reverse=True,
        )
        accepted: List[Dict[str, Any]] = []
        for slot in ordered:
            rect = slot["rect"]
            duplicate = False
            for existing in accepted:
                existing_rect = existing["rect"]
                same_control = (
                    slot.get("slot_type") == existing.get("slot_type")
                    and abs(rect[0] - existing_rect[0]) <= 3.0
                    and abs(rect[1] - existing_rect[1]) <= 3.0
                )
                if same_control or intersection_over_union(rect, existing_rect) >= 0.72:
                    duplicate = True
                    break
            if not duplicate:
                accepted.append(slot)
        return sorted(accepted, key=lambda item: (item["rect"][1], item["rect"][0]))

    def _classify_field_kind(self, slot: Dict[str, Any], label: str) -> str:
        """Convert geometry and nearby language into an editable human field class."""
        if slot.get("field_kind") and slot.get("field_kind") != "table_cell":
            return slot["field_kind"]
        slot_type = slot.get("slot_type", "line")
        normalized = label.lower()
        semantic_uri = (slot.get("semantic_uri") or "").lower()
        context = f"{normalized} {semantic_uri}"

        if slot_type == "signature" or re.search(r"\bsign(?:ature)?\b", context):
            return "signature"
        if slot_type == "checkbox":
            return "checkbox"
        if slot.get("is_table_cell"):
            if re.search(r"\b(date|dob|birth|day|month|year)\b", context):
                return "date"
            return "table_cell"
        if slot_type == "cell":
            return "cell"
        if slot_type == "comb_box":
            if re.search(r"\b(date|dob|birth)\b", context):
                return "date"
            if re.search(r"\b(account|phone|mobile|bvn|number|amount)\b", context):
                return "number"
            return "comb"
        if re.search(r"\b(e-?mail)\b", context):
            return "email"
        if re.search(r"\b(phone|mobile|telephone|tel\.?|contact\s*(?:no|number))\b", context):
            return "phone"
        if re.search(r"\b(date|dob|birth)\b", context):
            return "date"
        if re.search(r"\b(amount|account\s*(?:no|number)|bvn|sort\s*code)\b", context):
            return "number"
        if re.search(r"\b(address|description|details|reason|comments?)\b", context) and slot.get("height", 0) >= 18:
            return "multiline"
        return "text"

    def _input_mode_for_kind(self, field_kind: str) -> str:
        return {
            "email": "email",
            "phone": "tel",
            "date": "date",
            "number": "numeric",
            "checkbox": "boolean",
            "radio": "boolean",
            "signature": "signature",
            "select": "choice",
            "multiline": "multiline",
            "cell": "text",
        }.get(field_kind, "text")

    def _expanded_text_spans(self, text_spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Split widely spaced composite spans so labels inside segmented fields remain local."""
        expanded = list(text_spans)
        for span in text_spans:
            text = span.get("text", "")
            if not re.search(r"\s{2,}", text):
                continue
            bbox = span.get("bbox", [0, 0, 0, 0])
            width = bbox[2] - bbox[0]
            for match in re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", text):
                fragment = match.group(0).strip()
                if not fragment:
                    continue
                start_ratio = match.start() / max(len(text), 1)
                end_ratio = match.end() / max(len(text), 1)
                clone = dict(span)
                clone["text"] = fragment
                clone["bbox"] = [
                    round(bbox[0] + width * start_ratio, 2), bbox[1],
                    round(bbox[0] + width * end_ratio, 2), bbox[3],
                ]
                expanded.append(clone)
        return expanded

    def _label_and_index_slots(self, slots: List[Dict[str, Any]], text_spans: List[Dict[str, Any]], page_num: int):
        label_spans = self._expanded_text_spans(text_spans)
        for idx, slot in enumerate(slots):
            s_rect = slot["rect"]
            slot_x0, slot_y0 = s_rect[0], s_rect[1]

            nearest_label = (slot.get("label_hint") or "").strip()
            min_dist = 9999.0

            if not nearest_label:
                for span in label_spans:
                    t_bbox = span["bbox"]
                    t_x0, t_x1 = t_bbox[0], t_bbox[2]
                    t_y0, t_y1 = t_bbox[1], t_bbox[3]
                    t_text = span["text"].strip(": ")

                    if t_text in ["☐", "□", "[]", "[ ]"] or t_text.startswith(("...", "……", "____")):
                        continue
                    if not t_text or len(t_text) > 120:
                        continue

                    slot_center_y = (s_rect[1] + s_rect[3]) / 2.0
                    text_center_y = (t_y0 + t_y1) / 2.0
                    score = None

                    # Segmented fields often print a short part label inside each boundary
                    # (for example Day / Month / Year). Use it and move only the editable rect
                    # to the ink-free remainder while retaining ``boundary_rect``.
                    inside = (
                        slot.get("is_table_cell")
                        and t_x0 >= s_rect[0] - 2.0 and t_x1 <= s_rect[2] + 2.0
                        and t_y0 >= s_rect[1] - 3.0 and t_y1 <= s_rect[3] + 3.0
                        and len(t_text) <= 28
                    )
                    if inside:
                        score = -100.0 + (t_x0 - s_rect[0])

                    # Printed labels usually sit immediately to the left of a line/cell.
                    elif t_x1 <= slot_x0 + 2.0 and abs(text_center_y - slot_center_y) <= 18.0:
                        gap = max(0.0, slot_x0 - t_x1)
                        if gap <= 220.0:
                            score = gap + abs(text_center_y - slot_center_y) * 2.0
                    # Checkbox/radio labels commonly sit to the right.
                    elif slot.get("slot_type") == "checkbox" and t_x0 >= s_rect[2] - 2.0 and abs(text_center_y - slot_center_y) <= 16.0:
                        gap = max(0.0, t_x0 - s_rect[2])
                        if gap <= 180.0:
                            score = gap + abs(text_center_y - slot_center_y) * 2.0
                    # Stacked layouts put the label just above the field.
                    elif t_y1 <= s_rect[1] + 3.0 and 0.0 <= s_rect[1] - t_y1 <= 30.0:
                        overlaps_x = min(t_x1, s_rect[2]) - max(t_x0, s_rect[0])
                        if overlaps_x > 0 or abs(t_x0 - s_rect[0]) <= 30.0:
                            score = (s_rect[1] - t_y1) * 2.0 + abs(t_x0 - s_rect[0]) * 0.2

                    if score is not None and score < min_dist:
                        min_dist = score
                        nearest_label = t_text
                        if inside and t_x1 + 8.0 < s_rect[2]:
                            slot["boundary_rect"] = slot.get("boundary_rect", list(s_rect))
                            slot["rect"] = [round(t_x1 + 3.0, 2), s_rect[1], s_rect[2], s_rect[3]]
                            slot["width"] = round(s_rect[2] - (t_x1 + 3.0), 2)
                            slot["baseline_y"] = round(s_rect[3] - 2.5, 2)

            field_label = nearest_label if nearest_label else f"Field {idx+1}"
            field_label = re.sub(r"\s+", " ", field_label).strip(" :.-_")
            field_name = re.sub(r"[^a-z0-9]+", "_", field_label.lower()).strip("_") or f"field_{idx+1}"

            slot["id"] = f"p{page_num}_slot_{idx+1}"
            slot["field_name"] = field_name
            slot["label"] = field_label

            semantic_uri, confidence = self.semantic_mapper.classify_slot(slot)
            slot["semantic_uri"] = semantic_uri
            slot["semantic_confidence"] = confidence
            field_kind = self._classify_field_kind(slot, field_label)
            slot["field_kind"] = field_kind
            slot["input_mode"] = self._input_mode_for_kind(field_kind)
            slot["detection_source"] = slot.get("detection_source", "geometry")
            slot["detection_confidence"] = round(float(slot.get("detection_confidence", 0.7)), 2)
            slot["review_status"] = "unreviewed"
            slot["required"] = bool(slot.get("required", False))
            slot["locked"] = bool(slot.get("native_widget", False))

        # Give every physical character cell a unique label and value while sharing only
        # optional sequence metadata. Semantic classification is propagated at group level.
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for slot in slots:
            if slot.get("group_id") and slot.get("group_role") == "character_sequence":
                groups.setdefault(slot["group_id"], []).append(slot)
        for group in groups.values():
            ordered = sorted(group, key=lambda item: item.get("group_index", 0))
            meaningful = next((item["label"] for item in ordered if not item["label"].startswith("Field ")), "Character cells")
            base_label = re.sub(r"\s*[·#]\s*\d+$", "", meaningful).strip() or "Character cells"
            probe = dict(ordered[0])
            probe["label"] = base_label
            semantic_uri, semantic_confidence = self.semantic_mapper.classify_slot(probe)
            for index, slot in enumerate(ordered):
                slot["group_label"] = base_label
                slot["group_index"] = index
                slot["group_size"] = len(ordered)
                slot["label"] = f"{base_label} · {index + 1}"
                slot["field_name"] = f"{re.sub(r'[^a-z0-9]+', '_', base_label.lower()).strip('_') or 'cell'}_{index + 1}"
                slot["semantic_uri"] = semantic_uri
                slot["semantic_confidence"] = semantic_confidence

        segmented: Dict[str, List[Dict[str, Any]]] = {}
        for slot in slots:
            if slot.get("group_id") and slot.get("group_role") == "segmented_fields":
                segmented.setdefault(slot["group_id"], []).append(slot)
        for group in segmented.values():
            date_parts = {}
            for slot in group:
                label = slot.get("label", "").lower()
                for part in ("day", "month", "year"):
                    if re.search(rf"\b{part}\b", label):
                        date_parts[part] = slot
            if len(date_parts) >= 2:
                for part, slot in date_parts.items():
                    slot["group_role"] = "date_parts"
                    slot["group_label"] = "Date"
                    slot["date_part"] = part
                    slot["semantic_uri"] = "person.dob"
                    slot["semantic_confidence"] = 0.9
                    slot["field_kind"] = "date"
                    slot["input_mode"] = "numeric"
