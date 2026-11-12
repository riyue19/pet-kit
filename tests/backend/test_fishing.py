import asyncio
import dataclasses
import random
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.app import create_app
from core.agent import Labels
from core.games.fishing import FishConfig, FishFiles, FishingGame, Store, engine, parse_command, perform
from core.games.fishing.config import Lines, pick_group
from core.games.fishing.engine import CastResult, CastState
from core.llm_config import ConfigError

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
TOKEN = "test-token"
DAY = datetime(2026, 1, 1, 13, 0).timestamp()
NIGHT = datetime(2026, 1, 1, 23, 0).timestamp()
CFG = FishFiles(CONFIG_DIR).fish()


def state(**kw) -> CastState:
    base = dict(seq=1, location="pond")
    base.update(kw)
    return CastState(**base)


class FakeBrain:
    def __init__(self, reply=None, delay=0.0):
        self.reply, self.delay, self.calls = reply, delay, 0

    def available(self):
        return True

    async def ask(self, rules, situation):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


class HighRng(random.Random):
    def random(self):
        return 0.99


def make_game(*, seed=1, now=None, quiet=False, brain=None, labels=None, config_dir=CONFIG_DIR):
    clock = {"t": now or DAY}
    sent, said = [], []
    st = {"quiet": quiet}

    async def send(m):
        sent.append(m)

    async def say(text, expression, motion, voiced):
        said.append((text, expression, motion))

    async def is_quiet():
        return st["quiet"]

    game = FishingGame(
        files=FishFiles(config_dir), store=Store(None), brain=brain, rng=random.Random(seed),
        clock=lambda: clock["t"], send=send, say=say, quiet=is_quiet, labels=lambda: labels,
    )
    return game, clock, sent, said, st


async def drain(game):
    while game._tasks:
        await asyncio.gather(*list(game._tasks), return_exceptions=True)


def force_window(game, species="snakehead", size=50.0):
    def _resolve(cfg, now):
        seq = int(game.store.get("cast_seq", 0) or 0) + 1
        return CastResult(
            seq=seq, ts=now, outcome="fish", species_id=species, size_cm=size, needs_window=True,
            ctx={"location": "pond", "weather": "sunny", "time_slot": "night", "bait": "none", "rod_level": 1},
        )

    game._resolve = _resolve


def no_window(game):
    orig = game._resolve
    game._resolve = lambda cfg, now: dataclasses.replace(orig(cfg, now), needs_window=False)


def give(game, species, size, trophy=False):
    return game.store.add_fish(species, size, trophy, DAY, "pond")


def sizes(game):
    return sorted(f["size_cm"] for f in game.store.inventory())


# ---------------------------------------------------------------- 1-7 engine


def test_resolve_cast_reproducible():
    def run():
        rng, out = random.Random(42), []
        for i in range(200):
            out.append(engine.resolve_cast(state(seq=i, empty_streak=i % 5), CFG, rng, DAY).to_dict())
        return out

    assert run() == run()


@pytest.mark.parametrize("seed", range(8))
def test_pity_forces_fish(seed):
    rng = random.Random(seed)
    for _ in range(100):
        r = engine.resolve_cast(state(empty_streak=CFG.game.pity_empty_streak), CFG, rng, DAY)
        assert r.outcome == "fish" and r.reason == "pity"


def test_no_candidates_degrades_to_empty():
    cfg = CFG.model_copy(update={"species": [s for s in CFG.species if s.time == ["night"]]})
    rng = random.Random(1)
    for _ in range(50):
        r = engine.resolve_cast(state(empty_streak=cfg.game.pity_empty_streak), cfg, rng, DAY)
        assert r.outcome == "empty" and r.reason == "no_species"


def _species_seen(now, weather, n=4000, **kw):
    rng, seen = random.Random(3), set()
    for _ in range(n):
        r = engine.resolve_cast(state(empty_streak=4, weather=weather, **kw), CFG, rng, now)
        seen.add(r.species_id)
    return seen


def test_conditions_filter_species():
    day_sunny = _species_seen(DAY, "sunny", rod_level=3, bait="lure")
    assert "snakehead" not in day_sunny and "koi" not in day_sunny
    assert "carp" in day_sunny
    assert "snakehead" in _species_seen(NIGHT, "rain", rod_level=3, bait="lure")
    assert "koi" in _species_seen(DAY, "rain", rod_level=3, bait="lure")


def test_rarity_weights_only_present_and_modifiers():
    present = {"common", "rare", "legendary"}
    base = engine.rarity_weights(CFG, state(), present)
    assert set(base) == present
    assert set(engine.rarity_weights(CFG, state(), {"common"})) == {"common"}
    rod3 = engine.rarity_weights(CFG, state(rod_level=3), present)
    assert rod3["rare"] > base["rare"] and rod3["legendary"] > base["legendary"]
    lure = engine.rarity_weights(CFG, state(bait="lure"), present)
    assert lure["rare"] > base["rare"] and lure["legendary"] > base["legendary"]
    lucky = engine.rarity_weights(CFG, state(luck=1.1), present)
    assert lucky["rare"] > base["rare"] and lucky["legendary"] > base["legendary"]
    assert lucky["common"] == base["common"] and rod3["common"] == base["common"]


def test_sizes_and_trophy():
    rng = random.Random(7)
    by_rod = {1: [], 3: []}
    for rod in (1, 3):
        for _ in range(6000):
            r = engine.resolve_cast(state(empty_streak=4, rod_level=rod), CFG, rng, DAY)
            sp = CFG.species_by_id(r.species_id)
            assert sp.size_cm.min <= r.size_cm <= round(sp.size_cm.max * 1.1, 1) + 1e-9
            sz = sp.size_cm
            assert r.trophy == (r.size_cm >= sz.mean + CFG.economy.trophy_sd * sz.sd)
            assert r.trophy == engine.is_trophy(CFG, sp, r.size_cm)
            if sp.id == "carp":
                by_rod[rod].append(r.size_cm)
    assert len(by_rod[1]) > 200 and len(by_rod[3]) > 200
    assert sum(by_rod[3]) / len(by_rod[3]) > sum(by_rod[1]) / len(by_rod[1])


def test_simulate_statistics():
    res = engine.simulate(CFG, 100000, seed=1)
    assert res["casts"] == 100000
    total = sum(CFG.outcome_weights.values())
    for k, w in CFG.outcome_weights.items():
        tol = 0.03 if k in ("empty", "fish") else 0.015
        assert abs(res["outcomes"][k] / 100000 - w / total) < tol, k


# ---------------------------------------------------------------- 8 hook


def test_hook_clicked_always_lands():
    async def run():
        game, _, sent, said, _ = make_game(now=NIGHT)
        force_window(game)
        r = await game.tick(force=True)
        assert r["outcome"] == "pending"
        assert await game.tick(force=True) is None  # window open
        await game.hook(r["seq"] + 99, True)  # wrong seq: no effect
        assert game.store.inventory() == [] and game._pending is not None
        await game.hook(r["seq"], True)
        await drain(game)
        inv = game.store.inventory()
        assert len(inv) == 1 and inv[0]["species_id"] == "snakehead"
        assert game.store.codex_entry("snakehead")["count"] == 1
        assert game.store.recent_log(1)[0]["escaped"] == 0
        assert game._pending is None

    asyncio.run(run())


def test_hook_unclicked_fails_with_high_roll():
    async def run():
        game, *_ = make_game(now=NIGHT)
        game.rng = HighRng(1)
        force_window(game)
        r = await game.tick(force=True)
        await game.hook(r["seq"], False)
        await drain(game)
        assert game.store.inventory() == []
        assert game.store.recent_log(1)[0]["escaped"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("rod", [1, 2, 3])
def test_hook_chance_statistics(rod):
    p = engine.hook_chance(CFG, rod)
    rng = random.Random(11)
    rate = sum(rng.random() < p for _ in range(10000)) / 10000
    assert abs(rate - p) < 0.03
    assert 0 <= p <= 0.9


# ---------------------------------------------------------------- 9 commands


def test_sell_picks_smallest_or_largest():
    async def run():
        game, *_ = make_game()
        for s in (30.0, 20.0, 40.0):
            give(game, "carp", s)
        r = await game.command("sell", ["鲤鱼"])
        assert r.ok and sizes(game) == [30.0, 40.0]
        r = await game.command("sell", ["鲤鱼", "最大"])
        assert r.ok and sizes(game) == [30.0]

    asyncio.run(run())


def test_sell_all_common_keeps_rare_and_all_needs_confirm():
    async def run():
        game, *_ = make_game()
        give(game, "carp", 30.0)
        give(game, "crucian", 12.0)
        give(game, "snakehead", 50.0)
        give(game, "koi", 45.0)
        r = await game.command("sell", ["全部普通"])
        assert r.ok
        left = {f["species_id"] for f in game.store.inventory()}
        assert left == {"snakehead", "koi"}
        coins = game.store.get("coins")
        r = await game.command("sell", ["全部"])
        assert r.need_confirm and not r.ok
        assert len(game.store.inventory()) == 2 and game.store.get("coins") == coins
        r = await game.command("sell", ["全部"], True)
        assert r.ok and game.store.inventory() == [] and game.store.get("coins") > coins

    asyncio.run(run())


def test_place_locked_mentions_price():
    async def run():
        game, *_ = make_game()
        r = await game.command("place", ["河口"])
        assert not r.ok and "100" in r.text
        assert game.store.get("location") is None

    asyncio.run(run())


def test_not_enough_coins_changes_nothing():
    async def run():
        game, *_ = make_game()
        game.store.set("coins", 1)
        for cmd, args in (("buy", ["虫饵", "5"]), ("unlock", ["河口"]), ("rod", [])):
            r = await game.command(cmd, args, True)
            assert not r.ok, cmd
        assert game.store.get("coins") == 1
        assert game.store.bait_qty("worm") == 0
        assert game.store.get("rod_level", 1) == 1
        assert game.store.get("unlocked_locations", []) == []

    asyncio.run(run())


def test_spending_commands_with_enough_coins():
    async def run():
        game, *_ = make_game()
        game.store.set("coins", 1000)
        r = await game.command("rod", [])
        assert r.need_confirm and game.store.get("coins") == 1000
        r = await game.command("rod", [], True)
        assert r.ok and game.store.get("rod_level") == 2 and game.store.get("coins") == 850
        r = await game.command("buy", ["面包屑", "3"])
        assert r.ok and game.store.bait_qty("bread") == 3 and game.store.get("coins") == 844
        r = await game.command("unlock", ["河口"], True)
        assert r.ok and game.store.get("coins") == 744
        assert (await game.command("place", ["河口"])).ok

    asyncio.run(run())


def test_release_rare_needs_confirm():
    async def run():
        game, *_ = make_game()
        give(game, "snakehead", 50.0)
        r = await game.command("release", ["黑鱼"])
        assert r.need_confirm and len(game.store.inventory()) == 1
        r = await game.command("release", ["黑鱼"], True)
        assert r.ok and game.store.inventory() == []

    asyncio.run(run())


def test_interval_range():
    async def run():
        game, *_ = make_game()
        before = game.settings().wait_min_sec
        for bad in (["1", "60"], ["60", "10"], ["5", "9999"], ["abc", "60"], ["10"]):
            assert not (await game.command("interval", bad)).ok
        assert game.settings().wait_min_sec == before
        assert (await game.command("interval", ["10", "120"])).ok
        assert (game.settings().wait_min_sec, game.settings().wait_max_sec) == (600, 7200)
        assert not (await game.command("nonsense")).ok

    asyncio.run(run())


def test_parse_command():
    assert parse_command("/钓鱼 状态") == ("status", [], False)
    assert parse_command("／fish status") == ("status", [], False)
    assert parse_command("/钓鱼 卖 全部 确认") == ("sell", ["全部"], True)
    assert parse_command("/钓鱼") == ("status", [], False)
    assert parse_command("今天钓鱼吗") is None


# ---------------------------------------------------------------- 10 catch up


def _tmp_config(tmp_path, **repl):
    text = (CONFIG_DIR / "fish.yaml").read_text(encoding="utf-8")
    for a, b in repl.values():
        assert a in text
        text = text.replace(a, b)
    (tmp_path / "fish.yaml").write_text(text, encoding="utf-8")
    shutil.copy(CONFIG_DIR / "fishing_lines.yaml", tmp_path / "fishing_lines.yaml")
    return tmp_path


FIXED_WAIT = ("wait_max_sec: 10800", "wait_max_sec: 300")  # every cast waits exactly 5 minutes


def test_catch_up_cap(tmp_path):
    cfg_dir = _tmp_config(tmp_path, w=FIXED_WAIT)

    async def run():
        game, clock, sent, said, _ = make_game(config_dir=cfg_dir)
        await game.command("start")
        game.store.set("cast_due", clock["t"] - 100 * 3600)
        sent.clear()
        n = await game.catch_up()
        await drain(game)
        assert n == 24  # cap 24 < 8h/5min
        assert await game.catch_up() == 0
        assert not [m for m in sent if m.get("type") == "fishing.anim"]
        assert len(game.store.get("digest")) == n
        # The cap was reached: a fresh line goes out now.
        assert game.store.get("cast_ts") == clock["t"] and game.store.get("cast_due") == clock["t"] + 300

    asyncio.run(run())


def test_catch_up_max_hours(tmp_path):
    cfg_dir = _tmp_config(tmp_path, a=("offline_catchup_cap: 24", "offline_catchup_cap: 200"),
                          b=("offline_catchup_max_hours: 8", "offline_catchup_max_hours: 1"), w=FIXED_WAIT)

    async def run():
        game, clock, *_ = make_game(config_dir=cfg_dir)
        await game.command("start")
        game.store.set("cast_due", clock["t"] - 100 * 3600)
        n = await game.catch_up()
        await drain(game)
        assert n == 13  # the last hour, one every 5 minutes, both ends included
        assert len(game.store.get("digest")) == 13
        assert game.store.get("cast_due") == clock["t"] + 300

    asyncio.run(run())


# ---------------------------------------------------------------- real, random waits


def test_wait_is_random_and_never_short():
    rng = random.Random(3)
    waits = [engine.draw_wait(rng, 300, 10800) for _ in range(5000)]
    assert min(waits) >= 300 and max(waits) <= 10800
    assert max(waits) > 3600 and len({round(w) for w in waits}) > 1000
    assert abs(sum(waits) / len(waits) - engine.mean_wait(300, 10800)) < 150
    with pytest.raises(ValidationError):
        CFG.game.model_validate({**CFG.game.model_dump(), "wait_min_sec": 60})


def test_nothing_is_caught_before_the_wait_is_over():
    async def run():
        game, clock, sent, _, _ = make_game()
        no_window(game)
        await game.command("start")
        t0 = clock["t"]
        due = game.store.get("cast_due")
        assert due - t0 >= game.settings().wait_min_sec and game.store.get("cast_ts") == t0
        # Starting only puts the line out: nothing is settled, and the state does not tell when the bite comes.
        assert [m["phase"] for m in sent if m.get("type") == "fishing.anim"] == ["cast"]
        assert "cast_due" not in game.state_message() and "next_tick_ts" not in game.state_message()
        for t in (t0 + 1, t0 + 170, due - 1):
            clock["t"] = t
            assert await game.tick() is None
        assert game.store.get("cast_seq", 0) == 0 and game.store.recent_log(5) == []
        clock["t"] = due
        r = await game.tick()
        await drain(game)
        assert r is not None and game.store.get("cast_seq") == 1
        # The line goes straight back out with a new wait.
        assert game.store.get("cast_ts") == due and game.store.get("cast_due") - due >= game.settings().wait_min_sec
        assert await game.tick() is None

    asyncio.run(run())


def test_stop_reels_in_empty_and_start_waits_again():
    async def run():
        game, clock, *_ = make_game()
        await game.command("start")
        clock["t"] += 100
        await game.command("stop")
        assert game.store.get("cast_due") is None and game.cast_ts() is None
        await game.command("start")
        assert game.store.get("cast_ts") == clock["t"]
        assert game.store.get("cast_due") - clock["t"] >= game.settings().wait_min_sec
        assert game.store.get("cast_seq", 0) == 0

    asyncio.run(run())


def test_hud_feed_and_settings():
    async def run():
        game, *_ = make_game()
        assert game.feed(None) == ([], 0)
        await game.command("start")
        items, last = game.feed(0)
        assert last == 1 and items[0]["msg"]["phase"] == "cast" and game.feed(last)[0] == []
        game.set_settings({"hud_placed": False, "hud_scale": 1.8})
        msg = game.state_message()
        assert msg["placed"] is False and msg["scale"] == 1.8
        with pytest.raises(ValidationError):
            game.set_settings({"hud_scale": 9})
        # A leftover key of an older version does not break saving.
        game.store.set("settings", {"tick_interval_sec": 300})
        assert game.set_settings({"hud_placed": True}).hud_placed is True

    asyncio.run(run())


# ---------------------------------------------------------------- 11 LLM validation


GOOD = {"text": "钓到了，12.5厘米。", "expression": "happy", "motion": None, "tool_calls": []}


def _vl(move):
    return perform.validate_line(move, allowed_expressions=["happy"], allowed_motions=["wave"], numbers={12.5})


def test_validate_line():
    assert _vl(GOOD)["expression"] == "happy"
    assert "12.5" in _vl(GOOD)["text"]
    assert _vl({**GOOD, "motion": "wave"})["motion"] == "wave"
    bad = [
        "text", None, [GOOD],
        {**GOOD, "tool_calls": [{"name": "open_app", "args": {}}]},
        {**GOOD, "expression": "angry"},
        {**GOOD, "motion": "dance"},
        {**GOOD, "text": "钓到了，13厘米。"},
        {**GOOD, "text": "啊" * 61},
        {**GOOD, "text": ""},
        {**GOOD, "text": "   "},
    ]
    for b in bad:
        assert _vl(b) is None, b


def _rare_catch(game):
    async def go():
        force_window(game)
        r = await game.tick(force=True)
        await game.hook(r["seq"], True)
        await drain(game)

    return go


class _Safe(dict):
    def __missing__(self, key):
        return ""


def _pool_lines(game):
    lines = pick_group(game.files.lines(), "catch", "rare", game.files.fish().persona.mood)
    return {line.format_map(_Safe(fish="黑鱼", size="50")) for line in lines}


def test_invalid_llm_line_falls_back_to_pool():
    illegal = {"text": "这条99厘米的怪鱼！", "expression": None, "motion": None, "tool_calls": []}

    async def run():
        brain = FakeBrain(illegal)
        game, _, _, said, _ = make_game(now=NIGHT, brain=brain, labels=Labels(expressions=["happy"], motions=["wave"]))
        await _rare_catch(game)()
        assert brain.calls >= 1
        # said[0] is the bite-window line; the last one is the catch line
        assert len(said) == 2
        assert all(s[0] != illegal["text"] and "99" not in s[0] for s in said)
        assert said[-1][0] in _pool_lines(game)

    asyncio.run(run())


def test_llm_timeout_falls_back_to_pool(monkeypatch):
    monkeypatch.setattr(perform, "LLM_TIMEOUT_S", 0.05)

    async def run():
        brain = FakeBrain({"text": "好", "tool_calls": []}, delay=10)
        game, _, _, said, _ = make_game(now=NIGHT, brain=brain)
        await _rare_catch(game)()
        assert len(said) == 2 and said[-1][0] in _pool_lines(game)

    asyncio.run(run())


# ---------------------------------------------------------------- 12 budget


def test_budget_direct_and_rollover():
    game, clock, *_ = make_game(brain=FakeBrain())
    cfg = game.files.fish()
    game.set_settings({"llm_budget_per_day": 2})
    assert [game._spend(cfg) for _ in range(5)] == [True, True, False, False, False]
    assert game.llm_used_today() == 2
    clock["t"] += 86400
    assert game.llm_used_today() == 0
    assert game._spend(cfg) is True


def test_budget_limits_brain_calls():
    async def run():
        brain = FakeBrain({"text": "x", "tool_calls": []})
        game, *_ = make_game(now=NIGHT, brain=brain)
        game.set_settings({"llm_budget_per_day": 2})
        for _ in range(4):
            await _rare_catch(game)()
        assert brain.calls <= 2
        assert game.llm_used_today() <= 2

    asyncio.run(run())


def test_no_brain_means_no_spend():
    game, *_ = make_game(brain=None)
    assert game._spend(game.files.fish()) is False


# ---------------------------------------------------------------- 13 letters


BODY = "今天海边的风很温柔，我把这封信放进瓶子里，希望你也有好心情。"


def test_valid_letter():
    assert perform.valid_letter("海边人", BODY) == ("海边人", BODY)
    bad_bodies = [
        BODY + " https://example.com", BODY + " www.example.com", BODY + " me@x", BODY + " 13812345678",
        "太短了", "字" * 121,
    ]
    for b in bad_bodies:
        assert perform.valid_letter("海边人", b) is None, b
    assert perform.valid_letter("", BODY) is None
    assert perform.valid_letter(None, BODY) is None
    assert perform.valid_letter("海边人", 5) is None


def test_validate_letters_drops_bad_one_by_one():
    batch = {"letters": [
        {"sender": "甲", "body": BODY},
        {"sender": "乙", "body": BODY + " http://a.b"},
        {"sender": "丙", "body": "短"},
        {"sender": "丁", "body": BODY + "再见。"},
        "junk",
    ]}
    out = perform.validate_letters(batch)
    assert [s for s, _ in out] == ["甲", "丁"]
    assert perform.validate_letters(None) == [] and perform.validate_letters({"letters": 3}) == []


def test_builtin_letters_seeded():
    game, *_ = make_game()
    assert game.store.bottle_counts()[0] >= 20


# ---------------------------------------------------------------- 14 config validation


def _dump():
    return FishFiles(CONFIG_DIR).fish().model_dump()


def _edit(species_id, **changes):
    d = _dump()
    sp = next(s for s in d["species"] if s["id"] == species_id)
    sp.update(changes)
    return d


@pytest.mark.parametrize("changes", [
    {"template": "bogus"}, {"patterns": ["bogus"]}, {"locations": ["nowhere"]}, {"bait_bonus": {"nobait": 2.0}},
])
def test_config_rejects_bad_reference(changes):
    with pytest.raises(ValidationError) as e:
        FishConfig.model_validate(_edit("carp", **changes))
    assert "carp" in str(e.value)


def test_config_eel_scales_warning():
    assert CFG.warnings() == []
    cfg = FishConfig.model_validate(_edit("loach", patterns=["scales"]))
    assert cfg.warnings() and "loach" in cfg.warnings()[0]


def test_missing_fish_yaml_raises(tmp_path):
    with pytest.raises(ConfigError):
        FishFiles(tmp_path).fish()


def test_lines_fallback_builtin():
    assert pick_group(Lines(), "catch", "rare", "neutral")
    assert pick_group(Lines(), "streak", None, "neutral")


# ---------------------------------------------------------------- 15 quiet


def test_quiet_digest_then_flush():
    async def run():
        game, _, sent, said, st = make_game(quiet=True)
        no_window(game)
        await game.tick(force=True)
        await drain(game)
        assert not [m for m in sent if m.get("type") == "fishing.anim"]
        assert said == []
        assert len(game.store.get("digest")) == 1
        assert await game.flush_digest() is False  # still quiet
        st["quiet"] = False
        assert await game.flush_digest() is True
        await drain(game)
        anims = [m for m in sent if m.get("type") == "fishing.anim"]
        assert len(anims) == 1 and anims[0]["phase"] == "digest"
        assert len(said) == 1
        assert game.store.get("digest") == []

    asyncio.run(run())


# ---------------------------------------------------------------- 16 transactions


def test_tick_bookkeeping():
    async def run():
        game, clock, *_ = make_game()
        no_window(game)
        await game.tick(force=True)
        await drain(game)
        assert game.store.get("cast_seq") == 1
        assert len(game.store.recent_log(10)) == 1
        assert game.store.get("last_tick_ts") == clock["t"]
        clock["t"] += 300
        await game.tick(force=True)
        await drain(game)
        assert game.store.get("cast_seq") == 2 and len(game.store.recent_log(10)) == 2
        assert game.store.get("last_tick_ts") == clock["t"]

    asyncio.run(run())


def test_bait_consumed_and_reset():
    async def run():
        game, *_ = make_game()
        no_window(game)
        game.store.add_bait("bread", 2)
        game.store.set("bait", "bread")
        await game.tick(force=True)
        assert game.store.bait_qty("bread") == 1 and game.store.get("bait") == "bread"
        await game.tick(force=True)
        await drain(game)
        assert game.store.bait_qty("bread") == 0 and game.store.get("bait") == "none"

    asyncio.run(run())


# ---------------------------------------------------------------- 17-18 routes


class CountingFactory:
    calls = 0

    def __init__(self, *a, **kw):
        CountingFactory.calls += 1


@pytest.fixture
def client(tmp_path):
    (tmp_path / "backend.yaml").write_text(
        "server:\n  port: 8765\nllm:\n  profiles:\n    - name: main\n      base_url: https://api.example.com/v1\n"
        "      model: m\n  roles:\n    chat: main\n",
        encoding="utf-8",
    )
    (tmp_path / "persona.yaml").write_text("name: 小白\n", encoding="utf-8")
    shutil.copy(CONFIG_DIR / "fish.yaml", tmp_path / "fish.yaml")
    shutil.copy(CONFIG_DIR / "fishing_lines.yaml", tmp_path / "fishing_lines.yaml")
    CountingFactory.calls = 0
    return TestClient(create_app(config_path=tmp_path / "backend.yaml", token=TOKEN, provider_factory=CountingFactory))


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_routes_auth_and_snapshot(client):
    assert client.get("/api/fishing").status_code == 401
    r = client.get("/api/fishing", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and len(body["species"]) == len(CFG.species) == 26
    assert all("name" not in s for s in body["species"] if not s["found"])


def test_routes_command_settings_simulate(client):
    r = client.post("/api/fishing/command", headers=auth(), json={"cmd": "start"})
    assert r.status_code == 200 and r.json()["state"]["enabled"] is True
    assert client.get("/api/fishing", headers=auth()).json()["enabled"] is True
    assert client.put("/api/fishing/settings", headers=auth(), json={"wait_min_sec": 10}).status_code == 422
    ok = client.put("/api/fishing/settings", headers=auth(), json={"wait_min_sec": 600})
    assert ok.status_code == 200 and ok.json()["settings"]["wait_min_sec"] == 600
    hud = client.get("/api/fishing/hud", headers=auth()).json()
    assert hud["state"]["enabled"] is True and hud["pending_seq"] is None and hud["feed"] == [] and hud["last"] >= 1
    assert client.get("/api/fishing/hud?after=0", headers=auth()).json()["feed"][0]["msg"]["phase"] == "cast"
    assert client.post("/api/fishing/hook", headers=auth(), json={"seq": 99}).status_code == 200
    assert client.post("/api/fishing/cast", headers=auth()).status_code in (404, 405)
    r = client.post("/api/fishing/simulate", headers=auth())
    assert r.status_code == 200 and r.json()["casts"] == 10000


def test_slash_command_over_ws_skips_llm(client):
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {}})
        ws.receive_json(), ws.receive_json()
        ws.send_json({"type": "event", "event": {"type": "user.text", "payload": {"text": "/钓鱼 状态"}}})
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "output":
                break
        else:
            pytest.fail("no output message")
        assert "金币" in msg["output"]["text"]
    assert CountingFactory.calls == 0


def test_lines_follow_proactive_switch():
    """主动对话关着：钓鱼不自己开口，也不为台词调用 LLM；命令的回复照常。"""

    async def run():
        brain = FakeBrain({"text": "钓到了", "expression": None, "motion": None})
        game, _, _, said, _ = make_game(brain=brain)
        game._may_speak = lambda: False
        await game._say_line(CFG, "有鱼咬钩了", None, None)
        assert said == []
        assert await game._llm_line(CFG, {"outcome": "escape"}, set()) is None
        assert brain.calls == 0
        assert await game.handle_text("/钓鱼") is True
        assert len(said) == 1

        game._may_speak = lambda: True
        await game._say_line(CFG, "有鱼咬钩了", None, None)
        assert len(said) == 2

    asyncio.run(run())
