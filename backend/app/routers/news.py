from fastapi import APIRouter, Depends, Request

from ..dependencies import current_subject

router = APIRouter(prefix="/news", tags=["news"])


@router.get("/feed")
async def news_feed(
    request: Request,
    _subject: str = Depends(current_subject),
) -> dict:
    engine = request.app.state.news_engine
    snapshot = await engine.poll_due()
    return {"success": True, **snapshot}
