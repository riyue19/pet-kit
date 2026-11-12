"""The fishing rules as pure functions: what one cast brings up, whether a bite is landed, what a fish sells for.

Nothing here touches the database, the clock or the network; the random source is passed in, so a fixed seed
replays the same casts. The result of a cast is decided here in one go; animation and talk only play it back.
"""

import math
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from core.games.fishing.config import RARITIES, FishConfig, Species

Outcome = Literal["empty", "fish", "junk", "bottle"]


@dataclass
class CastState:
    seq: int
    location: str
    bait: str = "none"
    rod_level: int = 1
    empty_streak: int = 0
    weather: str = "sunny"
    luck: float = 1.0


@dataclass
class CastResult:
    seq: int
    ts: float
    outcome: Outcome
    species_id: str | None = None
    junk_id: str | None = None
    size_cm: float | None = None
    trophy: bool = False
    needs_window: bool = False
    # location / weather / time_slot / bait / rod_level
    ctx: dict[str, Any] = field(default_factory=dict)
    # "pity" | "no_species" | None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def time_slot(now: float) -> str:
    hour = datetime.fromtimestamp(now).hour
    if 5 <= hour < 8:
        return "dawn"
    if 8 <= hour < 18:
        return "day"
    if 18 <= hour < 21:
        return "dusk"
    return "night"


def weighted(rng: random.Random, weights: dict[Any, float]) -> Any:
    keys = [k for k, w in weights.items() if w > 0]
    return rng.choices(keys, [weights[k] for k in keys])[0]


def draw_weather(cfg: FishConfig, rng: random.Random) -> str:
    return weighted(rng, dict(cfg.weather_weights))


def luck_for(cfg: FishConfig, affinity_level: int) -> float:
    eco = cfg.economy
    return 1 + min(max(affinity_level, 0) * eco.luck_per_affinity_level, eco.luck_cap)


def candidates(cfg: FishConfig, location: str, slot: str, weather: str) -> list[Species]:
    return [s for s in cfg.species if s.at(location, slot, weather)]


def rarity_weights(cfg: FishConfig, state: CastState, present: set[str]) -> dict[str, float]:
    """Weight of each rarity that has a candidate, with rod / bait / luck applied."""
    rod = cfg.rods.get(state.rod_level) or cfg.rods[1]
    bait = cfg.baits.get(state.bait) or cfg.baits["none"]
    out = {}
    for r in RARITIES:
        if r not in present or r not in cfg.rarity:
            continue
        w = cfg.rarity[r].base_weight * rod.rarity_mult.get(r, 1) * bait.rarity_mult.get(r, 1)
        out[r] = w * (state.luck if r in ("rare", "legendary") else 1)
    return out


def resolve_cast(state: CastState, cfg: FishConfig, rng: random.Random, now: float) -> CastResult:
    bait_id = state.bait if state.bait in cfg.baits else "none"
    bait = cfg.baits[bait_id]
    rod = cfg.rods.get(state.rod_level) or cfg.rods[1]
    slot = time_slot(now)
    ctx = {
        "location": state.location,
        "weather": state.weather,
        "time_slot": slot,
        "bait": bait_id,
        "rod_level": state.rod_level,
    }
    result = CastResult(seq=state.seq, ts=now, outcome="empty", ctx=ctx)

    if state.empty_streak >= cfg.game.pity_empty_streak:
        outcome, result.reason = "fish", "pity"
    else:
        weights = dict(cfg.outcome_weights)
        weights["fish"] = weights.get("fish", 0) * bait.fish_weight_mult
        if not cfg.junk:
            weights.pop("junk", None)
        outcome = weighted(rng, weights)

    if outcome == "empty":
        return result
    if outcome == "junk":
        result.outcome, result.junk_id = "junk", rng.choice(cfg.junk).id
        return result
    if outcome == "bottle":
        result.outcome = "bottle"
        return result

    pool = candidates(cfg, state.location, slot, state.weather)
    by_rarity = rarity_weights(cfg, state, {s.rarity for s in pool})
    if not pool or not by_rarity:
        result.reason = "no_species"
        return result
    rarity = weighted(rng, by_rarity)
    same = [s for s in pool if s.rarity == rarity]
    species: Species = rng.choices(same, [s.weight * s.bait_bonus.get(bait_id, 1) for s in same])[0]
    size = species.size_cm
    cm = rng.gauss(size.mean * rod.size_mult, size.sd)
    cm = round(min(max(cm, size.min), size.max * 1.1), 1)
    result.outcome, result.species_id, result.size_cm = "fish", species.id, cm
    result.trophy = cm >= size.mean + cfg.economy.trophy_sd * size.sd
    result.needs_window = cfg.rarity[rarity].bite_window or result.trophy
    return result


def draw_wait(rng: random.Random, lo: float, hi: float) -> float:
    """Seconds a cast waits for its bite: between lo and hi, short waits more common than long ones
    (log-uniform), so most casts take a good while and some take hours."""
    lo, hi = sorted((max(1.0, float(lo)), max(1.0, float(hi))))
    if hi - lo < 1e-9:
        return lo
    return min(hi, max(lo, math.exp(rng.uniform(math.log(lo), math.log(hi)))))


def mean_wait(lo: float, hi: float) -> float:
    """Average of draw_wait."""
    lo, hi = sorted((max(1.0, float(lo)), max(1.0, float(hi))))
    return lo if hi - lo < 1e-9 else (hi - lo) / math.log(hi / lo)


def hook_chance(cfg: FishConfig, rod_level: int) -> float:
    """Chance that the pet lands a bite on its own (the player's click always lands it)."""
    return min(max(0.30 + 0.45 * cfg.persona.patience + 0.05 * rod_level, 0.0), 0.90)


def is_trophy(cfg: FishConfig, species: Species, size_cm: float) -> bool:
    return size_cm >= species.size_cm.mean + cfg.economy.trophy_sd * species.size_cm.sd


def price(cfg: FishConfig, species: Species, size_cm: float, trophy: bool) -> int:
    lo, hi = cfg.economy.price_size_clamp
    mult = min(max(size_cm / species.size_cm.mean, lo), hi)
    rarity = cfg.rarity.get(species.rarity)
    value = species.base_price * mult * (rarity.price_mult if rarity else 1)
    return max(1, round(value * (cfg.economy.trophy_price_mult if trophy else 1)))


def simulate(
    cfg: FishConfig,
    n: int,
    *,
    seed: int = 1,
    location: str | None = None,
    bait: str = "none",
    rod_level: int = 1,
    start: float = 1767225600.0,
) -> dict[str, Any]:
    """Runs `n` casts over simulated time (so day/night and the weather go round) and tallies them.
    Bites that need the window are landed at the pet's own chance. For tuning fish.yaml."""
    rng = random.Random(seed)
    location = location or cfg.start_location()
    # One cast per average wait.
    interval = mean_wait(cfg.game.wait_min_sec, cfg.game.wait_max_sec)
    outcomes = {k: 0 for k in ("empty", "fish", "junk", "bottle")}
    rarities = {r: 0 for r in RARITIES}
    species: dict[str, int] = {}
    escaped = coins = 0
    streak, weather, weather_ts = 0, draw_weather(cfg, rng), start
    p = hook_chance(cfg, rod_level)
    for i in range(n):
        now = start + i * interval
        if now - weather_ts >= cfg.game.weather_change_hours * 3600:
            weather, weather_ts = draw_weather(cfg, rng), now
        r = resolve_cast(CastState(i + 1, location, bait, rod_level, streak, weather), cfg, rng, now)
        outcomes[r.outcome] += 1
        streak = streak + 1 if r.outcome == "empty" else 0
        if r.outcome != "fish":
            continue
        sp = cfg.species_by_id(r.species_id)
        assert sp is not None and r.size_cm is not None
        if r.needs_window and rng.random() >= p:
            escaped += 1
            continue
        rarities[sp.rarity] += 1
        species[sp.name] = species.get(sp.name, 0) + 1
        coins += price(cfg, sp, r.size_cm, r.trophy)
    days = n * interval / 86400
    legendary = rarities["legendary"]
    return {
        "casts": n,
        "days": round(days, 1),
        "location": location,
        "bait": bait,
        "rod_level": rod_level,
        "outcomes": outcomes,
        "rarities": rarities,
        "species": dict(sorted(species.items(), key=lambda kv: -kv[1])),
        "escaped": escaped,
        "coins_per_day": round(coins / days, 1) if days else 0,
        "days_per_legendary": round(days / legendary, 1) if legendary else None,
    }
