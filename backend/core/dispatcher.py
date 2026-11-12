"""Routes bus events through the rule layer to the agent (cheap before expensive, CLAUDE.md §4.4)."""

import random
import time
from typing import TYPE_CHECKING, Awaitable, Callable

from core.agent import Agent, AgentOutput, Labels, ReactionRules
from core.events import Event
from core.hotkeys import HotkeyRules
from core.image_stash import ImageStash
from core.proactive import ProactiveGate

Emit = Callable[[AgentOutput, Event], Awaitable[None]]
SetThinking = Callable[[bool], Awaitable[None]]

if TYPE_CHECKING:
    from core.camera_react import CameraReactor
    from core.screen_comment import ScreenCommenter

MAX_PENDING_LLM = 2
USER_EVENTS = ("user.text", "voice.utterance", "pet.interaction", "hotkey.pressed")


class Dispatcher:
    def __init__(
        self,
        *,
        agent: Agent,
        rules: ReactionRules,
        labels: Callable[[], Labels],
        emit: Emit,
        set_thinking: SetThinking,
        hotkeys: HotkeyRules | None = None,
        gate: ProactiveGate | None = None,
        screen: "ScreenCommenter | None" = None,  # noqa: F821 - avoid import cycle
        camera: "CameraReactor | None" = None,  # noqa: F821
        images: ImageStash | None = None,
    ) -> None:
        self._images = images
        self._agent = agent
        self._rules = rules
        self._labels = labels
        self._emit = emit
        self._set_thinking = set_thinking
        self._thinking = 0
        self._hotkeys = hotkeys
        self._gate = gate
        self.screen = screen
        self.camera = camera
        self.scheduler = None
        # Typed commands handled by code before the LLM: async (text) -> consumed?
        self.commands: list[Callable[[str], Awaitable[bool]]] = []

    @property
    def thinking(self) -> bool:
        return self._thinking > 0

    async def __call__(self, event: Event) -> None:
        if self._gate is not None and event.type in USER_EVENTS:
            self._gate.user_active()  # proactive remarks hold back while the user is engaging
        if event.type == "screen.changed":
            if self.screen is not None:
                output = await self.screen.handle(event, force=event.payload.get("reason") == "manual")
                if output is not None and output.text:
                    await self._emit(output, event)
            return
        if event.type == "camera.presence":
            if self.camera is not None:
                output = await self.camera.handle(event, force=event.payload.get("reason") in ("manual", "ask"))
                if output is not None and output.text:
                    await self._emit(output, event)
            return
        if event.type == "timer.idle":
            if event.payload.get("kind") == "action":
                await self._idle_action(event)
            else:
                await self._proactive(event)
            return
        if event.type == "user.text" and not event.payload.get("image_id"):
            for command in self.commands:
                if await command(str(event.payload.get("text") or "")):
                    return
        if event.type in ("user.text", "voice.utterance"):
            await self._ask_llm(event)
        elif event.type == "pet.interaction":
            decision = self._rules.decide(event, self._labels(), self._agent.chat_configured())
            if decision == "llm":
                await self._ask_llm(event)
            elif decision is not None:
                await self._emit(decision, event)
        elif event.type == "hotkey.pressed" and self._hotkeys is not None:
            await self._on_hotkey(event)

    async def _on_hotkey(self, event: Event) -> None:
        assert self._hotkeys is not None
        decision = self._hotkeys.decide(str(event.payload.get("id")))
        if decision is None:
            return
        kind, hk = decision
        if kind == "llm":
            if not self._agent.chat_configured():
                await self._emit(AgentOutput(text="（还没有指定聊天用的 LLM）"), event)
                return
            await self._ask_llm(event, user_text=hk.prompt.strip())
            return
        labels = self._labels()
        await self._emit(
            AgentOutput(
                text=self._hotkeys.pick(hk.lines) or "",
                expression=self._hotkeys.pick([x for x in hk.expressions if x in labels.expressions]),
                motion=self._hotkeys.pick([x for x in hk.motions if x in labels.motions]),
            ),
            event,
        )

    async def _idle_action(self, event: Event) -> None:
        """Rule layer: one random motion / expression out of the configured labels the current model has."""
        if self.thinking:
            return
        labels = self._labels()

        def pick(key: str, have: list[str]) -> str | None:
            raw = event.payload.get(key)
            usable = [x for x in raw if isinstance(x, str) and x in have] if isinstance(raw, list) else []
            return random.choice(usable) if usable else None

        output = AgentOutput(expression=pick("expressions", labels.expressions), motion=pick("motions", labels.motions))
        if output.expression or output.motion:
            await self._emit(output, event)

    async def _proactive(self, event: Event) -> None:
        """timer.idle from the scheduler, which already passed (and charged) the proactive gate."""
        situation = str(event.payload.get("situation") or "").strip()
        if not situation:
            return
        user_before = self._gate.last_user if self._gate is not None else None
        turn = await self._agent.respond_proactive(event, self._labels(), situation=situation)
        output = turn.output
        if self._gate is not None:
            if turn.failed:
                self._gate.failed()
                return
            if self._gate.last_user != user_before:
                return  # the user started talking meanwhile: drop it, and don't remember it either
        if output.text:
            await turn.commit()
            if self._gate is not None:
                self._gate.spoke()
            if self.scheduler is not None:
                self.scheduler.spoken(event.payload, output.text, time.time())
            await self._emit(output, event)

    async def _ask_llm(self, event: Event, *, user_text: str | None = None) -> None:
        # One in flight plus one queued; spamming Enter must not queue paid calls.
        if self._thinking >= MAX_PENDING_LLM:
            if event.type in ("user.text", "voice.utterance"):
                await self._emit(AgentOutput(text="（等我先回完上一句～）"), event)
            return
        self._thinking += 1
        if self._thinking == 1:
            await self._set_thinking(True)
        try:
            image_id = event.payload.get("image_id") if event.type == "user.text" else None
            if image_id:
                data_url = self._images.take(image_id) if self._images is not None else None
                if data_url is None:
                    output = AgentOutput(text="（这张图片已经过期了，再发一次吧）")
                else:
                    output = await self._agent.respond_image(event, self._labels(), data_url=data_url)
            else:
                output = await self._agent.respond(event, self._labels(), user_text=user_text)
        finally:
            self._thinking -= 1
            if self._thinking == 0:
                await self._set_thinking(False)
        await self._emit(output, event)
