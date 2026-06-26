"""Raphael service: raphael-sync."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from raphael_contracts.errors import ErrorResponse
from raphael_sync.routes import router

app = FastAPI(
    title="raphael-sync",
    description="Desktop file monitoring, offline sync, conflict resolution",
    version="0.1.0",
    openapi_url="/v1/sync/openapi.json" if "/v1/sync" else "/openapi.json",
)

app.include_router(router, prefix="/v1/sync" if "/v1/sync" else "")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "raphael-sync"}


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception) -> JSONResponse:
    err = ErrorResponse(code="internal_error", message=str(exc))
    return JSONResponse(status_code=500, content=err.model_dump())
