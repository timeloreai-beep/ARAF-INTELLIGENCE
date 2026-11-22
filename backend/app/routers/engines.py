from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..dependencies import current_subject
from ..quant.engines import analyze_quant_features, evaluate_engine_suite
from ..schemas import EngineEvaluationRequest

router = APIRouter(prefix="/engines", tags=["engines"])
_replay_journal: deque[dict] = deque(maxlen=40)


@router.post("/evaluate")
async def evaluate_engines(
    payload: EngineEvaluationRequest,
    subject: str = Depends(current_subject),
) -> dict:
    data = payload.model_dump(mode="json")
    try:
        report = evaluate_engine_suite(data)
        quant_features = analyze_quant_features(data["candles"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    report["quant_features"] = quant_features
    report["analysisOnly"] = True
    report["executionEnabled"] = False
    snapshot = {
        "ts": report["timestamp"],
        "version": report["version"],
        "symbol": report["symbol"],
        "fusionScore": report["fusionScore"],
        "allRequiredPass": report["allRequiredPass"],
        "autoTradeEligible": report["autoTradeEligible"],
        "snapshot": {
            "ltp": report["entry"],
            "dataQuality": data["data_quality"],
            "dataSource": data["data_source"],
            "closedBarsCount": quant_features["closed_candle_count"],
        },
        "engines": [
            {
                "id": item["engineId"],
                "status": item["status"],
                "delta": item["scoreDelta"],
                "reason": item["reasonCode"],
            }
            for item in report["engines"]
        ],
    }
    _replay_journal.appendleft({"subject": subject, "report": report, "snapshot": snapshot})
    return report


@router.get("/replay")
async def read_engine_replay(subject: str = Depends(current_subject)) -> dict:
    entries = [item["snapshot"] for item in _replay_journal if item["subject"] == subject]
    return {"success": True, "count": len(entries), "replay_journal": entries}


@router.get("/replay/export")
async def export_engine_replay(subject: str = Depends(current_subject)) -> JSONResponse:
    entries = [item for item in _replay_journal if item["subject"] == subject]
    exported = {
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.0-ADDITIVE",
        "latestReport": entries[0]["report"] if entries else None,
        "replayJournal": [item["snapshot"] for item in entries],
    }
    return JSONResponse(
        content=exported,
        headers={"Content-Disposition": 'attachment; filename="ARAF_ENGINES_REPLAY.json"'},
    )
