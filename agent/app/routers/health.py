"""Liveness/readiness probe."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import async_session_maker

router = APIRouter()


@router.get("/healthz")
async def healthz() -> JSONResponse:
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})
    return JSONResponse(status_code=200, content={"status": "ok"})
