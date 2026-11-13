"""The pet's status values (好感度 and the user's own stats): current numbers, prompt text, applying changes.

The LLM only says "+0.3" / "-1" (core.agent.parse_output); here the change is cut down to the stat's
per-reply and per-day limits, added, clamped to the range and persisted. Config: core/status_config.py.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from core.llm_config import ConfigError
from core.persona import load_yaml_model
from core.status_config import Stat, StatusChange, StatusConfig, fmt

log = logging.getLogger(__name__)

STATE_FILE = "status.json"
# GET /api/status carries only the newest few; the 状态 page pages through history().
RECENT_IN_VIEW = 20


@dataclass
class StatusPrompt:
    """What a turn's prompt gets while the module is on."""

    # Attitude lines of the tiers the values are in ("" when no tier has a prompt).
    attitude: str
    # One line per stat: id, name, current value, meaning, rule, limits.
    stats: str
    ids: list[str]


class StatusService:
    def __init__(
        self,
        config_path: Path,
        state_path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        today: Callable[[], str] = lambda: date.today().isoformat(),
    ) -> None:
        self._config_path = config_path
        # None (tests): values live in memory only.
        self._state_path = state_path
        self._clock = clock
        self._today = today
        self._values: dict[str, float] = {}
        self._day = ""
        # Rise per stat on `_day`, for Stat.daily_up.
        self._gained: dict[str, float] = {}
        # Oldest first; trimmed to StatusConfig.keep_history (0 = keep everything).
        self._recent: list[dict[str, Any]] = []
        # What the pet window animates for the last manual edit; None when it changed nothing.
        self.last_manual: StatusChange | None = None
        self._load()

    @property
    def config_path(self) -> Path:
        return self._config_path

    def config(self) -> StatusConfig:
        """Raises ConfigError when status.yaml is broken."""
        return load_yaml_model(self._config_path, StatusConfig)

    def value(self, stat: Stat) -> float:
        return stat.clamp(self._values.get(stat.id, stat.initial))

    def prompt(self) -> StatusPrompt | None:
        """None while the module is off (or its file is broken: chat must keep working)."""
        try:
            cfg = self.config()
        except ConfigError as e:
            log.warning("状态栏配置有误，本次不带状态栏：%s", e)
            return None
        if not cfg.enabled:
            return None
        attitude, lines = [], []
        for s in cfg.stats:
            v = self.value(s)
            tier = s.tier_for(v)
            if tier and tier.prompt:
                attitude.append(f"- {s.name}「{tier.name}」：{tier.prompt}")
            parts = [f"- {s.id}：{s.name}，当前 {fmt(v)}（范围 {fmt(s.min)}~{fmt(s.max)}）。"]
            if s.meaning:
                parts.append(f"含义：{s.meaning}")
            if s.rule:
                parts.append(f"增减规则：{s.rule}")
            parts.append(f"一次最多 +{fmt(s.max_up)} / -{fmt(s.max_down)}。")
            lines.append(" ".join(parts))
        return StatusPrompt(attitude="\n".join(attitude), stats="\n".join(lines), ids=[s.id for s in cfg.stats])

    def apply(self, changes: list[StatusChange]) -> list[StatusChange]:
        """Applies what the model asked for; returns the changes that actually happened (possibly none)."""
        if not changes:
            return []
        try:
            cfg = self.config()
        except ConfigError:
            return []
        if not cfg.enabled:
            return []
        self._roll_day()
        done: list[StatusChange] = []
        seen: set[str] = set()
        for c in changes:
            s = cfg.stat(c.id)
            if s is None or c.id in seen:
                continue
            seen.add(c.id)
            old = self.value(s)
            delta = min(s.max_up, max(-s.max_down, c.delta))
            if delta > 0 and s.daily_up > 0:
                room = max(0.0, s.daily_up - self._gained.get(s.id, 0.0))
                if delta > room:
                    log.info("%s 今天已经涨了 %s（每日上限 %s），这次只加 %s", s.name, fmt(self._gained.get(s.id, 0.0)), fmt(s.daily_up), fmt(room))
                    delta = room
            new = s.clamp(old + delta)
            actual = round(new - old, 2)
            if actual == 0:
                continue
            self._values[s.id] = new
            if actual > 0:
                self._gained[s.id] = round(self._gained.get(s.id, 0.0) + actual, 2)
            before, after = s.tier_for(old), s.tier_for(new)
            crossed = after.name if after is not None and after is not before else None
            done.append(
                StatusChange(
                    id=s.id,
                    delta=actual,
                    reason=c.reason,
                    name=s.name,
                    value=new,
                    color=s.color,
                    tier=crossed,
                    ratio=round((new - s.min) / (s.max - s.min), 4),
                )
            )
            self._record(
                {"ts": self._clock(), "id": s.id, "name": s.name, "delta": actual, "value": new, "reason": c.reason, "tier": crossed},
                cfg.keep_history,
            )
            log.info("%s %+g → %s%s（%s）", s.name, actual, fmt(new), f"，进入「{crossed}」" if crossed else "", c.reason or "未说明原因")
        if done:
            self._save()
        return done

    def grant(self, stat_id: str, delta: float, reason: str) -> StatusChange | None:
        """A change decided by the program (a mini-game result): not cut to the per-reply or per-day limits,
        only kept inside the stat's range. None when the module is off, the stat is gone or nothing changed."""
        try:
            cfg = self.config()
        except ConfigError:
            return None
        s = cfg.stat(stat_id) if cfg.enabled else None
        if s is None:
            return None
        old = self.value(s)
        new = s.clamp(old + delta)
        actual = round(new - old, 2)
        if actual == 0:
            return None
        self._values[s.id] = new
        before, after = s.tier_for(old), s.tier_for(new)
        crossed = after.name if after is not None and after is not before else None
        self._record(
            {"ts": self._clock(), "id": s.id, "name": s.name, "delta": actual, "value": new, "reason": reason, "tier": crossed},
            cfg.keep_history,
        )
        log.info("%s %+g → %s（%s）", s.name, actual, fmt(new), reason)
        self._save()
        return StatusChange(
            id=s.id, delta=actual, reason=reason, name=s.name, value=new, color=s.color, tier=crossed,
            ratio=round((new - s.min) / (s.max - s.min), 4),
        )

    def set_value(self, stat_id: str, value: float | None) -> bool:
        """Console: set a value by hand (None = back to the initial value). False = no such stat."""
        cfg = self.config()
        s = cfg.stat(stat_id)
        if s is None:
            return False
        old = self.value(s)
        new = s.clamp(s.initial if value is None else value)
        self._values[s.id] = new
        self.last_manual = None
        if new != old:
            reason = "在控制台手动修改"
            self._record(
                {"ts": self._clock(), "id": s.id, "name": s.name, "delta": round(new - old, 2), "value": new, "reason": reason, "tier": None},
                cfg.keep_history,
            )
            before, after = s.tier_for(old), s.tier_for(new)
            self.last_manual = StatusChange(
                id=s.id,
                delta=round(new - old, 2),
                reason=reason,
                name=s.name,
                value=new,
                color=s.color,
                tier=after.name if after is not None and after is not before else None,
                ratio=round((new - s.min) / (s.max - s.min), 4),
            )
        self._save()
        return True

    def view(self, cfg: StatusConfig) -> dict[str, Any]:
        """Current numbers for the console."""
        self._roll_day()
        stats = []
        for s in cfg.stats:
            v = self.value(s)
            tier = s.tier_for(v)
            stats.append({"id": s.id, "value": v, "tier": tier.name if tier else None, "gained_today": self._gained.get(s.id, 0.0)})
        return {"stats": stats, "recent": self._recent[::-1][:RECENT_IN_VIEW], "recent_total": len(self._recent)}

    def history(self, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        """One page of the change history, newest first, and the total count."""
        newest = self._recent[::-1]
        return newest[offset : offset + limit], len(newest)

    def trim(self, keep: int) -> int:
        """Drops the oldest records beyond `keep` (0 = keep everything); returns how many were dropped."""
        drop = len(self._recent) - keep if keep > 0 else 0
        if drop <= 0:
            return 0
        del self._recent[:drop]
        self._save()
        log.info("状态值变化记录超过 %d 条，删掉了最早的 %d 条", keep, drop)
        return drop

    def _record(self, entry: dict[str, Any], keep: int) -> None:
        self._recent.append(entry)
        if keep > 0 and len(self._recent) > keep:
            del self._recent[: len(self._recent) - keep]

    def _roll_day(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._gained = day, {}

    def _load(self) -> None:
        if self._state_path is None:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("状态值文件读不了，从初始值开始：%s", e)
            return
        if not isinstance(data, dict):
            return

        def numbers(raw: Any) -> dict[str, float]:
            if not isinstance(raw, dict):
                return {}
            return {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

        self._values = numbers(data.get("values"))
        self._gained = numbers(data.get("gained"))
        self._day = str(data.get("day") or "")
        recent = data.get("recent")
        if isinstance(recent, list):
            self._recent = [x for x in recent if isinstance(x, dict)]

    def _save(self) -> None:
        if self._state_path is None:
            return
        data = {"values": self._values, "day": self._day, "gained": self._gained, "recent": self._recent}
        tmp = self._state_path.with_suffix(".json.tmp")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            log.exception("保存状态值失败")
        finally:
            tmp.unlink(missing_ok=True)
