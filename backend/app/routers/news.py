from fastapi import APIRouter, Depends, Request, Body
from typing import List, Optional
from pydantic import BaseModel, Field

from ..dependencies import current_subject

router = APIRouter(prefix="/news", tags=["news"])


class TranslateRequest(BaseModel):
    items: List[dict]
    target_langs: List[str] = Field(default=["bn", "hi", "gu", "mr", "ta"])
    fields: List[str] = Field(default=["title", "description", "summary"])


class TranslateResponse(BaseModel):
    success: bool
    items: List[dict]


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


@router.post("/translate", response_model=TranslateResponse)
async def translate_news(
    payload: TranslateRequest,
    request: Request,
    _subject: str = Depends(current_subject),
) -> TranslateResponse:
    """Translate news items to target languages using Gemini Flash."""
    engine = request.app.state.news_engine
    if not engine.settings.gemini_api_key:
        return TranslateResponse(success=False, items=payload.items)
    
    translated_items = []
    for item in payload.items:
        translated_item = dict(item)
        for lang in payload.target_langs:
            for field in payload.fields:
                text = item.get(field, "")
                if not text:
                    continue
                try:
                    translated = await engine._gemini_translate_async(text, lang)
                    if translated and translated != text:
                        translated_item[f"{field}_{lang}"] = translated
                except Exception:
                    continue
        translated_items.append(translated_item)
    
    return TranslateResponse(success=True, items=translated_items)
