import json
import random

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core import llm_chain
from core.games import Auction, GameBrain, GameError, GameHall, LiarDice, Roulette, Rps, Taboo
from core.games import auction as auction_game
from core.games import liar_dice as dice_game
from core.games.brain import first_object
from core.games.common import clean_say
from core.games.roulette import ITEMS, auto_move
from core.games.rps import TALK_ROUNDS, auto_pick, outcome
from core.credentials import KeyStore
from providers.base import ChatResult

TOKEN = "test-token"
CONFIG = """server:
  port: 8765
llm:
  profiles:
    - name: main
      base_url: https://api.example.com/v1
      model: m
  roles:
    chat: main
"""


class Scripted:
    calls: list = []
    replies: list = []

    def __init__(self, profile, key):
        pass

    async def chat(self, messages, **_kw):
        Scripted.calls.append(messages)
        reply = Scripted.replies.pop(0) if Scripted.replies else "不是 JSON"
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(text=reply, model="m", latency_ms=1)

    async def aclose(self):
        pass


@pytest.fixture
def client(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "persona.yaml").write_text("name: 小白\n", encoding="utf-8")
    Scripted.calls, Scripted.replies = [], []
    llm_chain.reset_cooldowns()
    return TestClient(create_app(config_path=tmp_path / "backend.yaml", token=TOKEN, provider_factory=Scripted))


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def post(client, path, body=None):
    return client.post(f"/api/games{path}", headers=auth(), json=body)


def hall_of(client) -> GameHall:
    return client.app.state.games


def situations() -> str:
    """Every user message sent to the model (the situation as the pet sees it)."""
    return "\n".join(call[1]["content"] for call in Scripted.calls)


def game(chamber, turn="player", items=(), pet_items=(), **kw):
    g = Roulette(rng=random.Random(1), **kw)
    g.chamber, g.turn = list(chamber), turn
    g.items = {"player": list(items), "pet": list(pet_items)}
    return g


# --- shared ---------------------------------------------------------------------------------


def test_clean_say_strips_emoji_and_newlines():
    assert clean_say("接好了哦 💥✋\n 看招") == "接好了哦 看招"
    assert clean_say("你好，世界！（笑）") == "你好，世界！（笑）"
    assert clean_say(None) == "" and clean_say("啊" * 100) == "啊" * 60


def test_first_object():
    assert first_object('```json\n{"action": "shoot"}\n```') == {"action": "shoot"}
    assert first_object('好的 {坏的} {"a": 1} 结尾') == {"a": 1}
    assert first_object("没有") is None


# --- 恶魔轮盘赌 ------------------------------------------------------------------------------


def test_classic_setup():
    g = Roulette(mode="classic", bullets=9, player_hp=1, pet_hp=1, rng=random.Random(3))
    assert g.bullets == 6 and g.hp == {"player": 4, "pet": 4}
    assert len(g.chamber) == 6 and 1 <= sum(g.chamber) <= 5
    assert len(g.items["player"]) == 3 and len(g.items["pet"]) == 3
    assert all(i in ITEMS for i in g.items["player"] + g.items["pet"])
    assert g.turn == g.first and g.log[0]["kind"] == "load" and g.log[0]["side"] == g.first


def test_first_player_is_a_coin_flip():
    firsts = [Roulette(rng=random.Random(s)).first for s in range(200)]
    assert 60 < firsts.count("player") < 140


def test_custom_limits():
    g = Roulette(mode="custom", bullets=10, player_hp=2, pet_hp=7, rng=random.Random(1))
    assert len(g.chamber) == 10 and g.max_hp == {"player": 2, "pet": 7}
    with pytest.raises(GameError):
        Roulette(mode="custom", bullets=1)
    with pytest.raises(GameError):
        Roulette(mode="custom", player_hp=0)


def test_shooting():
    g = game([0, 1, 1, 0, 1])
    g.shoot("player", "self")
    assert g.turn == "player" and g.hp["player"] == 4  # blank at yourself: keep the turn
    g.shoot("player", "self")
    assert g.turn == "pet" and g.hp["player"] == 3  # live: lose health and the turn
    g.shoot("pet", "opponent")
    assert g.turn == "player" and g.hp["player"] == 2
    g.shoot("player", "opponent")  # blank at the opponent still passes the turn
    assert g.turn == "pet" and g.hp["pet"] == 4


def test_turn_and_end_are_enforced():
    g = game([1, 1], player_hp=1, pet_hp=1, mode="custom")
    with pytest.raises(GameError):
        g.shoot("pet", "opponent")
    g.shoot("player", "opponent")
    assert g.winner == "player" and g.result == "player" and g.log[-1]["kind"] == "win"
    with pytest.raises(GameError):
        g.shoot("pet", "opponent")


def test_items():
    g = game([1, 0, 1, 0], items=["juice", "flash", "lens", "driver", "dice"])
    with pytest.raises(GameError):
        g.use("player", "juice")  # full health
    g.hp["player"] = 2
    g.use("player", "juice")
    assert g.hp["player"] == 3
    g.use("player", "lens")
    assert g.known["player"] == 1 and "live" not in g.log[-1]  # what was seen is not public
    g.use("player", "driver")
    assert g.chamber == [0, 1, 0] and g.log[-1]["live"] is True and g.known["player"] is None
    g.use("player", "flash")
    with pytest.raises(GameError):
        g.use("player", "flash")
    with pytest.raises(GameError):
        g.use("player", "dice")  # needs a guess
    before = g.hp["player"]
    e = g.use("player", "dice", "big")
    assert g.hp["player"] == before + (1 if e["ok"] else -1) and e["ok"] == (e["roll"] >= 4)
    assert g.items["player"] == [] and g.turn == "player"  # items don't end the turn


def test_flash_doubles_only_the_next_shot():
    g = game([1, 1, 0], items=["flash"])
    g.use("player", "flash")
    g.shoot("player", "opponent")
    assert g.hp["pet"] == 2 and not g.boost
    g.shoot("pet", "opponent")
    assert g.hp["player"] == 3
    g = game([0, 1, 0], items=["flash"])
    g.use("player", "flash")
    g.shoot("player", "self")  # blank: the grenade is spent, nobody is hurt
    assert g.hp == {"player": 4, "pet": 4} and not g.boost


def test_new_round_keeps_health_and_replaces_items():
    g = game([1], items=["juice"] * 5, pet_items=["lens"])
    g.shoot("player", "opponent")
    assert g.round == 2 and len(g.chamber) == 6 and g.hp == {"player": 4, "pet": 3}
    assert len(g.items["player"]) == 3 and len(g.items["pet"]) == 3  # old ones thrown away, three new
    load = g.log[-1]
    assert load["kind"] == "load" and load["round"] == 2 and g.turn == g.first == load["side"]
    firsts = set()
    for seed in range(40):  # the wheel decides again each round
        g = game([0], rng=None) if False else Roulette(rng=random.Random(seed))
        g.chamber, g.turn = [0], "player"
        g.shoot("player", "opponent")
        firsts.add(g.first)
    assert firsts == {"player", "pet"}


def test_views_hide_what_each_side_keeps_secret():
    g = game([1, 0, 1], turn="pet", items=["lens", "juice"], pet_items=["flash", "driver"])
    g.known = {"player": 1, "pet": 1}
    view = g.view()
    assert "chamber" not in view and view["pet_items"] == 2 and view["items"] == ["lens", "juice"]
    dumped = json.dumps(view, ensure_ascii=False)
    assert "flash" not in dumped and "driver" not in dumped
    seen = g.situation()
    assert "flash（震爆弹）" in seen and "juice" not in seen and "lens" not in seen
    assert "主人还有 2 个道具" in seen and "当前这一发是真弹" in seen


def test_roulette_auto_move():
    rng = random.Random(1)
    g = game([0, 1], turn="pet")
    g.known["pet"] = 0
    assert auto_move(g, rng) == {"action": "shoot", "target": "self"}
    g.known["pet"] = 1
    assert auto_move(g, rng) == {"action": "shoot", "target": "opponent"}
    for seed in range(100):
        rng = random.Random(seed)
        g, steps = Roulette(rng=rng), 0
        while g.winner is None:
            move = auto_move(g, rng) if g.turn == "pet" else {"action": "shoot", "target": rng.choice(["self", "opponent"])}
            g.act(g.turn, move)
            steps += 1
            assert steps < 800


# --- 骗子猜拳 --------------------------------------------------------------------------------


def test_rps_flow():
    assert outcome("rock", "scissors") == 1 and outcome("scissors", "rock") == -1 and outcome("paper", "paper") == 0
    g = Rps()
    g.submit("rock", "paper")
    assert g.step == 2 and g.options("player") == ["scissors", "paper"] and g.options("pet") == ["rock", "scissors"]
    with pytest.raises(GameError):
        g.check_talk()  # talking belongs to 局 3
    with pytest.raises(GameError):
        g.submit("rock", "rock")  # the second gesture must differ from the first
    g.submit("scissors", "rock")
    assert g.step == 3 and g.options("player") == ["rock", "scissors"]
    g.check_talk()
    with pytest.raises(GameError):
        g.submit("paper", "rock")  # the final throw is one of your own two
    g.submit("rock", "paper")
    assert g.step == 4 and g.result == "pet" and g.final == {"player": "rock", "pet": "paper"}


def test_rps_auto_pick():
    rng = random.Random(2)
    g = Rps()
    g.submit("rock", "paper")
    g.submit("scissors", "scissors")
    # The player holds rock/scissors, the pet paper/scissors: paper wins one and loses one, scissors loses one and ties.
    assert auto_pick(g, rng) == "paper"


# --- 吹牛骰 ----------------------------------------------------------------------------------


def dice(player, pet, turn="player"):
    g = LiarDice(rng=random.Random(1))
    g.hands = {"player": list(player), "pet": list(pet)}
    g.dice = {"player": len(player), "pet": len(pet)}
    g.turn = turn
    return g


def test_dice_bidding_rules():
    g = dice([1, 2, 3, 4, 5], [6, 6, 6, 1, 1])
    with pytest.raises(GameError):
        g.call("player")  # nothing to call yet
    with pytest.raises(GameError):
        g.bid("player", 2, 7)
    with pytest.raises(GameError):
        g.bid("player", 11, 3)  # more than the dice on the table
    g.bid("player", 2, 3)
    with pytest.raises(GameError):
        g.bid("player", 3, 3)  # not the player's turn
    with pytest.raises(GameError):
        g.bid("pet", 2, 3)  # must be higher
    with pytest.raises(GameError):
        g.bid("pet", 2, 2)
    g.bid("pet", 2, 4)  # same count, higher face
    g.bid("player", 3, 1)  # larger count, any face
    assert g.turn == "pet" and [b["count"] for b in g.bids] == [2, 2, 3]


def test_dice_call_and_rounds():
    g = dice([6, 2, 3, 4, 5], [6, 6, 1, 1, 1])
    g.bid("player", 3, 6)
    reveal = g.call("pet")  # there are exactly three sixes: the bidder was right, the caller loses a die
    assert reveal["total"] == 3 and reveal["loser"] == "pet" and g.dice == {"player": 5, "pet": 4}
    assert g.phase == "reveal" and reveal["hands"]["pet"] == [6, 6, 1, 1, 1]
    with pytest.raises(GameError):
        g.bid("player", 1, 1)
    g.next_round()
    assert g.round == 2 and g.turn == "pet" and g.bids == [] and len(g.hands["pet"]) == 4

    g = dice([2, 2], [3], turn="pet")
    g.bid("pet", 2, 3)
    g.call("player")  # only one three: the bidder loses its last die
    assert g.winner == "player" and g.phase == "over" and g.result == "player"


def test_dice_views():
    g = dice([1, 2, 3, 4, 5], [6, 6, 6, 6, 6], turn="pet")
    view = g.view()
    assert view["hand"] == [1, 2, 3, 4, 5] and view["reveal"] is None
    assert "6, 6" not in json.dumps(view)
    seen = g.situation()
    assert "6、6、6、6、6" in seen and "1、2、3" not in seen and "由你先报数" in seen


def test_dice_auto_move_is_always_legal():
    for seed in range(150):
        rng = random.Random(seed)
        g, steps = LiarDice(rng=rng), 0
        while g.winner is None:
            if g.phase == "reveal":
                g.next_round()
                continue
            if g.turn == "pet":
                g.act("pet", dice_game.auto_move(g, rng))
            elif g.bids and rng.random() < 0.4:
                g.call("player")
            elif g.can_raise():
                last = g.bids[-1] if g.bids else {"count": 0, "face": 6}
                g.bid("player", *((last["count"], last["face"] + 1) if last["face"] < 6 else (last["count"] + 1, 1)))
            else:
                g.call("player")
            steps += 1
            assert steps < 3000


# --- 拍卖数字牌 ------------------------------------------------------------------------------


def test_auction_rules():
    g = Auction(rng=random.Random(1))
    assert len(g.pot) == 1 and len(g.deck) == 12 and g.hands["player"] == list(range(1, 14))
    prize = g.pot[0]
    with pytest.raises(GameError):
        g.check("player", 14)
    g.submit(13, 5)
    assert g.scores == {"player": prize, "pet": 0} and 13 not in g.hands["player"] and 5 not in g.hands["pet"]
    with pytest.raises(GameError):
        g.submit(13, 4)  # already spent
    second = g.pot[0]
    g.submit(7, 7)  # a tie: nobody scores, the prize stays and the next one joins it
    assert g.scores["player"] == prize and g.pot[0] == second and len(g.pot) == 2
    both = sum(g.pot)
    g.submit(1, 2)
    assert g.scores["pet"] == both and len(g.pot) == 1 and g.round == 4


def test_auction_ends_at_45_or_after_13_rounds():
    g = Auction(rng=random.Random(2))
    g.pot, g.deck = [13], list(range(1, 13))  # prizes come up 13, 12, 11, 10: 46 points after four rounds
    while g.result is None:
        g.submit(max(g.hands["player"]), min(g.hands["pet"]))
    assert g.result == "player" and g.scores["player"] >= 45 and len(g.history) < 13
    g = Auction(rng=random.Random(2))
    while g.result is None:
        g.submit(g.hands["player"][0], g.hands["pet"][0])  # all ties: 13 rounds, no points
    assert g.result == "draw" and len(g.history) == 13 and g.scores == {"player": 0, "pet": 0}


def test_auction_views_and_auto_bid():
    rng = random.Random(3)
    g = Auction(rng=rng)
    view = g.view()
    assert "deck" not in view and view["left"] == 12 and view["pet_hand"] == list(range(1, 14))
    assert f"桌上的奖励：{g.pot[0]}" in g.situation()
    assert auction_game.model_card({"card": "K"}) == 13 and auction_game.model_card({"card": "7"}) == 7
    while g.result is None:
        card = auction_game.auto_bid(g, rng)
        assert card in g.hands["pet"]
        g.submit(rng.choice(g.hands["player"]), card)


# --- 禁忌词 ----------------------------------------------------------------------------------

ENTRY = ("火锅", ("涮", "辣", "锅", "四川", "毛肚"))


def test_taboo_rules():
    g = Taboo(describer="pet", entry=ENTRY)
    assert g.violation("冬天围着吃的，很 辣") == "辣" and g.violation("火 锅") == "火锅"
    assert g.violation("冬天大家围着一起吃的") is None
    assert not g.guess("player", "烧烤") and g.round == 2 and g.result is None
    assert g.guess("player", "是火锅吧！") and g.result == "player"

    g = Taboo(describer="player", entry=ENTRY)
    for _ in range(10):
        g.guess("pet", "不知道")
    assert g.result == "draw" and g.round == 10


def test_taboo_views():
    g = Taboo(describer="pet", entry=ENTRY)
    g.clue("pet", "冬天大家围着吃")
    view = g.view()
    assert view["word"] is None and view["taboo"] is None and view["word_chars"] == 2
    assert "火锅" not in json.dumps(view, ensure_ascii=False)
    g.guess("player", "火锅")
    assert g.view()["word"] == "火锅"

    g = Taboo(describer="player", entry=ENTRY)
    g.clue("player", "冬天大家围着吃")
    assert g.view()["word"] == "火锅"
    seen = g.guess_situation()  # the guesser's prompt: clues only
    assert "火锅" not in seen and "毛肚" not in seen and "冬天大家围着吃" in seen
    assert "目标词：火锅" in g.describe_situation()


# --- hall -----------------------------------------------------------------------------------


def brain(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    return GameBrain(config_path=tmp_path / "backend.yaml", persona_path=tmp_path / "persona.yaml", keys=KeyStore(), provider_factory=Scripted)


def test_hall_records_persist_and_reach_the_prompt(tmp_path):
    hall = GameHall(brain(tmp_path), state_path=tmp_path / "games.json")
    assert hall.record_line() is None
    g = Rps()
    hall.start("rps", g)
    assert g.invited is False and g.end is None
    g.result = "pet"
    hall.record(g)
    hall.set_settings(voice=True, invite=None)
    again = GameHall(brain(tmp_path), state_path=tmp_path / "games.json")
    assert again.records["rps"] == {"player": 0, "pet": 1, "draw": 0} and again.settings == {"voice": True, "invite": True}
    line = again.record_line()
    assert "骗子猜拳 你赢 1 局、主人赢 0 局" in line and "最近一局是骗子猜拳，你赢了" in line


def test_hall_invitations(tmp_path):
    now = [1000.0]
    hall = GameHall(brain(tmp_path), clock=lambda: now[0])
    assert hall.can_invite(unprompted=True)
    hall.invite("dice", by_pet=True)
    assert not hall.can_invite(unprompted=True) and hall.can_invite()  # on its own: at most every 30 minutes
    other_game = Rps()
    hall.start("rps", other_game)
    assert other_game.invited is False and hall.pending_invite() is not None
    assert not hall.can_invite()  # a match is in progress
    g = LiarDice(rng=random.Random(1))
    hall.start("dice", g)
    assert g.invited is True and hall.pending_invite() is None
    hall.invite("dice", by_pet=False)  # the user asked for it: no bonus
    g2 = LiarDice(rng=random.Random(1))
    hall.start("dice", g2)
    assert g2.invited is False
    hall.invite("auction", by_pet=True)
    now[0] += 901
    assert hall.pending_invite() is None
    hall.set_settings(invite=False)
    assert not hall.can_invite()


# --- API ------------------------------------------------------------------------------------


def test_games_need_auth_and_start_empty(client):
    assert client.get("/api/games").status_code == 401
    body = client.get("/api/games", headers=auth()).json()
    assert body["llm_available"] is True and body["pet_name"] == "小白" and body["status_on"] is False
    assert body["settings"] == {"voice": False, "invite": True} and body["invited"] is None
    assert all(body[k] is None for k in ("roulette", "rps", "dice", "auction", "taboo"))
    assert post(client, "/roulette/move", {"action": "shoot", "target": "self"}).status_code == 409
    assert post(client, "/rps/pick", {"choice": "rock"}).status_code == 409
    assert post(client, "/dice/pet").status_code == 409
    body = client.put("/api/games/settings", headers=auth(), json={"voice": True}).json()
    assert body["settings"] == {"voice": True, "invite": True}


def test_roulette_api(client):
    r = post(client, "/roulette/start", {"mode": "custom", "bullets": 4, "player_hp": 2, "pet_hp": 3, "use_llm": False})
    assert r.status_code == 200
    view = r.json()["roulette"]
    assert view["left"] == 4 and view["max_hp"] == {"player": 2, "pet": 3} and len(view["items"]) == 3
    assert "chamber" not in view and view["end"] is None and view["invited"] is False
    assert post(client, "/roulette/start", {"mode": "custom", "bullets": 99}).status_code == 422
    g = hall_of(client).games["roulette"]
    g.chamber, g.turn = [0, 1, 1, 0], "player"
    view = post(client, "/roulette/move", {"action": "shoot", "target": "self"}).json()["roulette"]
    assert view["turn"] == "player" and view["left"] == 3
    assert post(client, "/roulette/move", {"action": "item", "item": "nope"}).status_code == 409
    view = post(client, "/roulette/move", {"action": "shoot", "target": "opponent"}).json()["roulette"]
    assert view["turn"] == "pet" and view["hp"]["pet"] == 2
    before = view["seq"]
    view = post(client, "/roulette/pet").json()["roulette"]
    assert view["seq"] > before and Scripted.calls == []  # built-in strategy: no LLM call
    assert post(client, "/roulette/quit").json()["roulette"] is None


def test_roulette_pet_move_from_the_model(client):
    post(client, "/roulette/start", {"use_llm": True})
    g = hall_of(client).games["roulette"]
    g.chamber, g.turn = [1, 0, 0], "pet"
    g.items = {"player": ["lens"], "pet": ["flash"]}
    g.known["player"] = 1
    Scripted.replies = ['{"action": "item", "item": "flash", "say": "接好了哦 💥"}', '{"action": "shoot", "target": "opponent", "say": "砰"}']
    view = post(client, "/roulette/pet").json()["roulette"]
    assert view["boost"] and view["turn"] == "pet" and view["log"][-1]["say"] == "接好了哦"  # no emoji
    view = post(client, "/roulette/pet").json()["roulette"]
    assert view["hp"]["player"] == 2 and view["turn"] == "player"
    # The pet's situation never held the player's items or what the player peeked at.
    sent = situations()
    assert "lens" not in sent and "放大镜看过" not in sent and "主人还有 1 个道具" in sent
    assert "不要用 emoji" in Scripted.calls[0][0]["content"]


def test_roulette_bad_model_move_falls_back_and_end_is_settled_once(client):
    post(client, "/roulette/start", {"mode": "custom", "bullets": 2, "player_hp": 1, "pet_hp": 1, "use_llm": True})
    g = hall_of(client).games["roulette"]
    g.chamber, g.turn, g.items["pet"] = [1, 1], "pet", []
    Scripted.replies = ['{"action": "item", "item": "juice", "say": "喝一口"}', '{"say": "赢啦"}']
    body = post(client, "/roulette/pet").json()
    view = body["roulette"]
    assert view["winner"] == "pet" and view["end"] == {"say": "赢啦", "reward": None}
    assert body["records"]["roulette"] == {"player": 0, "pet": 1, "draw": 0}
    calls = len(Scripted.calls)
    assert post(client, "/roulette/pet").json()["records"]["roulette"]["pet"] == 1 and len(Scripted.calls) == calls


def test_rps_api_keeps_the_players_choice_from_the_model(client):
    assert post(client, "/rps/start", {"use_llm": True}).json()["rps"]["step"] == 1
    assert post(client, "/rps/chat", {"text": "嗨"}).status_code == 409  # talking belongs to 局 3
    Scripted.replies = ['{"choice": "paper", "say": "我最后一定出布 ✋"}', '{"choice": "rock", "say": ""}']
    view = post(client, "/rps/pick", {"choice": "scissors"}).json()["rps"]
    assert view["picks"] == {"player": ["scissors"], "pet": ["paper"]} and view["chat"][-1]["text"] == "我最后一定出布"
    # Nothing had been shown when the pet chose, and the player's pending choice was nowhere in its prompt.
    assert "主人亮过的手势：还没有" in Scripted.calls[0][1]["content"]
    view = post(client, "/rps/pick", {"choice": "rock"}).json()["rps"]
    assert view["step"] == 3 and view["picks"]["pet"] == ["paper", "rock"] and view["options"] == ["scissors", "rock"]
    assert "主人亮过的手势：scissors（剪刀）。" in Scripted.calls[1][1]["content"]
    Scripted.replies = ['{"say": "信不信由你"}'] * TALK_ROUNDS
    for i in range(TALK_ROUNDS):
        view = post(client, "/rps/chat", {"text": f"第{i}句"}).json()["rps"]
        assert view["talk_left"] == TALK_ROUNDS - i - 1 and view["chat"][-1] == {"side": "pet", "text": "信不信由你"}
    assert post(client, "/rps/chat", {"text": "再来"}).status_code == 409  # five exchanges at most
    Scripted.replies = ['{"choice": "scissors"}', '{"say": "再来一把"}']  # not one of the pet's two: built-in choice
    body = post(client, "/rps/pick", {"choice": "rock"}).json()
    view = body["rps"]
    assert view["step"] == 4 and view["final"]["player"] == "rock" and view["final"]["pet"] in ("paper", "rock")
    assert sum(body["records"]["rps"].values()) == 1 and view["end"]["say"] == "再来一把"
    assert post(client, "/rps/quit").json()["rps"] is None


def test_dice_api(client):
    view = post(client, "/dice/start", {"use_llm": True}).json()["dice"]
    assert len(view["hand"]) == 5 and view["dice"] == {"player": 5, "pet": 5} and view["reveal"] is None
    g = hall_of(client).games["dice"]
    g.hands, g.turn = {"player": [1, 1, 1, 1, 1], "pet": [2, 3, 4, 5, 6]}, "player"
    assert post(client, "/dice/move", {"action": "bid", "count": 0, "face": 1}).status_code == 422
    assert post(client, "/dice/move", {"action": "call"}).status_code == 409
    view = post(client, "/dice/move", {"action": "bid", "count": 4, "face": 1}).json()["dice"]
    assert view["turn"] == "pet" and view["bids"][-1] == {"side": "player", "count": 4, "face": 1}
    Scripted.replies = ['{"action": "bid", "count": 4, "face": 1, "say": "跟"}']  # not higher: the built-in move plays
    view = post(client, "/dice/pet").json()["dice"]
    assert view["phase"] in ("bid", "reveal")
    sent = situations()
    assert "你杯子里的骰子：2、3、4、5、6" in sent and "1、1、1" not in sent
    if view["phase"] == "bid":
        view = post(client, "/dice/move", {"action": "call"}).json()["dice"]
    assert view["reveal"]["hands"]["pet"] == [2, 3, 4, 5, 6] and sum(view["dice"].values()) == 9
    view = post(client, "/dice/next").json()["dice"]
    assert view["round"] == 2 and view["reveal"] is None and view["bids"] == []
    assert post(client, "/dice/next").status_code == 409


def test_auction_api_keeps_the_players_card_from_the_model(client):
    view = post(client, "/auction/start", {"use_llm": True}).json()["auction"]
    prize = view["pot"][0]
    Scripted.replies = ['{"card": 2, "say": "随便出一张"}', '{"card": 99}']
    view = post(client, "/auction/play", {"card": 13}).json()["auction"]
    assert view["scores"] == {"player": prize, "pet": 0} and view["history"][0]["pet"] == 2
    assert view["history"][0]["say"] == "随便出一张" and 13 not in view["hand"] and 2 not in view["pet_hand"]
    # The prompt for round 1 listed the player's full hand: the card being played was not singled out.
    assert "主人手里还剩的牌（点数）：1、2、3、4、5、6、7、8、9、10、11、12、13。" in Scripted.calls[0][1]["content"]
    assert post(client, "/auction/play", {"card": 13}).status_code == 409
    view = post(client, "/auction/play", {"card": 1}).json()["auction"]  # the model's 99 is not a card: built-in bid
    assert len(view["history"]) == 2 and view["history"][1]["pet"] in range(1, 14)


def test_taboo_api(client):
    Scripted.replies = ['{"say": "冬天吃的，很辣"}', '{"say": "大家围着一口热气腾腾的东西吃"}', '{"say": "可以放很多菜进去煮"}']
    hall_of(client).rng = random.Random(5)
    body = post(client, "/taboo/start", {"describer": "pet"}).json()
    g = hall_of(client).games["taboo"]
    view = body["taboo"]
    assert view["word"] is None and view["describer"] == "pet"
    first = view["turns"][0]["text"]
    assert g.violation(first) is None  # a clue with a forbidden word never reaches the player
    view = post(client, "/taboo/say", {"text": "不知道"}).json()["taboo"]
    assert view["round"] == 2 and view["turns"][-1]["kind"] == "clue" and view["word"] is None
    body = post(client, "/taboo/say", {"text": g.word}).json()
    assert body["taboo"]["result"] == "player" and body["taboo"]["word"] == g.word and body["records"]["taboo"]["player"] == 1

    Scripted.calls, Scripted.replies = [], ['{"guess": "苹果", "say": "是苹果吗"}']
    post(client, "/taboo/start", {"describer": "player"})
    g = hall_of(client).games["taboo"]
    assert post(client, "/taboo/say", {"text": f"就是{g.word}啊"}).status_code == 409  # the player may not say it either
    view = post(client, "/taboo/say", {"text": "一种很常见的东西"}).json()["taboo"]
    assert view["turns"][-1] == {"side": "pet", "kind": "guess", "text": "苹果", "ok": g.correct("苹果")}
    assert g.word not in situations()  # the guessing model never saw the word


def test_taboo_needs_an_llm(tmp_path):
    (tmp_path / "backend.yaml").write_text("server:\n  port: 8765\n", encoding="utf-8")
    c = TestClient(create_app(config_path=tmp_path / "backend.yaml", token=TOKEN, provider_factory=Scripted))
    assert post(c, "/taboo/start", {}).status_code == 409


def rps_final(client, choice, *, use_llm=False, invite=False):
    """Plays the final throw of a game where the pet's best gesture is scissors and the player holds rock/paper."""
    if invite:
        hall_of(client).invite("rps", by_pet=True)
    post(client, "/rps/start", {"use_llm": use_llm})
    g = hall_of(client).games["rps"]
    g.picks, g.step = {"player": ["rock", "paper"], "pet": ["scissors", "rock"]}, 3
    return post(client, "/rps/pick", {"choice": choice}).json()


def affection(client) -> float:
    status = client.get("/api/status", headers=auth()).json()
    return next(s["value"] for s in status["stats"] if s["id"] == "affection")


def test_affection_rewards(client, tmp_path):
    body = rps_final(client, "rock")
    assert body["rps"]["result"] == "player" and body["rps"]["end"]["reward"] is None  # status bar off: nothing
    (tmp_path / "status.yaml").write_text("enabled: true\n", encoding="utf-8")
    body = rps_final(client, "rock")
    assert body["status_on"] and body["rps"]["end"]["reward"]["delta"] == 0.5 and affection(client) == 0.5
    body = rps_final(client, "rock", invite=True)  # the pet had proposed the game: +1
    assert body["rps"]["invited"] and body["rps"]["end"]["reward"]["delta"] == 1.0 and affection(client) == 1.5
    body = rps_final(client, "paper")  # lost, and the pet is not thinking: nothing
    assert body["rps"]["result"] == "pet" and body["rps"]["end"]["reward"] is None and affection(client) == 1.5
    Scripted.replies = ['{"choice": "scissors"}', '{"say": "玩得开心", "affection": true}']
    body = rps_final(client, "paper", use_llm=True)  # lost: the pet decides
    assert body["rps"]["end"] == {"say": "玩得开心", "reward": body["rps"]["end"]["reward"]} and affection(client) == 2.0
    assert '"affection": true 或 false' in Scripted.calls[-1][0]["content"]
    Scripted.replies = ['{"choice": "scissors"}', '{"say": "哼", "affection": false}']
    assert rps_final(client, "paper", use_llm=True)["rps"]["end"]["reward"] is None and affection(client) == 2.0


def test_game_talk_is_not_stored_and_the_tally_reaches_chat(client):
    rps_final(client, "rock")
    assert client.app.state.agent.last_prompt is None  # games never go through the agent
    events = client.get("/api/events", headers=auth()).json()
    assert events == [] or all(e.get("type") != "user.text" for e in (events if isinstance(events, list) else events.get("events", [])))
    preview = client.post("/api/prompt/preview", headers=auth(), json={"text": "你好"}).json()
    env = next(s["text"] for s in preview["sections"] if s["key"] == "environment")
    assert "小游戏战绩" in env and "骗子猜拳 你赢 0 局、主人赢 1 局" in env


def test_invite_tool_is_offered_only_when_asked(client):
    def skills_text(text):
        view = client.post("/api/prompt/preview", headers=auth(), json={"text": text}).json()
        return next((s["text"] for s in view["sections"] if s["key"] == "skills"), "")

    client.app.state.pet_hub.clients.add(object())  # the invitation is a dialog on the pet window: needs one
    assert "invite_game" not in skills_text("你好呀")
    assert "invite_game" in skills_text("好无聊，玩点什么")
    client.put("/api/games/settings", headers=auth(), json={"invite": False})
    assert "invite_game" not in skills_text("好无聊，玩点什么")
