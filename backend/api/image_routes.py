"""POST /api/pet/image: the pet window parks an image here, then sends a user.text event with the returned id."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import require_token
from core.image_stash import MAX_IMAGE_BYTES

router = APIRouter(prefix="/api/pet", dependencies=[Depends(require_token)])


class ImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # base64 inflates by 4/3; the stash enforces the exact byte limit.
    data_url: str = Field(max_length=MAX_IMAGE_BYTES * 4 // 3 + 200)


class ImageOut(BaseModel):
    id: str


@router.post("/image")
def upload_image(request: Request, body: ImageIn) -> ImageOut:
    try:
        return ImageOut(id=request.app.state.images.put(body.data_url))
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
