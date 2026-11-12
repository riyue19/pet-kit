"""Idle fishing (see game.py). Config: config/fish.yaml, config/fishing_lines.yaml; data: fishing.db."""

from core.games.fishing.config import FishConfig, FishFiles
from core.games.fishing.game import CmdResult, FishingGame, parse_command
from core.games.fishing.store import Store

DB_FILE = "fishing.db"

__all__ = ["DB_FILE", "CmdResult", "FishConfig", "FishFiles", "FishingGame", "Store", "parse_command"]
