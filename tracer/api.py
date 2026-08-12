"""Local API and static host for the Tracer Studio workbench."""

from __future__ import annotations

import base64
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image
from starlette.concurrency import run_in_threadpool

from .analyzer import analyze_image, recommend_output_contract, recommend_preset
from .config import OutputMode, PRESETS, TracingConfig
from .document import (
    SceneDocument,
    annotate_svg_scene,
    create_project_archive,
    open_project_archive,
)
from .parity import convert_with_parity
from .svg_safety import validate_svg_code
from .verifier import measure_bit_parity, render_svg_to_png

MAX_UPLOAD_BYTES = 64_000_000
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STUDIO_DIST = PROJECT_ROOT / "studio" / "dist"


def _verify_annotated_parity(annotated_svg: str, result: Any) -> bool:
    """Confirm an annotated exact document still renders pixel-identically."""
    proof = render_svg_to_png(annotated_svg, result.rendered.size)
    return bool(measure_bit_parity(result.rendered, proof)["bit_exact"])


def _image_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _decode_data_url(value: str | None) -> bytes | None:
    if not value:
        return None
    try:
        _, encoded = value.split(",", 1)
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Project image data is not a valid data URL.") from exc


async def _read_upload(upload: UploadFile, *, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    payload = await upload.read(limit + 1)
    if len(payload) > limit:
        raise HTTPException(status_code=413, detail=f"Upload exceeds the {limit // 1_000_000} MB limit.")
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return payload


def _temporary_image(payload: bytes, filename: str | None) -> tuple[Path, tempfile.TemporaryDirectory]:
    directory = tempfile.TemporaryDirectory(prefix="tracer-api-")
    suffix = Path(filename or "source.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        suffix = ".png"
    path = Path(directory.name) / f"source{suffix}"
    path.write_bytes(payload)
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        directory.cleanup()
        raise HTTPException(status_code=400, detail=f"Unsupported or invalid raster image: {exc}") from exc
    return path, directory


class SvgInspectRequest(BaseModel):
    svg: str = Field(min_length=1, max_length=5_000_000)
    name: str = "Imported SVG"


class ProjectSaveRequest(BaseModel):
    name: str = "Untitled"
    svg: str = Field(min_length=1, max_length=32_000_000)
    document: dict[str, Any]
    source_data_url: str | None = None
    preview_data_url: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Tracer Studio API", version="2.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "studio": "Tracer",
            "version": "2.0.0",
            "output_modes": [mode.value for mode in OutputMode],
        }

    @app.get("/api/presets")
    def presets() -> dict[str, Any]:
        return {
            name: {**values, "name": name}
            for name, values in PRESETS.items()
        }

    @app.post("/api/analyze")
    async def analyze(file: UploadFile = File(...)) -> dict[str, Any]:
        payload = await _read_upload(file)
        path, directory = _temporary_image(payload, file.filename)
        try:
            stats = await run_in_threadpool(analyze_image, path)
            preset, _, refinements = await run_in_threadpool(recommend_preset, path)
            representation = recommend_output_contract(stats)
            return {
                "preset": preset,
                "statistics": stats,
                "refinements": refinements,
                "representation": representation,
            }
        finally:
            directory.cleanup()

    @app.post("/api/convert")
    async def convert(
        file: UploadFile = File(...),
        output_mode: str = Form(OutputMode.HYBRID_PARITY.value),
        control_mode: str = Form("automatic"),
        preset: str = Form("complex_map_ui"),
        quality_profile: str = Form("balanced"),
        verify: bool = Form(False),
        target_quality: float = Form(0.985),
        residual_threshold: int = Form(4),
        residual_expansion: int = Form(1),
        max_dim: int = Form(4096),
        overrides_json: str = Form("{}"),
    ) -> dict[str, Any]:
        try:
            selected_mode = OutputMode(output_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown output mode.") from exc
        if preset not in PRESETS:
            raise HTTPException(status_code=422, detail="Unknown tracing preset.")
        try:
            raw_overrides = json.loads(overrides_json or "{}")
            allowed = set(TracingConfig.__dataclass_fields__)
            overrides = {key: value for key, value in raw_overrides.items() if key in allowed}
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Optimization overrides must be valid JSON.") from exc

        payload = await _read_upload(file)
        path, directory = _temporary_image(payload, file.filename)
        try:
            result = await run_in_threadpool(
                convert_with_parity,
                path,
                output_mode=selected_mode,
                preset=None if control_mode == "automatic" else preset,
                auto_preset=control_mode == "automatic",
                verify=verify,
                target_quality=max(0.0, min(1.0, target_quality)),
                quality_profile=quality_profile,
                max_dim=max(256, min(8192, max_dim)),
                residual_threshold=max(0, min(64, residual_threshold)),
                residual_expansion=max(0, min(8, residual_expansion)),
                **overrides,
            )
            if not result.validity.passed:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "The conversion did not satisfy Tracer's output contract.",
                        "errors": result.validity.errors,
                        "warnings": result.validity.warnings,
                    },
                )
            annotated_svg, document = await run_in_threadpool(
                annotate_svg_scene,
                result.svg,
                name=Path(file.filename or "Untitled").stem,
                output_mode=selected_mode.value,
            )
            # Scene annotation rewrites the document. For an exact
            # representation that rewrite must be proven harmless rather than
            # assumed, because a parity break here is invisible to every
            # quality metric.
            if result.validity.bit_exact:
                annotated_parity = await run_in_threadpool(
                    _verify_annotated_parity, annotated_svg, result
                )
                if not annotated_parity:
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "message": "Scene annotation broke pixel parity.",
                            "errors": ["The annotated document no longer matches the source."],
                            "warnings": [],
                        },
                    )
            document.metadata.update(result.report())
            return {
                "svg": annotated_svg,
                "preview_png": _image_data_url(result.rendered),
                "difference_png": _image_data_url(result.difference),
                "svgz_base64": base64.b64encode(result.svgz).decode("ascii"),
                "document": document.to_dict(),
                "report": result.report(),
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}") from exc
        finally:
            directory.cleanup()

    @app.post("/api/svg/inspect")
    async def inspect_svg(request: SvgInspectRequest) -> dict[str, Any]:
        try:
            normalized, width, height, elements = validate_svg_code(request.svg)
            annotated, document = annotate_svg_scene(normalized, name=request.name)
            rendered = await run_in_threadpool(render_svg_to_png, annotated, (width, height))
            return {
                "svg": annotated,
                "preview_png": _image_data_url(rendered),
                "document": document.to_dict(),
                "report": {
                    "elements": elements,
                    "paths": annotated.count("<path"),
                    "svg_bytes": len(annotated.encode("utf-8")),
                    "width": width,
                    "height": height,
                },
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/project/save")
    async def save_project(request: ProjectSaveRequest) -> Response:
        try:
            normalized, _, _, _ = validate_svg_code(request.svg, max_bytes=32_000_000)
            document = SceneDocument.from_dict(request.document)
            document.name = request.name.strip() or document.name
            archive = create_project_archive(
                document,
                normalized,
                source=_decode_data_url(request.source_data_url),
                preview=_decode_data_url(request.preview_data_url),
            )
            filename = "".join(
                character if character.isalnum() or character in "-_" else "-"
                for character in document.name.lower()
            ).strip("-") or "untitled"
            return Response(
                archive,
                media_type="application/x-tracer-project",
                headers={"Content-Disposition": f'attachment; filename="{filename}.tracer"'},
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/project/open")
    async def open_project(file: UploadFile = File(...)) -> dict[str, Any]:
        payload = await _read_upload(file, limit=128_000_000)
        try:
            project = open_project_archive(payload)
            source = project["source"]
            preview = project["preview"]
            return {
                "manifest": project["manifest"],
                "document": project["document"].to_dict(),
                "svg": project["svg"],
                "source_data_url": (
                    "data:image/png;base64," + base64.b64encode(source).decode("ascii")
                    if source else None
                ),
                "preview_data_url": (
                    "data:image/png;base64," + base64.b64encode(preview).decode("ascii")
                    if preview else None
                ),
            }
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if STUDIO_DIST.exists():
        app.mount("/", StaticFiles(directory=STUDIO_DIST, html=True), name="studio")
    else:
        @app.get("/", response_class=HTMLResponse)
        def studio_not_built() -> str:
            return (
                "<main style='font:16px system-ui;padding:40px'>"
                "<h1>Tracer Studio API is ready</h1>"
                "<p>Build the workbench with <code>npm run build</code> in <code>studio/</code>.</p>"
                "</main>"
            )
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("tracer.api:app", host="127.0.0.1", port=7999, reload=False)


if __name__ == "__main__":
    main()
