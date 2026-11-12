import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


def runtime_dir() -> Path:
    """Per-user directory for runtime files (endpoint/token); deliberately outside the repo."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "pet-kit"
