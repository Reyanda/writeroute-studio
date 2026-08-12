from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
# The engine lives beside this file now. The previous release nested a whole copy of
# the WriteRoute distribution under core/, including a stale detection engine that
# reverted false-positive work when installed; that copy is gone.
sys.path.insert(0, str(ROOT))

from writeroute.audit import audit_text  # noqa: E402
from writeroute.route import suggest_text, repair_text, rewrite_with_callback, verify_text  # noqa: E402
from writeroute.contracts import compile_revision_contract  # noqa: E402
from writeroute.formatting import formatting_advice
from writeroute.genres import get_genre, load_genres  # noqa: E402

MAX_CHARS = 300_000
MAX_UPLOAD = 15 * 1024 * 1024
STATIC = ROOT / "static"

app = FastAPI(title="WriteRoute Studio", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class TextPayload(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    genre: str = Field(min_length=2)


class SuggestPayload(TextPayload):
    max_candidates: int = Field(default=3, ge=1, le=5)


class VerifyPayload(BaseModel):
    original: str = Field(min_length=1, max_length=MAX_CHARS)
    candidate: str = Field(min_length=1, max_length=MAX_CHARS)
    genre: str = Field(min_length=2)


class RewritePayload(TextPayload):
    provider: str = "openai-compatible"
    model: str = ""
    base_url: str = ""
    candidates: int = Field(default=3, ge=1, le=5)
    temperature: float = Field(default=0.25, ge=0, le=1.2)


class ExportPayload(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    html: str = Field(default="", max_length=MAX_CHARS * 3)
    filename: str = "writeroute-document"
    format: str = "txt"


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/studio", response_class=HTMLResponse)
@app.get("/studio.html", response_class=HTMLResponse)
def studio() -> str:
    return (STATIC / "studio.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "product": "WriteRoute Studio",
        "engine": "WriteRoute 2.0",
        "genres": list(load_genres().keys()),
        "byok": True,
        "persistence": "none",
    }


@app.post("/api/audit")
def api_audit(body: TextPayload) -> dict[str, Any]:
    try:
        audit = audit_text(body.text, body.genre)
        return {
            "audit": audit.to_dict(),
            "formatting": formatting_advice(body.text, audit.genre),
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/suggest")
def api_suggest(body: SuggestPayload) -> dict[str, Any]:
    try:
        result = suggest_text(body.text, body.genre, max_candidates=body.max_candidates)
        result["formatting"] = formatting_advice(body.text, result["genre"])
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/repair")
def api_repair(body: TextPayload) -> dict[str, Any]:
    try:
        return repair_text(body.text, body.genre)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/verify")
def api_verify(body: VerifyPayload) -> dict[str, Any]:
    try:
        return verify_text(body.original, body.candidate, body.genre)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _extract_upload(data: bytes, filename: str, content_type: str | None) -> tuple[str, str]:
    suffix = Path(filename or "upload.txt").suffix.lower()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file exceeds 15 MB upload limit")
    if suffix in {".txt", ".md", ".markdown", ".rst", ".csv", ".log"}:
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                return data.decode(enc), "plain-text"
            except UnicodeDecodeError:
                continue
    if suffix == ".docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(data))
            parts: list[str] = []
            for p in doc.paragraphs:
                parts.append(p.text)
            for table in doc.tables:
                for row in table.rows:
                    parts.append("\t".join(cell.text for cell in row.cells))
            return "\n\n".join(p for p in parts if p.strip()), "docx"
        except Exception as exc:
            raise HTTPException(422, f"could not read DOCX: {exc}") from exc
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
            if not text.strip():
                raise ValueError("the PDF contains no extractable text")
            return text, "pdf"
        except Exception as exc:
            raise HTTPException(422, f"could not read PDF text layer: {exc}") from exc
    if suffix == ".rtf":
        raw = data.decode("latin-1", errors="replace")
        raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
        raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
        raw = raw.replace("{", "").replace("}", "")
        return raw, "rtf"
    raise HTTPException(415, "supported uploads: TXT, Markdown, DOCX, PDF with text layer, RTF, CSV")


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), genre: str = Form(...)) -> dict[str, Any]:
    data = await file.read()
    text, source_format = _extract_upload(data, file.filename or "upload", file.content_type)
    if not text.strip():
        raise HTTPException(422, "document contains no readable text")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"extracted document exceeds {MAX_CHARS:,} characters")
    audit = audit_text(text, genre)
    return {
        "filename": file.filename,
        "sourceFormat": source_format,
        "text": text,
        "audit": audit.to_dict(),
        "formatting": formatting_advice(text, audit.genre),
    }


def _http_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 75) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise RuntimeError(f"provider returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach provider: {exc.reason}") from exc


def _make_byok_callback(provider: str, api_key: str, model: str, base_url: str, temperature: float):
    provider = provider.strip().lower()
    if not api_key.strip():
        raise HTTPException(400, "an API key is required for generative rewrite")
    if not model.strip():
        raise HTTPException(400, "choose or enter a model")

    def callback(contract: str, source: str) -> str:
        system = "You are WriteRoute's revision engine. Follow the editorial contract exactly. Return only the revised document text."
        user = f"EDITORIAL CONTRACT\n{contract}\n\nSOURCE DOCUMENT\n{source}"
        if provider == "anthropic":
            url = (base_url.strip().rstrip("/") or "https://api.anthropic.com") + "/v1/messages"
            data = _http_json(url, {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }, {
                "model": model,
                "max_tokens": 8192,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            })
            try:
                return "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text").strip()
            except Exception as exc:
                raise RuntimeError(f"unexpected Anthropic response: {data}") from exc

        if provider == "deepseek":
            root = base_url.strip().rstrip("/") or "https://api.deepseek.com"
        elif provider == "openrouter":
            root = base_url.strip().rstrip("/") or "https://openrouter.ai/api/v1"
        elif provider == "openai":
            root = base_url.strip().rstrip("/") or "https://api.openai.com/v1"
        else:
            root = base_url.strip().rstrip("/")
            if not root:
                raise RuntimeError("a base URL is required for an OpenAI-compatible provider")
        url = root + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        if provider == "openrouter":
            headers.update({"HTTP-Referer": "http://localhost", "X-Title": "WriteRoute Studio"})
        data = _http_json(url, headers, {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        })
        try:
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in content)
            return str(content).strip()
        except Exception as exc:
            raise RuntimeError(f"unexpected compatible-provider response: {data}") from exc

    return callback


@app.post("/api/rewrite")
def api_rewrite(body: RewritePayload, x_writeroute_key: str | None = Header(default=None)) -> dict[str, Any]:
    callback = _make_byok_callback(body.provider, x_writeroute_key or "", body.model, body.base_url, body.temperature)
    try:
        return rewrite_with_callback(body.text, callback, body.genre, candidates=body.candidates)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/export")
def api_export(body: ExportPayload) -> Response:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", body.filename).strip("-.") or "writeroute-document"
    fmt = body.format.lower()
    if fmt == "txt":
        return Response(body.text, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe}.txt"'})
    if fmt == "md":
        return Response(body.text, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe}.md"'})
    if fmt == "html":
        content = body.html or "<p>" + body.text.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>") + "</p>"
        doc = f"<!doctype html><meta charset='utf-8'><title>{safe}</title><body>{content}</body>"
        return Response(doc, media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe}.html"'})
    if fmt == "docx":
        try:
            from docx import Document
            from docx.shared import Pt
            doc = Document()
            styles = doc.styles
            styles["Normal"].font.name = "Aptos"
            styles["Normal"].font.size = Pt(11)
            for block in re.split(r"\n\s*\n", body.text):
                if block.strip():
                    doc.add_paragraph(block.strip())
            out = io.BytesIO(); doc.save(out)
            return Response(out.getvalue(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f'attachment; filename="{safe}.docx"'})
        except Exception as exc:
            raise HTTPException(500, f"DOCX export failed: {exc}") from exc
    raise HTTPException(422, "format must be txt, md, html, or docx")


@app.exception_handler(Exception)
async def generic_error(_request, exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    return JSONResponse(status_code=500, content={"detail": str(exc)})
