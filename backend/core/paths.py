import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


def runtime_dir() -> Path:
    """Per-user directory for runtime files (endpoint/token, data); deliberately outside the repo."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / instance_name()


def _slug(text: str) -> str:
    """A folder/service name safe on Windows: no spaces, no characters a folder name forbids."""
    # ASCII only, same as frontend/src/main/backend.ts and launcher/start-pet.ps1.
    keep = "".join(c if (c.isascii() and c.isalnum()) or c in "-_." else "-" for c in text.strip())
    return keep.strip(".-")[:40]


def instance_name() -> str:
    """Which copy this is. `config/instance.yaml` (not in git) sets `name`; without it, "pet-kit".

    Two copies on one machine need different names: the name picks the data folder
    (%LOCALAPPDATA%/<name>), the credential-store prefix and the backend port.
    """
    try:
        text = (CONFIG_DIR / "instance.yaml").read_text(encoding="utf-8")
    except FileNotFoundError:
        return "pet-kit"
    from ruamel.yaml import YAML

    data = YAML(typ="safe").load(text) or {}
    name = data.get("name") if isinstance(data, dict) else None
    slug = _slug(str(name)) if isinstance(name, str) else ""
    return slug or "pet-kit"


def key_service(kind: str) -> str:
    """Credential-store service name, e.g. "pet-kit/llm". `kind` is llm, tts or skill."""
    return f"{instance_name()}/{kind}"
