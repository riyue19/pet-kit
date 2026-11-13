"""Runs the skills the LLM asked for: whitelist check -> argument check -> optional confirmation -> launch.

Nothing here logs or returns the LLM's raw arguments beyond a short search phrase, and secrets (keyring)
are only substituted into the launch arguments at the last moment.
"""

import asyncio
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from action.skills.config import BUILTIN_NAMES, BUILTIN_TITLES, SECRET_PLACEHOLDER, SKILL_NAMES, SkillConfig, SkillsConfig
from core.credentials import KeyStore
from core.paths import key_service
from core.llm_config import ConfigError
from core.persona import load_yaml_model

log = logging.getLogger(__name__)


def _clean(value: object) -> str:
    """LLM-controlled text for log lines: short, one line."""
    return repr(str(value)[:40])

SKILL_KEY_SERVICE = key_service("skill")
MAX_QUERY_CHARS = 100
# Which argument names the target for each skill.
TARGET_PARAM = {"open_app": "app", "play_music": "app", "open_game": "game"}
VERB = {"open_app": "打开", "play_music": "播放音乐：", "open_game": "启动游戏"}

# (title, detail, timeout seconds) -> user allowed. Implemented by the pet window's dialog.
Confirm = Callable[[str, str, float], Awaitable[bool]]
# (exe, args, url): start a program (no shell) or open a URL scheme.
Launcher = Callable[[str | None, list[str], str | None], None]


@dataclass
class SkillResult:
    name: str
    ok: bool
    # Safe to show and speak: never contains credentials.
    message: str


def launch(exe: str | None, args: list[str], url: str | None) -> None:
    if url:
        os.startfile(url)  # type: ignore[attr-defined]  # Windows: ShellExecute, handles registered URL schemes
        return
    assert exe
    if not Path(exe).is_absolute():
        # Resolve once ourselves so CreateProcess doesn't pick a same-named file from the backend's directory.
        found = shutil.which(exe)
        if found is None:
            raise FileNotFoundError(exe)
        exe = found
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(  # noqa: S603 - argv list, never a shell
        [exe, *args],
        shell=False,
        close_fds=True,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(exe).parent) if Path(exe).is_absolute() else None,
    )


class SkillRunner:
    def __init__(
        self,
        *,
        config_path: Path,
        confirm: Confirm,
        launcher: Launcher = launch,
        keys: KeyStore | None = None,
    ) -> None:
        self._path = config_path
        self._confirm = confirm
        self._launch = launcher
        self._keys = keys or KeyStore(SKILL_KEY_SERVICE)

    def _config(self) -> SkillsConfig:
        try:
            return load_yaml_model(self._path, SkillsConfig)
        except ConfigError as e:
            log.warning("skills.yaml 有误，技能已停用：%s", e)
            return SkillsConfig(enabled=False)

    @staticmethod
    def _usable(cfg: SkillsConfig) -> dict[str, SkillConfig]:
        if not cfg.enabled:
            return {}
        return {n: s for n, s in cfg.skills.items() if n in SKILL_NAMES and s.enabled and s.targets}

    def enabled_builtins(self) -> set[str]:
        """Built-in actions (BUILTIN_NAMES) the LLM may be offered: on unless switched off in skills.yaml."""
        cfg = self._config()
        if not cfg.enabled:
            return set()
        return {n for n in BUILTIN_NAMES if n not in cfg.skills or cfg.skills[n].enabled}

    async def allow_builtin(self, name: str) -> str | None:
        """Whitelist check and optional confirmation for a built-in action, right before it runs.
        None = go ahead; otherwise a short line that is safe to show."""
        cfg = self._config()
        skill = cfg.skills.get(name, SkillConfig())
        if name not in BUILTIN_NAMES or not cfg.enabled or not skill.enabled:
            log.warning("拒绝未注册或已停用的内置动作：%s", _clean(name))
            return "这个我不会做。"
        if skill.confirm:
            log.info("内置动作 %s：等待确认", name)
            if not await self._confirm("桌宠想执行一个操作", BUILTIN_TITLES[name], cfg.confirm_timeout_seconds):
                log.info("内置动作 %s：未获确认，不执行", name)
                return "好吧，那我不弄了。"
        return None

    def names(self) -> set[str]:
        return set(self._usable(self._config()))

    def catalog(self) -> list[str]:
        """One prompt line per usable skill, listing the ids the LLM may pick."""
        return list(self.offer().values())

    def labels(self) -> list[str]:
        """Names of all usable commands (the 指令 page's 名称), which trigger them in keyword mode."""
        seen: dict[str, None] = {}
        for skill in self._usable(self._config()).values():
            for t in skill.targets.values():
                seen.setdefault(t.label)
        return list(seen)

    def offer(self, text: str | None = None, keywords: dict[str, list[str]] | None = None) -> dict[str, str]:
        """Skill name -> prompt line. With `text`, only the targets whose name appears in it are listed
        (all targets of a skill when one of its `keywords` appears instead); skills with no hit are left out."""
        out: dict[str, str] = {}
        low = "".join(text.lower().split()) if text is not None else ""
        for name, skill in self._usable(self._config()).items():
            targets = skill.targets
            if text is not None:
                hit = {tid: t for tid, t in targets.items() if "".join(t.label.lower().split()) in low}
                if not hit and not any(w.lower() in text.lower() for w in (keywords or {}).get(name, [])):
                    continue
                targets = hit or targets
            options = "、".join(f"{tid}（{t.label}）" for tid, t in targets.items())
            param = TARGET_PARAM[name]
            if name == "play_music":
                out[name] = f'play_music：播放音乐。args: {{"query": "歌名或歌手", "{param}": 可选，{options}}}'
            elif name == "open_app":
                out[name] = f'open_app：打开程序。args: {{"{param}": {options}}}'
            else:
                out[name] = f'open_game：启动游戏。args: {{"{param}": {options}}}'
        return out

    async def run(self, name: str, args: dict[str, Any]) -> SkillResult:
        cfg = self._config()
        skill = self._usable(cfg).get(name)
        if skill is None:
            log.warning("拒绝未注册或已停用的技能：%s", _clean(name))
            return SkillResult(name, False, "这个我不会做。")
        param = TARGET_PARAM[name]
        raw_id = args.get(param)
        if raw_id is None and len(skill.targets) == 1:
            raw_id = next(iter(skill.targets))
        target = skill.targets.get(raw_id) if isinstance(raw_id, str) else None
        if target is None:
            log.warning("技能 %s 的目标不在白名单：%s", name, _clean(raw_id))
            return SkillResult(name, False, "我不知道你要的是哪一个。")
        query = ""
        if name == "play_music":
            q = args.get("query")
            if q is not None and not isinstance(q, str):
                return SkillResult(name, False, "我没听清要放什么。")
            query = "".join(c for c in (q or "") if c.isprintable()).strip()[:MAX_QUERY_CHARS]
        url = target.url
        if query and target.search_url:
            url = target.search_url.replace("{query}", quote(query, safe=""))

        what = f"{VERB[name]}{target.label}" + (f"（{query}）" if query and name == "play_music" else "")
        if skill.confirm:
            log.info("技能 %s -> %s：等待确认", name, raw_id)
            if not await self._confirm("桌宠想执行一个操作", what, cfg.confirm_timeout_seconds):
                log.info("技能 %s -> %s：未获确认，不执行", name, raw_id)
                return SkillResult(name, False, "好吧，那我不弄了。")

        launch_args = list(target.args)
        if target.credential:
            secret = await asyncio.to_thread(self._keys.get, target.credential)
            if not secret:
                log.warning("技能 %s -> %s：凭据管理器里没有 %s/%s", name, raw_id, SKILL_KEY_SERVICE, target.credential)
                return SkillResult(name, False, f"{target.label}的登录信息还没设置。")
            launch_args = [a.replace(SECRET_PLACEHOLDER, secret) for a in launch_args]
        try:
            await asyncio.to_thread(self._launch, target.exe, launch_args, url)
        except Exception as e:  # noqa: BLE001 - OSError and friends; the message may echo launch args
            log.warning("技能 %s -> %s 启动失败：%s", name, raw_id, type(e).__name__)
            return SkillResult(name, False, f"没能打开{target.label}。")
        log.info("技能 %s -> %s 已执行", name, raw_id)
        return SkillResult(name, True, what)
