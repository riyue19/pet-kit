"""Runs the tool calls of a reply: launchable skills (action/skills) and the built-in actions.

Split out of api/app.py. Built-in actions start nothing: they show something on the pet side, ask the camera
for a look or invite the user to a mini game. Both kinds are whitelisted in config/skills.yaml (CLAUDE.md 4.6).
"""

import logging
from collections.abc import Callable
from typing import Any

from action.builtin import SET_STYLE_LINE, SHOW_CODE_LINE, code_message, css_message
from action.skills import SkillResult, SkillRunner
from action.skills.config import BUILTIN_NAMES
from api.pet_ws import PetHub
from core.agent import AgentOutput
from core.event_bus import EventBus
from core.events import Event, make_event
from core.games import GAME_NAMES, GameHall
from core.logs import current_event

log = logging.getLogger(__name__)

LOOK_CAMERA_LINE = (
    "look_camera：看一眼主人（抽一帧摄像头画面）。只在主人让你「看看我 / 你看得到我吗 / 我现在什么样」时用，args: {}。"
    "text 只说一句「我看看～」这类，不要描述你还没看到的东西，看到的内容会随后另外说出来。"
)
INVITE_GAME_LINE = (
    "invite_game：邀请主人玩一局小游戏，主人会看到一个弹窗决定玩不玩。args: {\"game\": \""
    + " | ".join(f"{k}（{v}）" for k, v in GAME_NAMES.items())
    + "\"}，只填英文名。主人说无聊、想玩游戏时用；text 里用你的口吻发出邀请，说清楚想玩哪个，不要说已经开始了。"
)
UNPROMPTED_INVITE_NOTE = "这一条不需要主人先开口：你自己想玩的时候可以用，但只是偶尔，大多数时候不要用。"


class ToolRunner:
    def __init__(
        self,
        *,
        skills: SkillRunner,
        hub: PetHub,
        bus: EventBus,
        games: GameHall,
        camera_working: Callable[[], bool],
    ) -> None:
        self._skills = skills
        self._hub = hub
        self._bus = bus
        self._games = games
        # The camera is open and has judged presence at least once.
        self._camera_working = camera_working

    # --- what the LLM is offered (Agent.extra_tools / Agent.proactive_tools) ---

    def extra_tools(self) -> dict[str, str]:
        """Built-in actions for a turn where the user spoke: name -> prompt line. Only the usable ones."""
        tools = {"show_code": SHOW_CODE_LINE, "set_style": SET_STYLE_LINE}
        if self._games.can_invite() and self._hub.clients:
            tools["invite_game"] = INVITE_GAME_LINE
        if self._camera_working():
            tools["look_camera"] = LOOK_CAMERA_LINE
        # Only the ones left on in config/skills.yaml (指令 page).
        on = self._skills.enabled_builtins()
        return {name: line for name, line in tools.items() if name in on}

    def proactive_tools(self) -> dict[str, str]:
        """Built-in actions for a turn the pet started itself."""
        if "invite_game" in self._skills.enabled_builtins() and self._games.can_invite(unprompted=True) and self._hub.clients:
            return {"invite_game": INVITE_GAME_LINE + UNPROMPTED_INVITE_NOTE}
        return {}

    # --- running them ---

    async def run(self, output: AgentOutput, event: Event, result: dict) -> None:
        """Runs output.tool_calls in order, recording each outcome in the event's result as it goes."""
        current_event.set(event.id)
        done: list[dict[str, Any]] = []

        def record(name: str, ok: bool, message: str) -> None:
            done.append({"name": name, "ok": ok, "message": message})
            result["skills"] = done
            self._bus.record_result(event.id, result)

        for call in output.tool_calls:
            if call.name in BUILTIN_NAMES:
                # Same whitelist as launchable skills (config/skills.yaml): switched off, or the user said no.
                refusal = await self._skills.allow_builtin(call.name)
                if refusal is not None:
                    record(call.name, False, refusal)
                    await self._notice(refusal, event)
                    continue
                ok, message = await self._builtin(call.name, call.args, event)
                record(call.name, ok, message)
                continue
            try:
                res = await self._skills.run(call.name, call.args)
            except Exception:
                log.exception("技能 %s 执行出错", call.name)
                res = SkillResult(call.name, False, "出了点问题，没弄成。")
            record(res.name, res.ok, res.message)
            if not res.ok:
                try:
                    await self._notice(res.message, event)
                except Exception:
                    log.exception("技能失败提示发送出错")

    async def _notice(self, text: str, event: Event) -> None:
        """A short bubble saying why something was not done; dropped if the user pressed 停止说话 meanwhile."""
        if event.ts >= self._hub.stopped_at:
            await self._hub.emit(AgentOutput(text=f"（{text}）"), event)

    async def _builtin(self, name: str, args: dict[str, Any], event: Event) -> tuple[bool, str]:
        """One built-in action; (ok, message for the event log)."""
        if name == "look_camera":
            # The camera reactor does the look (and speaks the result).
            self._bus.publish(make_event("camera.presence", {"state": "arrived", "away_seconds": 0, "reason": "ask"}))
            return True, ""
        if name == "invite_game":
            # Only asks the user (a dialog on the pet side) whether to open the 小游戏 page.
            key = args.get("game")
            unprompted = event.type == "timer.idle"
            if key not in GAME_NAMES or not self._games.can_invite(unprompted=unprompted):
                return False, "现在不能邀请"
            self._games.invite(key, by_pet=unprompted)
            await self._hub.broadcast({"type": "game_invite", "game": key, "name": GAME_NAMES[key], "pet": self._games.brain.pet_name()})
            return True, ""
        # show_code / set_style: only show something on the pet side (see action/builtin.py).
        msg = (code_message if name == "show_code" else css_message)(args)
        if isinstance(msg, str):
            log.warning("%s 被拒绝：%s", name, msg)
            await self._notice(msg, event)
            return False, msg
        await self._hub.broadcast(msg)
        return True, ""
