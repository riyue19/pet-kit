"""Background facts for every prompt: local time and (optionally) the weather of the configured city.

Weather comes from Open-Meteo (free, no key). The city is whatever the user typed in the console; no IP
geolocation. Results are cached, so a reply never waits on the network unless the cache is cold.
"""

import asyncio
from collections.abc import Callable
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from core import net
from core.screen_config import EnvironmentConfig

log = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TTL = 30 * 60
FAILURE_RETRY = 5 * 60
WEEKDAYS = "一二三四五六日"

# WMO weather interpretation codes (Open-Meteo `weather_code`).
WMO = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "较强阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


def now_line(now: datetime | None = None) -> str:
    d = now or datetime.now()
    return f"现在是 {d:%Y-%m-%d %H:%M}，星期{WEEKDAYS[d.weekday()]}。"


@dataclass
class Place:
    name: str  # display, e.g. 杭州（浙江）
    latitude: float
    longitude: float


def describe(place: Place, data: dict) -> str:
    cur, daily = data.get("current") or {}, data.get("daily") or {}

    def day(i: int, label: str) -> str | None:
        try:
            code = daily["weather_code"][i]
            lo, hi = daily["temperature_2m_min"][i], daily["temperature_2m_max"][i]
            rain = (daily.get("precipitation_probability_max") or [None] * 3)[i]
        except (KeyError, IndexError, TypeError):
            return None
        if lo is None or hi is None:  # Open-Meteo returns null for missing values
            return None
        s = f"{label}{WMO.get(code, '未知')}，{lo:.0f}~{hi:.0f}°C"
        return s + (f"，降水概率 {rain}%" if rain is not None else "")

    parts = []
    if cur.get("temperature_2m") is not None:
        s = f"现在{WMO.get(cur.get('weather_code'), '')} {cur['temperature_2m']:.0f}°C"
        if cur.get("apparent_temperature") is not None:
            s += f"（体感 {cur['apparent_temperature']:.0f}°C）"
        parts.append(s)
    parts += [p for p in (day(0, "今天"), day(1, "明天")) if p]
    return f"{place.name}天气：" + "；".join(parts) + "。" if parts else ""


class Environment:
    def __init__(self, config, *, client: httpx.AsyncClient | None = None, clock=time.monotonic) -> None:
        self._config = config  # () -> EnvironmentConfig
        self._client = client
        self._clock = clock
        self._place: tuple[str, Place | None] | None = None  # (city query, result)
        self._weather: tuple[str, float, str] | None = None  # (city, fetched_at, text)
        self._failed_at = float("-inf")
        self._lock = asyncio.Lock()
        # Callables returning one more prompt line (or None), e.g. the camera status; set by the app wiring.
        self.extra_lines: list[Callable[[], str | None]] = []

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = net.make_client(timeout=httpx.Timeout(8, connect=5))
        return self._client

    async def _geocode(self, city: str) -> Place | None:
        if self._place and self._place[0] == city:
            return self._place[1]
        # "北京 朝阳" -> search the last word, prefer results whose region mentions the first.
        words = city.replace("，", " ").replace(",", " ").split()
        if not words:
            self._place = (city, None)
            return None
        async def search(name: str) -> list[dict]:
            r = await self._http().get(GEOCODE_URL, params={"name": name, "count": 10, "language": "zh", "format": "json"})
            r.raise_for_status()
            return r.json().get("results") or []

        results = await search(words[-1])
        if len(words) > 1:
            hint = words[0]
            region = lambda x: f"{x.get('admin1', '')}{x.get('admin2', '')}{x.get('country', '')}"  # noqa: E731
            matching = [x for x in results if hint in region(x)]
            # The district isn't known under that region (e.g. 北京 朝阳): fall back to the region itself.
            results = matching or await search(hint) or results
        place = None
        if results:
            x = results[0]
            region = x.get("admin1") or x.get("country") or ""
            name = x.get("name", city) + (f"（{region}）" if region and region != x.get("name") else "")
            # External text goes into the system prompt: keep it short and on one line.
            name = " ".join(str(name).split())[:30]
            place = Place(name=name, latitude=x["latitude"], longitude=x["longitude"])
        self._place = (city, place)
        return place

    async def weather(self, *, force: bool = False) -> str:
        """One-line weather summary for the configured city, or "" (no city / disabled / unavailable)."""
        cfg: EnvironmentConfig = self._config()
        if not cfg.weather or not cfg.city:
            return ""
        now = self._clock()
        if not force and self._weather and self._weather[0] == cfg.city and now - self._weather[1] < WEATHER_TTL:
            return self._weather[2]
        if not force and now - self._failed_at < FAILURE_RETRY:
            return self._weather[2] if self._weather and self._weather[0] == cfg.city else ""
        async with self._lock:
            # Another caller may have refreshed it while we waited for the lock.
            if not force and self._weather and self._weather[0] == cfg.city and self._clock() - self._weather[1] < WEATHER_TTL:
                return self._weather[2]
            try:
                place = await self._geocode(cfg.city)
                if place is None:
                    log.warning("找不到城市「%s」，天气不可用", cfg.city)
                    self._failed_at = now
                    return ""
                r = await self._http().get(
                    FORECAST_URL,
                    params={
                        "latitude": place.latitude,
                        "longitude": place.longitude,
                        "current": "temperature_2m,apparent_temperature,weather_code",
                        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                        "timezone": "auto",
                        "forecast_days": 2,
                    },
                )
                r.raise_for_status()
                text = describe(place, r.json())
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as e:
                log.warning("获取天气失败：%s", type(e).__name__)
                self._failed_at = now
                return self._weather[2] if self._weather and self._weather[0] == cfg.city else ""
            self._weather = (cfg.city, now, text)
            log.info("天气已更新：%s", text)
            return text

    async def context_block(self) -> str:
        """Prompt lines with the time and, if available, the weather."""
        lines = [now_line()]
        w = await self.weather()
        if w:
            lines.append(w)
        for extra in self.extra_lines:
            try:
                line = extra()
            except Exception:  # noqa: BLE001 - a broken status line must not break the reply
                log.exception("附加环境信息出错")
                continue
            if line:
                lines.append(line)
        return "\n".join(lines)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
