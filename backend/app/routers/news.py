from fastapi import APIRouter, Depends, Request

from ..dependencies import current_subject

router = APIRouter(prefix="/news", tags=["news"])


def _add_summaries_to_snapshot(snapshot: dict[str, Any], engine: Any) -> dict[str, Any]:
    """Add full summaries with market impact to each news item in the snapshot."""
    enriched_items = []
    for item in snapshot.get("items", [])[:50]:
        summary = engine._generate_summary_with_impact(item)
        enriched_items.append({
            **item,
            "summary": summary,
        })
    snapshot["items"] = enriched_items
    return snapshot


@router.get("/feed")
async def news_feed(
    request: Request,
    _subject: str = Depends(current_subject),
) -> dict:
    engine = request.app.state.news_engine
    snapshot = await engine.poll_due()
    snapshot = _add_summaries_to_snapshot(snapshot, engine)
    return {"success": True, **snapshot}
