from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "paper_trading": settings.paper_trading,
        "tracked_symbols": settings.tracked_symbols,
        "model": settings.model_name,
    }