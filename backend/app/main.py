from __future__ import annotations

import csv
import io
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BeforeValidator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from backend.app.catalog import digest, invalid, json_bytes, missing
from backend.app.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_CONTROL = "public, max-age=300, must-revalidate"


def parse_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use YYYY-MM-DD")
    return date.fromisoformat(value)


ISODate = Annotated[date, BeforeValidator(parse_date)]
IssueDate = Annotated[ISODate, Query()]
OptionalDate = Annotated[ISODate | None, Query()]
Model = Annotated[
    str | None, Query(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(/[a-zA-Z][a-zA-Z0-9_]*)?$", max_length=100)
]
Variant = Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{0,79}$")]
Kind = Literal["observed", "estimate"]


def query_guard(*allowed: str) -> Callable[[Request], None]:
    def validate(request: Request) -> None:
        keys = list(request.query_params)
        if set(keys) - set(allowed) or any(
            len(request.query_params.getlist(key)) > 1 for key in keys
        ):
            raise invalid("Unknown or repeated query parameter")

    return validate


def cached(
    request: Request,
    content: bytes,
    media_type: str = "application/json",
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    etag = f'"{digest(content)}"'
    response_headers = {
        "Cache-Control": CACHE_CONTROL,
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        **(headers or {}),
    }
    candidates = [
        value.strip().removeprefix("W/")
        for value in request.headers.get("if-none-match", "").split(",")
    ]
    if etag in candidates or "*" in candidates:
        return Response(status_code=304, headers=response_headers)
    return Response(content, media_type=media_type, headers=response_headers)


class SPAFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        raw = scope["path"]
        unsafe = (
            "\\" in raw
            or "%" in raw
            or ":" in raw
            or "\x00" in raw
            or ".." in raw.split("/")
            or raw == "/api"
            or raw.startswith("/api/")
        )
        if unsafe:
            raise HTTPException(404, "Not found")
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404 or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def cors_origins() -> list[str]:
    origins = [
        value.strip()
        for value in os.environ.get("BOLIVIA_FIRE_CORS_ORIGINS", "").split(",")
        if value.strip()
    ]
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise ValueError("CORS origins must be explicit HTTP or HTTPS origins")
    return origins


def create_app(root: Path | None = None) -> FastAPI:
    data_root = (root or Path(os.environ.get("BOLIVIA_FIRE_DATA_ROOT", REPO_ROOT))).resolve()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        tick = perf_counter()
        store = Store(data_root)
        application.state.store = store
        application.state.grid_bytes = json_bytes(store.grid) if store.grid is not None else None
        application.state.boundary_bytes = {
            level: json_bytes(data) for level, data in store.boundaries.items()
        }
        application.state.meta_bytes = json_bytes(store.meta())
        application.state.startup_seconds = perf_counter() - tick
        yield

    application = FastAPI(
        title="Bolivia Fire API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    application.add_middleware(GZipMiddleware, minimum_size=1000)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_methods=["GET", "HEAD"],
        allow_headers=["If-None-Match"],
        expose_headers=["ETag", "Content-Disposition"],
        allow_credentials=False,
    )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        code = {404: "not_found", 422: "validation_error", 503: "data_unavailable"}.get(
            error.status_code, "request_error"
        )
        message = error.detail if isinstance(error.detail, str) else "Request failed"
        return Response(
            json_bytes({"error": {"code": code, "message": message}}),
            status_code=error.status_code,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> Response:
        return Response(
            json_bytes(
                {"error": {"code": "validation_error", "message": "Invalid request parameters"}}
            ),
            status_code=422,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(Exception)
    async def server_error(request: Request, error: Exception) -> Response:
        return Response(
            json_bytes({"error": {"code": "internal_error", "message": "Internal server error"}}),
            status_code=500,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/v1/health", dependencies=[Depends(query_guard())])
    def health(request: Request) -> Response:
        return Response(
            json_bytes(request.app.state.store.health()),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/v1/meta", dependencies=[Depends(query_guard())])
    def meta(request: Request) -> Response:
        return cached(request, request.app.state.meta_bytes)

    @application.get(
        "/api/v1/weeks", dependencies=[Depends(query_guard("start", "end", "offset", "limit"))]
    )
    def weeks(
        request: Request,
        start: OptionalDate = None,
        end: OptionalDate = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=700)] = 700,
    ) -> Response:
        store = request.app.state.store
        first, last = store.date_range(start, end)
        rows = [
            row for row in store.weeks if first.isoformat() <= row["issue_date"] <= last.isoformat()
        ]
        return cached(
            request,
            json_bytes(
                {
                    "weeks": rows[offset : offset + limit],
                    "total": len(rows),
                    "offset": offset,
                    "limit": limit,
                }
            ),
        )

    @application.get("/api/v1/grid", dependencies=[Depends(query_guard())])
    def grid(request: Request) -> Response:
        if request.app.state.grid_bytes is None:
            raise HTTPException(503, "Prepared grid is not available")
        return cached(request, request.app.state.grid_bytes)

    @application.get("/api/v1/boundaries", dependencies=[Depends(query_guard("level"))])
    def boundaries(request: Request, level: Literal["0", "1"] = "0") -> Response:
        data = request.app.state.boundary_bytes.get(int(level))
        if data is None:
            raise missing("Pinned boundary is not available")
        return cached(request, data, "application/geo+json")

    @application.get(
        "/api/v1/layer",
        dependencies=[Depends(query_guard("issue_date", "kind", "model", "variant"))],
    )
    def layer(
        request: Request,
        issue_date: IssueDate,
        kind: Kind,
        model: Model = None,
        variant: Variant = "strict",
    ) -> Response:
        data = request.app.state.store.layer(issue_date, kind, model, variant)
        return cached(request, json_bytes(data))

    @application.get(
        "/api/v1/cells/{cell_id}/series",
        dependencies=[Depends(query_guard("start", "end", "model", "variant"))],
    )
    def series(
        request: Request,
        cell_id: str,
        start: OptionalDate = None,
        end: OptionalDate = None,
        model: Model = None,
        variant: Variant = "strict",
    ) -> Response:
        if not re.fullmatch(r"r\d{4}c\d{4}", cell_id):
            raise invalid("Invalid cell identifier")
        data = request.app.state.store.series(cell_id, start, end, model, variant)
        return cached(request, json_bytes(data))

    @application.get("/api/v1/metrics", dependencies=[Depends(query_guard("variant", "split"))])
    def metrics(
        request: Request,
        variant: Variant = "strict",
        split: Literal["train", "val_select", "val_calib", "test"] = "test",
    ) -> Response:
        store = request.app.state.store
        store.validate_variant(variant)
        data = store.evaluation.get((variant, split))
        if data is None:
            raise missing("Evaluation summary is not available for this variant and split")
        return cached(request, json_bytes(data))

    @application.get(
        "/api/v1/export",
        dependencies=[Depends(query_guard("issue_date", "kind", "model", "variant", "format"))],
    )
    def export(
        request: Request,
        issue_date: IssueDate,
        kind: Kind,
        model: Model = None,
        variant: Variant = "strict",
        format: Literal["csv"] = "csv",
    ) -> Response:
        store = request.app.state.store
        data = store.layer(issue_date, kind, model, variant)
        buffer = io.StringIO(newline="")
        buffer.write(f"# project=Bolivia Fire; protocol_sha256={store.protocol_hash}\n")
        buffer.write(
            f"# issue_date={issue_date}; kind={kind}; variant={variant}; "
            f"model={data['model'] or ''}\n"
        )
        buffer.write(f"# dataset_snapshot_ids={json_bytes(store.snapshots).decode()}\n")
        buffer.write("# Sources: NASA FIRMS; geoBoundaries CC BY 4.0; CHIRPS.\n")
        buffer.write(
            "# Detections are not fires; zeros are not proof of absence; "
            "retrospective estimates are not operational forecasts; missing labels are empty.\n"
        )
        writer = csv.writer(buffer)
        writer.writerow(["cell_id", "issue_date", "kind", "variant", "model", "value", "n_primary"])
        for index, value, count in zip(
            data["cell_index"], data["value"], data["n_primary"], strict=True
        ):
            writer.writerow(
                [store.cell_ids[index], issue_date, kind, variant, data["model"], value, count]
            )
        filename = f"bolivia-fire_{issue_date}_{kind}.csv"
        return cached(
            request,
            buffer.getvalue().encode(),
            "text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    dist = data_root / "frontend/dist"
    if (dist / "index.html").is_file():
        application.mount("/", SPAFiles(directory=dist, html=True), name="frontend")
    return application


app = create_app()
