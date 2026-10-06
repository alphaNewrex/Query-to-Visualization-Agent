"""HTTP routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", summary="Liveness: the process is up")
async def healthz() -> dict[str, str]:
    """Answer without touching ClinicalTrials.gov or the planner, so it stays true when they are down."""
    return {"status": "ok"}
