"""Console API for the 小游戏 page (core/games). Matches live in memory; a backend restart ends them.

Nothing here returns what the pet keeps hidden (its items, dice, the card or gesture it is playing in a step
the player has not played yet, the word the player must guess), and the pet's prompt is built from views that
leave out what the player keeps hidden. Game talk is never written to the chat history or to memory.
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import require_token
from core.agent import AgentOutput
from core.games import GAME_NAMES, Auction, GameError, GameHall, LiarDice, Roulette, Rps, Taboo
from core.games import auction as auction_game
from core.games import liar_dice as dice_game
from core.games import roulette as roulette_game
from core.games import rps as rps_game
from core.games import taboo as taboo_game
from core.games.common import JSON_RULE, clean_say
from core.llm_config import ConfigError
from core.status_config import AFFECTION

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/games", dependencies=[Depends(require_token)])

WIN_REWARD = 0.5
# The player won a game the pet itself proposed.
INVITED_WIN_REWARD = 1.0
PET_CHOICE_REWARD = 0.5


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsIn(Strict):
    voice: bool | None = None
    invite: bool | None = None


class StartIn(Strict):
    # False: the pet plays the built-in strategy and says nothing (no LLM calls).
    use_llm: bool = True


class RouletteStartIn(StartIn):
    mode: Literal["classic", "custom"] = "classic"
    bullets: int = Field(default=roulette_game.CLASSIC_BULLETS, ge=roulette_game.BULLET_RANGE[0], le=roulette_game.BULLET_RANGE[1])
    player_hp: int = Field(default=roulette_game.CLASSIC_HP, ge=roulette_game.HP_RANGE[0], le=roulette_game.HP_RANGE[1])
    pet_hp: int = Field(default=roulette_game.CLASSIC_HP, ge=roulette_game.HP_RANGE[0], le=roulette_game.HP_RANGE[1])


class RouletteMoveIn(Strict):
    action: Literal["shoot", "item"]
    target: Literal["self", "opponent"] | None = None
    item: str | None = Field(default=None, max_length=16)
    guess: Literal["big", "small"] | None = None


class RpsPickIn(Strict):
    choice: Literal["rock", "scissors", "paper"]


class TextIn(Strict):
    text: str = Field(min_length=1, max_length=200)


class DiceMoveIn(Strict):
    action: Literal["bid", "call"]
    count: int | None = Field(default=None, ge=1, le=10)
    face: int | None = Field(default=None, ge=1, le=6)


class AuctionPlayIn(Strict):
    card: int = Field(ge=1, le=13)


class TabooStartIn(Strict):
    # Who describes the word; the other side guesses.
    describer: Literal["player", "pet", "random"] = "random"


def _hall(request: Request) -> GameHall:
    return request.app.state.games


def _status_on(request: Request) -> bool:
    try:
        return request.app.state.status.config().enabled
    except ConfigError:
        return False


def _state(request: Request) -> dict[str, Any]:
    hall = _hall(request)
    invite = hall.pending_invite()
    out: dict[str, Any] = {
        "llm_available": hall.brain.available(),
        "pet_name": hall.brain.pet_name(),
        "status_on": _status_on(request),
        "settings": dict(hall.settings),
        "records": {k: dict(v) for k, v in hall.records.items()},
        "invited": invite["game"] if invite else None,
    }
    for key, game in hall.games.items():
        view = None
        if game is not None:
            view = game.view()
            view["end"] = game.end
            view["invited"] = game.invited
        out[key] = view
    return out


def _game(hall: GameHall, key: str) -> Any:
    game = hall.games[key]
    if game is None:
        raise HTTPException(409, "还没有开局")
    return game


async def _say(request: Request, *lines: str) -> None:
    """The pet's game lines show in its bubble; spoken too when the 语音 setting of the games is on.
    Never goes through the agent: nothing is stored in the chat history or in memory."""
    hub = request.app.state.pet_hub
    for text in lines:
        if not text:
            continue

        async def announce(text: str = text) -> None:
            await hub.broadcast({"type": "output", "event_id": None, "output": AgentOutput(text=text).model_dump()})

        if _hall(request).settings["voice"] and hub.speaker and hub.clients:
            await hub.speaker.say(text, None, announce)
        else:
            await announce()


def _outcome(game: Any) -> str:
    if game.key == "taboo":
        return "你们配合成功，猜中了" if game.result == "player" else "没能在规定的轮数里猜中"
    return {"player": "主人赢了，你输了", "pet": "你赢了，主人输了", "draw": "打平了"}[game.result]


async def _settle(request: Request, game: Any) -> str:
    """Once per finished match: tally it, let the pet say a closing line, hand out the affection reward.

    The player won: +0.5 好感度 no matter what (+1 when the pet had proposed the game). Otherwise the pet
    decides whether the game made it like the player a little more. Only while the status bar is on.
    Returns the closing line.
    """
    if game.result is None or game.end is not None:
        return ""
    hall = _hall(request)
    hall.record(game)
    on = _status_on(request)
    won = game.result == "player"
    amount = (INVITED_WIN_REWARD if game.invited else WIN_REWARD) if won else 0.0
    reason = "接受邀请并赢了游戏" if won and game.invited else "小游戏赢了"
    say = ""
    if game.use_llm:
        ask_affection = on and not won
        rules = (
            f"【小游戏】你和主人刚玩完一局「{GAME_NAMES[game.key]}」：{_outcome(game)}。"
            "用你的人设说一句赛后的话（赢了可以得意，输了可以不服气或夸主人，打平可以约下一把）。这只是游戏，不要当真生气。\n"
            f"{JSON_RULE}\n"
            + ('{"say": "…", "affection": true 或 false}\n' if ask_affection else '{"say": "…"}\n')
            + "say 不超过 30 个字，口语化；只用文字，不要用 emoji、颜文字和括号动作描写。"
            + (
                "\naffection：这一局玩下来，你对主人的好感有没有因此多一点（比如玩得开心、主人有风度、陪了你很久）。"
                "true = 多了一点，false = 没有变化。按你的人设和真实感受决定，不必每次都是 true。"
                if ask_affection
                else ""
            )
        )
        move = await hall.brain.ask(rules, f"{game.summary()}。说一句赛后的话。") or {}
        say = clean_say(move.get("say"))
        if ask_affection and move.get("affection") is True:
            amount, reason = PET_CHOICE_REWARD, "一起玩游戏很开心"
    reward = None
    if on and amount:
        try:
            change = request.app.state.status.grant(AFFECTION, amount, reason)
        except Exception:
            log.exception("小游戏奖励好感度失败")
            change = None
        if change is not None:
            reward = {"name": change.name, "delta": change.delta, "value": change.value, "reason": reason}
            # Same pop next to the pet's head as for a change made in conversation.
            await request.app.state.pet_hub.broadcast(
                {"type": "output", "event_id": None, "output": AgentOutput(status=[change]).model_dump()}
            )
    game.end = {"say": say, "reward": reward}
    log.info("小游戏「%s」结束：%s%s", GAME_NAMES[game.key], game.result, f"，好感度 +{amount:g}" if reward else "")
    return say


@router.get("")
def get_games(request: Request) -> dict[str, Any]:
    return _state(request)


@router.put("/settings")
def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    _hall(request).set_settings(**body.model_dump())
    return _state(request)


def _quit(key: str):
    async def quit_game(request: Request) -> dict[str, Any]:
        _hall(request).games[key] = None
        return _state(request)

    return quit_game


for _key in GAME_NAMES:
    router.post(f"/{_key}/quit")(_quit(_key))


# --- 恶魔轮盘赌 ------------------------------------------------------------------------------


@router.post("/roulette/start")
async def roulette_start(request: Request, body: RouletteStartIn) -> dict[str, Any]:
    hall = _hall(request)
    async with hall.lock:
        try:
            hall.start("roulette", Roulette(**body.model_dump(), rng=hall.rng))
        except GameError as e:
            raise HTTPException(422, str(e)) from None
        return _state(request)


@router.post("/roulette/move")
async def roulette_move(request: Request, body: RouletteMoveIn) -> dict[str, Any]:
    """The player's move: use an item or shoot."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "roulette")
        try:
            game.act("player", body.model_dump())
        except GameError as e:
            raise HTTPException(409, str(e)) from None
        closing = await _settle(request, game)
        state = _state(request)
    await _say(request, closing)
    return state


@router.post("/roulette/pet")
async def roulette_pet(request: Request) -> dict[str, Any]:
    """The pet makes one step (the page calls this while it is the pet's turn, pacing the animation)."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "roulette")
        if game.winner is not None or game.turn != "pet":
            return _state(request)
        entry, say = None, ""
        if game.use_llm:
            move = await hall.brain.ask(roulette_game.RULES, game.situation())
            if move is not None and hall.games["roulette"] is game:
                try:
                    entry = game.act("pet", move)
                    say = clean_say(move.get("say"))
                except GameError as e:
                    log.info("小游戏：模型选了不合法的一步（%s），由内置策略代打", e)
        if hall.games["roulette"] is not game:  # the player quit or restarted while the model was thinking
            return _state(request)
        if entry is None:
            entry = game.act("pet", roulette_game.auto_move(game, hall.rng))
        if say:
            entry["say"] = say
        closing = await _settle(request, game)
        state = _state(request)
    await _say(request, say, closing)
    return state


# --- 骗子猜拳 --------------------------------------------------------------------------------


@router.post("/rps/start")
async def rps_start(request: Request, body: StartIn) -> dict[str, Any]:
    hall = _hall(request)
    async with hall.lock:
        hall.start("rps", Rps(use_llm=body.use_llm))
        return _state(request)


@router.post("/rps/pick")
async def rps_pick(request: Request, body: RpsPickIn) -> dict[str, Any]:
    """The player's choice for the current step. The pet chooses from a prompt built before this choice is
    applied (it never contains it), then both are revealed together in the returned state."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "rps")
        try:
            game.check("player", body.choice)
        except GameError as e:
            raise HTTPException(409, str(e)) from None
        choice, say = None, ""
        if game.use_llm:
            move = await hall.brain.ask(f"{rps_game.RULES}\n{rps_game.PICK_FORMAT}", game.situation())
            if move is not None and move.get("choice") in game.options("pet"):
                choice, say = move["choice"], clean_say(move.get("say"))
            elif move is not None:
                log.info("小游戏：模型选了不能选的手势，由内置策略代打")
        if hall.games["rps"] is not game:
            return _state(request)
        game.submit(body.choice, choice or rps_game.auto_pick(game, hall.rng))
        game.say("pet", say)
        closing = await _settle(request, game)
        state = _state(request)
    await _say(request, say, closing)
    return state


@router.post("/rps/chat")
async def rps_chat(request: Request, body: TextIn) -> dict[str, Any]:
    """Before the final throw the player may talk (and bluff) up to five times; the pet answers each."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "rps")
        try:
            game.check_talk()
        except GameError as e:
            raise HTTPException(409, str(e)) from None
        game.talk_left -= 1
        game.say("player", body.text)
        say = ""
        if game.use_llm:
            move = await hall.brain.ask(f"{rps_game.RULES}\n{rps_game.CHAT_FORMAT}", game.situation())
            say = clean_say((move or {}).get("say"), 80)
        if hall.games["rps"] is game:
            game.say("pet", say or "……")
        state = _state(request)
    await _say(request, say)
    return state


# --- 吹牛骰 ----------------------------------------------------------------------------------


@router.post("/dice/start")
async def dice_start(request: Request, body: StartIn) -> dict[str, Any]:
    hall = _hall(request)
    async with hall.lock:
        hall.start("dice", LiarDice(use_llm=body.use_llm, rng=hall.rng))
        return _state(request)


@router.post("/dice/move")
async def dice_move(request: Request, body: DiceMoveIn) -> dict[str, Any]:
    """The player bids ("至少 count 个 face 点") or calls."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "dice")
        try:
            game.act("player", body.model_dump())
        except GameError as e:
            raise HTTPException(409, str(e)) from None
        closing = await _settle(request, game)
        state = _state(request)
    await _say(request, closing)
    return state


@router.post("/dice/pet")
async def dice_pet(request: Request) -> dict[str, Any]:
    """The pet bids or calls (the page calls this while it is the pet's turn)."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "dice")
        if game.phase != "bid" or game.turn != "pet":
            return _state(request)
        done, say = None, ""
        if game.use_llm:
            move = await hall.brain.ask(dice_game.RULES, game.situation())
            if move is not None and hall.games["dice"] is game:
                try:
                    done = game.act("pet", move)
                    say = clean_say(move.get("say"))
                except GameError as e:
                    log.info("小游戏：模型报了不合法的数（%s），由内置策略代打", e)
        if hall.games["dice"] is not game:
            return _state(request)
        if done is None:
            done = game.act("pet", dice_game.auto_move(game, hall.rng))
        if say:
            (game.bids[-1] if done is game.bids[-1] else done)["say"] = say
        closing = await _settle(request, game)
        state = _state(request)
    await _say(request, say, closing)
    return state


@router.post("/dice/next")
async def dice_next(request: Request) -> dict[str, Any]:
    """After the cups were lifted: roll again; whoever lost a die bids first."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "dice")
        try:
            game.next_round()
        except GameError as e:
            raise HTTPException(409, str(e)) from None
        return _state(request)


# --- 拍卖数字牌 ------------------------------------------------------------------------------


@router.post("/auction/start")
async def auction_start(request: Request, body: StartIn) -> dict[str, Any]:
    hall = _hall(request)
    async with hall.lock:
        hall.start("auction", Auction(use_llm=body.use_llm, rng=hall.rng))
        return _state(request)


@router.post("/auction/play")
async def auction_play(request: Request, body: AuctionPlayIn) -> dict[str, Any]:
    """The player's card for this round. The pet picks its card from a prompt built before this card is
    applied, then both are shown together."""
    hall = _hall(request)
    async with hall.lock:
        game = _game(hall, "auction")
        try:
            game.check("player", body.card)
        except GameError as e:
            raise HTTPException(409, str(e)) from None
        card, say = None, ""
        if game.use_llm:
            move = await hall.brain.ask(auction_game.RULES, game.situation())
            if move is not None:
                picked = auction_game.model_card(move)
                if picked in game.hands["pet"]:
                    card, say = picked, clean_say(move.get("say"))
                else:
                    log.info("小游戏：模型出了手里没有的牌，由内置策略代打")
        if hall.games["auction"] is not game:
            return _state(request)
        entry = game.submit(body.card, card or auction_game.auto_bid(game, hall.rng))
        if say:
            entry["say"] = say
        closing = await _settle(request, game)
        state = _state(request)
    await _say(request, say, closing)
    return state


# --- 禁忌词 ----------------------------------------------------------------------------------


async def _pet_clue(hall: GameHall, game: Taboo) -> str:
    """The pet's description for this round, checked by the program: a clue that contains the word or a
    forbidden word never reaches the player (one retry, then a harmless stock clue)."""
    note = ""
    for _ in range(2):
        move = await hall.brain.ask(taboo_game.DESCRIBE_RULES, game.describe_situation() + note)
        say = clean_say((move or {}).get("say"), 80)
        if not say:
            break
        bad = game.violation(say)
        if bad is None:
            return say
        log.info("小游戏：模型的描述里出现了禁忌词，重试")
        note = f"\n注意：你刚才的描述里出现了「{bad}」，犯规了。换一个完全不含目标词和禁忌词的说法。"
    return game.fallback_clue()


@router.post("/taboo/start")
async def taboo_start(request: Request, body: TabooStartIn) -> dict[str, Any]:
    hall = _hall(request)
    async with hall.lock:
        if not hall.brain.available():
            raise HTTPException(409, "禁忌词需要聊天用的 LLM，请先在「AI 模型」页指定")
        describer = hall.rng.choice(("player", "pet")) if body.describer == "random" else body.describer
        game = Taboo(describer=describer, rng=hall.rng)
        hall.start("taboo", game)
        clue = ""
        if describer == "pet":
            clue = await _pet_clue(hall, game)
            game.clue("pet", clue)
        state = _state(request)
    await _say(request, clue)
    return state


@router.post("/taboo/say")
async def taboo_say(request: Request, body: TextIn) -> dict[str, Any]:
    """The player's turn: a guess when the pet describes, a description when the player does."""
    hall = _hall(request)
    async with hall.lock:
        game: Taboo = _game(hall, "taboo")
        try:
            game.check_open()
        except GameError as e:
            raise HTTPException(409, str(e)) from None
        say = ""
        if game.describer == "pet":
            if not game.guess("player", body.text) and game.result is None:
                say = await _pet_clue(hall, game)
                if hall.games["taboo"] is game:
                    game.clue("pet", say)
        else:
            bad = game.violation(body.text)
            if bad is not None:
                raise HTTPException(409, f"描述里出现了「{bad}」，这是不能说的词，换个说法")
            game.clue("player", body.text)
            # The guesser's prompt holds only what was said, never the word.
            move = await hall.brain.ask(taboo_game.GUESS_RULES, game.guess_situation()) or {}
            guess = clean_say(move.get("guess"), 20)
            say = clean_say(move.get("say"))
            if hall.games["taboo"] is game:
                game.guess("pet", guess or "想不出来")
                say = say or (f"是{guess}吗" if guess else "")
        closing = await _settle(request, game) if hall.games["taboo"] is game else ""
        state = _state(request)
    await _say(request, say, closing)
    return state
