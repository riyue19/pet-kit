"""`camera` section of config/backend.yaml (M9)."""

from pydantic import BaseModel, ConfigDict, Field


class CameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Master switch. Off = the camera is never opened. While on, the camera stays open (its LED is lit).
    enabled: bool = False
    device_index: int = Field(default=0, ge=0, le=9)
    # Seconds between presence checks. Frames are analysed locally, kept in memory only for that moment.
    check_seconds: float = Field(default=3, ge=1, le=30)
    # No face for this long = the user has left.
    away_after_seconds: float = Field(default=30, ge=5, le=600)
    # When the user comes back after being away, take one look and say hello (the only place the camera
    # feeds the vision model). Needs llm.roles.vision.
    greet_on_return: bool = True
    # ...but only if they were away at least this long.
    greet_min_away_minutes: float = Field(default=5, ge=1, le=240)
    # Local face detector (YuNet), relative to backend/; fetched by scripts/fetch_models.py.
    model_dir: str = "models/camera"
