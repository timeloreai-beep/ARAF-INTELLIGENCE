from fastapi import APIRouter, Depends, HTTPException, Request
from google.api_core.exceptions import GoogleAPIError
from pydantic import BaseModel, Field

from ..dependencies import current_subject

router = APIRouter(prefix="/notifications", tags=["notifications"])


class DeviceRegistration(BaseModel):
    token: str = Field(min_length=32, max_length=4096)


@router.post("/device", status_code=204)
async def register_device(
    payload: DeviceRegistration,
    request: Request,
    subject: str = Depends(current_subject),
) -> None:
    firebase = request.app.state.firebase
    try:
        await firebase.register_device(subject, payload.token)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GoogleAPIError as exc:
        raise HTTPException(status_code=502, detail="Could not register notification device") from exc
