import os
import fitz
import csv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Dict, List, Any, Optional

from pdfstudio.unbundler import TracerUnbundler
from pdfstudio.slot_detector import TracerSlotDetector
from pdfstudio.rebundler import TracerRebundler
from pdfstudio.semantic_schema import (
    TracerSemanticMapper,
    TracerAgenticFillEngine,
    SAMPLE_PROFILES
)
from pdfstudio.stirling_bridge import StirlingBridge

# Mounted by app.py under /pdf, so every route here is reached as /pdf/api/... . It is a
# sub-application rather than routes on the main app because both surfaces define
# /api/upload and they mean different things: prose extracts text, this one stores a PDF.
app = FastAPI(
    title="WriteRoute PDF Studio",
    version="2.1.0",
    description="Local PDF reading, annotation, field detection, editing, filling and export",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "static", "pdfstudio")

# Uploads, outputs and ledgers go to a per-user data directory rather than into the source
# tree. The originals wrote beside the code, which is how a repository ends up holding
# somebody's bank forms; WRITEROUTE_DATA_DIR overrides it.
DATA_DIR = os.environ.get(
    "WRITEROUTE_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".writeroute", "pdf"),
)
OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
HIGHLIGHT_OUTPUT_DIR = os.path.join(DATA_DIR, "outputs", "highlights")
QC_DIR = os.path.join(DATA_DIR, "qc")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(HIGHLIGHT_OUTPUT_DIR, exist_ok=True)
os.makedirs(QC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

stirling_bridge = StirlingBridge()

class UnbundleRequest(BaseModel):
    filename: str

class FillRebundleRequest(BaseModel):
    filename: str
    slots: Dict[str, List[Dict[str, Any]]]
    annotations: Optional[List[Dict[str, Any]]] = None

class DetectRegionRequest(BaseModel):
    filename: str
    page: int
    x: float
    y: float
    radius: Optional[float] = 36.0

class SearchDocumentRequest(BaseModel):
    filename: str
    query: str
    max_results: Optional[int] = 200

class AgenticFillRequest(BaseModel):
    filename: str
    profile_key: Optional[str] = "individual_retail"
    custom_profile: Optional[Dict[str, Any]] = None

class StirlingSplitRequest(BaseModel):
    filename: str
    pages: List[int]

class StirlingMergeRequest(BaseModel):
    filenames: List[str]

class StirlingCompressRequest(BaseModel):
    filename: str

class StirlingEncryptRequest(BaseModel):
    filename: str
    password: str

def get_pdf_filepath(filename: str) -> str:
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid PDF filename.")
    for directory in (BASE_DIR, UPLOAD_DIR, BACKUP_DIR):
        candidate = os.path.join(directory, safe_name)
        if os.path.isfile(candidate):
            return candidate
    raise HTTPException(status_code=404, detail=f"PDF document '{filename}' not found.")

@app.get("/api/documents")
def list_documents():
    all_files = set()
    for directory in (BASE_DIR, UPLOAD_DIR, BACKUP_DIR):
        all_files.update(f for f in os.listdir(directory) if f.lower().endswith(".pdf"))
    all_files = sorted(all_files, key=str.lower)
    return {"documents": all_files, "count": len(all_files)}

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):
    original_name = os.path.basename(file.filename or "document.pdf")
    if not original_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Choose a PDF document.")

    data = await file.read()
    if len(data) > 60 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF exceeds the 60 MB local workspace limit.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")

    try:
        validation_doc = fitz.open(stream=data, filetype="pdf")
        page_count = len(validation_doc)
        is_encrypted = validation_doc.needs_pass
        validation_doc.close()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="The PDF could not be opened.") from exc

    if is_encrypted:
        raise HTTPException(status_code=400, detail="Unlock the PDF before adding it to the editor.")

    stem = "".join(ch if ch.isalnum() or ch in ("-", "_", " ") else "-" for ch in os.path.splitext(original_name)[0])
    stem = " ".join(stem.split()).strip(" .-_") or "document"
    safe_name = f"{stem}.pdf"
    suffix = 2
    while os.path.exists(os.path.join(UPLOAD_DIR, safe_name)):
        safe_name = f"{stem}-{suffix}.pdf"
        suffix += 1

    with open(os.path.join(UPLOAD_DIR, safe_name), "wb") as destination:
        destination.write(data)

    return {
        "status": "success",
        "filename": safe_name,
        "page_count": page_count,
        "size_bytes": len(data),
    }

@app.post("/api/unbundle")
def unbundle_pdf(req: UnbundleRequest):
    pdf_path = get_pdf_filepath(req.filename)
    unbundler = TracerUnbundler(pdf_path)
    doc_data = unbundler.unbundle_document()
    
    detector = TracerSlotDetector()
    all_slots = {}
    total_slots = 0

    for p in doc_data["pages"]:
        p_num = p["page_number"]
        slots = detector.detect_slots_for_page(p, unbundler.doc, p_num - 1)
        all_slots[str(p_num)] = slots
        total_slots += len(slots)

    client_pages = [{
        key: value for key, value in page.items()
        if key not in {"vector_primitives", "drawing_segments", "drawing_rectangles", "curve_bounds", "text_spans", "widgets"}
    } for page in doc_data["pages"]]
    unbundler.close()
    
    return {
        "status": "success",
        "filename": req.filename,
        "page_count": doc_data["page_count"],
        "total_slots": total_slots,
        "pages": client_pages,
        "slots": all_slots
    }

@app.post("/api/detect-region")
def detect_region(req: DetectRegionRequest):
    pdf_path = get_pdf_filepath(req.filename)
    unbundler = TracerUnbundler(pdf_path)
    if req.page < 1 or req.page > len(unbundler.doc):
        unbundler.close()
        raise HTTPException(status_code=400, detail="Invalid page number.")
    page_data = unbundler.unbundle_page(req.page - 1)
    detector = TracerSlotDetector()
    fields = detector.detect_slots_at_point(
        page_data,
        req.x,
        req.y,
        unbundler.doc,
        req.page - 1,
        max(8.0, min(float(req.radius or 36.0), 120.0)),
    )
    unbundler.close()
    return {"status": "success", "page": req.page, "fields": fields, "count": len(fields)}

@app.post("/api/search-document")
def search_document(req: SearchDocumentRequest):
    query = req.query.strip()
    if not query:
        return {"status": "success", "query": "", "hits": [], "count": 0}
    pdf_path = get_pdf_filepath(req.filename)
    doc = fitz.open(pdf_path)
    limit = max(1, min(int(req.max_results or 200), 1000))
    hits = []
    for page_index, page in enumerate(doc):
        for rect in page.search_for(query):
            hits.append({
                "id": f"search_p{page_index + 1}_{len(hits) + 1}",
                "page": page_index + 1,
                "rect": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                "text": page.get_textbox(rect).strip() or query,
            })
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    doc.close()
    return {"status": "success", "query": query, "hits": hits, "count": len(hits)}

@app.get("/api/page-render/{filename}/{page_num}")
def get_page_render(filename: str, page_num: int, dpi: int = 150):
    pdf_path = get_pdf_filepath(filename)
    doc = fitz.open(pdf_path)
    if page_num < 1 or page_num > len(doc):
        doc.close()
        raise HTTPException(status_code=400, detail=f"Invalid page number {page_num}")
    
    page = doc[page_num - 1]
    pix = page.get_pixmap(dpi=dpi)
    out_img_name = f"{filename}_p{page_num}_{dpi}.png".replace(" ", "_")
    out_img_path = os.path.join(OUTPUT_DIR, out_img_name)
    pix.save(out_img_path)
    doc.close()
    
    return FileResponse(out_img_path, media_type="image/png")

@app.post("/api/fill-and-rebundle")
def fill_and_rebundle_pdf(req: FillRebundleRequest):
    pdf_path = get_pdf_filepath(req.filename)
    formatted_slots = {}
    for p_str, slot_list in req.slots.items():
        formatted_slots[int(p_str)] = slot_list

    has_annotations = bool(req.annotations)
    stem = os.path.splitext(req.filename)[0]
    out_name = (f"{stem}-highlighted.pdf" if has_annotations else f"filled_{req.filename}").replace(" ", "_")
    out_dir = HIGHLIGHT_OUTPUT_DIR if has_annotations else OUTPUT_DIR
    out_path = os.path.join(out_dir, out_name)

    rebundler = TracerRebundler(pdf_path)
    result_path = rebundler.fill_and_rebundle(formatted_slots, out_path, req.annotations or [])

    if has_annotations:
        ledger_path = os.path.join(QC_DIR, f"{stem.replace(' ', '_')}-highlight-ledger.csv")
        with open(ledger_path, "w", newline="", encoding="utf-8") as ledger:
            writer = csv.DictWriter(ledger, fieldnames=[
                "claim_or_field", "page", "quoted_text_or_table_cell", "highlight_type",
                "colour", "confidence", "reviewer_note",
            ])
            writer.writeheader()
            for annotation in req.annotations or []:
                writer.writerow({
                    "claim_or_field": annotation.get("id", "highlight"),
                    "page": annotation.get("page", ""),
                    "quoted_text_or_table_cell": annotation.get("text", ""),
                    "highlight_type": annotation.get("type", "highlight"),
                    "colour": annotation.get("color", "yellow"),
                    "confidence": annotation.get("confidence", 1.0),
                    "reviewer_note": annotation.get("note", ""),
                })

    return {
        "status": "success",
        "output_filename": out_name,
        "download_url": f"/api/download/{out_name}"
    }

@app.get("/api/semantic-taxonomy")
def get_semantic_taxonomy():
    mapper = TracerSemanticMapper()
    return {
        "taxonomy": mapper.CANONICAL_ENTITIES,
        "sample_profiles": list(SAMPLE_PROFILES.keys())
    }

@app.post("/api/agentic-fill")
def agentic_fill_pdf(req: AgenticFillRequest):
    pdf_path = get_pdf_filepath(req.filename)
    unbundler = TracerUnbundler(pdf_path)
    doc_data = unbundler.unbundle_document()
    detector = TracerSlotDetector()

    all_slots = {}
    for p in doc_data["pages"]:
        p_num = p["page_number"]
        slots = detector.detect_slots_for_page(p, unbundler.doc, p_num - 1)
        all_slots[str(p_num)] = slots

    unbundler.close()

    if req.custom_profile:
        profile_data = req.custom_profile
    elif req.profile_key in SAMPLE_PROFILES:
        profile_data = SAMPLE_PROFILES[req.profile_key]["data"]
    else:
        profile_data = SAMPLE_PROFILES["individual_retail"]["data"]

    agentic_engine = TracerAgenticFillEngine()
    filled_slots = agentic_engine.fill_slots_agentically(all_slots, profile_data)

    out_name = f"agentic_filled_{req.filename}".replace(" ", "_")
    out_path = os.path.join(OUTPUT_DIR, out_name)

    formatted_slots = {int(k): v for k, v in filled_slots.items()}
    rebundler = TracerRebundler(pdf_path)
    rebundler.fill_and_rebundle(formatted_slots, out_path)

    return {
        "status": "success",
        "profile_used": req.profile_key,
        "output_filename": out_name,
        "download_url": f"/api/download/{out_name}",
        "slots": filled_slots
    }

# --- STIRLING-PDF API ENDPOINTS ---
@app.get("/api/stirling/status")
def stirling_status():
    return stirling_bridge.get_status()

@app.post("/api/stirling/split")
def stirling_split(req: StirlingSplitRequest):
    pdf_path = get_pdf_filepath(req.filename)
    out_name = f"split_{req.filename}".replace(" ", "_")
    out_path = os.path.join(OUTPUT_DIR, out_name)
    stirling_bridge.split_pdf(pdf_path, req.pages, out_path)
    return {"status": "success", "output_filename": out_name, "download_url": f"/api/download/{out_name}"}

@app.post("/api/stirling/merge")
def stirling_merge(req: StirlingMergeRequest):
    pdf_paths = [get_pdf_filepath(f) for f in req.filenames]
    out_name = f"merged_document.pdf"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    stirling_bridge.merge_pdfs(pdf_paths, out_path)
    return {"status": "success", "output_filename": out_name, "download_url": f"/api/download/{out_name}"}

@app.post("/api/stirling/compress")
def stirling_compress(req: StirlingCompressRequest):
    pdf_path = get_pdf_filepath(req.filename)
    out_name = f"compressed_{req.filename}".replace(" ", "_")
    out_path = os.path.join(OUTPUT_DIR, out_name)
    stirling_bridge.compress_pdf(pdf_path, out_path)
    return {"status": "success", "output_filename": out_name, "download_url": f"/api/download/{out_name}"}

@app.post("/api/stirling/encrypt")
def stirling_encrypt(req: StirlingEncryptRequest):
    pdf_path = get_pdf_filepath(req.filename)
    out_name = f"encrypted_{req.filename}".replace(" ", "_")
    out_path = os.path.join(OUTPUT_DIR, out_name)
    stirling_bridge.encrypt_pdf(pdf_path, req.password, out_path)
    return {"status": "success", "output_filename": out_name, "download_url": f"/api/download/{out_name}"}

@app.get("/api/download/{filename}")
def download_file(filename: str):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    candidates = [os.path.join(OUTPUT_DIR, safe_name), os.path.join(HIGHLIGHT_OUTPUT_DIR, safe_name)]
    file_path = next((path for path in candidates if os.path.exists(path)), None)
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=filename, media_type="application/pdf")

# Serve frontend static assets
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
