"""天氣：用 Open-Meteo（免費、不用金鑰）查預報。只會送出城市名稱或座標，不送任何個人資料或衣服資料。

- 台灣的縣市用內建座標表（地名查詢服務對中文城市不可靠：例如「台北」查不到、「高雄」會對到中國的地方）
- 其他地點才用 Open-Meteo 的地名查詢
- 使用者自己說的天氣（幾度、下不下雨、很冷）永遠優先於查到的預報
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = aiohttp.ClientTimeout(total=10)
MAX_FORECAST_DAYS = 16


class WeatherError(Exception):
    """查不到天氣（網路、地名或日期有問題）；訊息可以直接給使用者看。"""


# 縣市（以縣市政府所在地為準）：名稱 → (緯度, 經度)
TAIWAN_PLACES: dict[str, tuple[float, float]] = {
    "台北市": (25.0330, 121.5654), "新北市": (25.0120, 121.4650), "基隆市": (25.1276, 121.7392),
    "桃園市": (24.9936, 121.3010), "新竹市": (24.8138, 120.9675), "新竹縣": (24.8387, 121.0177),
    "苗栗縣": (24.5602, 120.8214), "台中市": (24.1477, 120.6736), "彰化縣": (24.0518, 120.5161),
    "南投縣": (23.9609, 120.9719), "雲林縣": (23.7092, 120.4313), "嘉義市": (23.4801, 120.4491),
    "嘉義縣": (23.4518, 120.2555), "台南市": (22.9997, 120.2270), "高雄市": (22.6273, 120.3014),
    "屏東縣": (22.5519, 120.5487), "宜蘭縣": (24.7021, 121.7378), "花蓮縣": (23.9871, 121.6015),
    "台東縣": (22.7583, 121.1444), "澎湖縣": (23.5711, 119.5793), "金門縣": (24.4493, 118.3767),
    "連江縣": (26.1608, 119.9517),
}
_ENGLISH = {
    "taipei": "台北市", "new taipei": "新北市", "keelung": "基隆市", "taoyuan": "桃園市", "hsinchu": "新竹市",
    "miaoli": "苗栗縣", "taichung": "台中市", "changhua": "彰化縣", "nantou": "南投縣", "yunlin": "雲林縣",
    "chiayi": "嘉義市", "tainan": "台南市", "kaohsiung": "高雄市", "pingtung": "屏東縣", "yilan": "宜蘭縣",
    "hualien": "花蓮縣", "taitung": "台東縣", "penghu": "澎湖縣", "kinmen": "金門縣", "matsu": "連江縣",
}
# 同名的縣和市：只說「新竹」「嘉義」時，預設指市
_PREFERRED = {"新竹": "新竹市", "嘉義": "嘉義市"}


def match_taiwan_place(text: str) -> str | None:
    """「台北」「臺北市」「Taipei」→「台北市」；不是台灣縣市就回 None。"""
    t = re.sub(r"\s+", " ", text.strip().replace("臺", "台")).lower()
    if t in _ENGLISH:
        return _ENGLISH[t]
    base = re.sub(r"[市縣]$", "", t)
    if base in _PREFERRED and not t.endswith(("縣", "市")):
        return _PREFERRED[base]
    if t in TAIWAN_PLACES:  # 使用者連「市／縣」都寫了：只認完全一樣的（避免「新竹縣」對到「新竹市」）
        return t
    if t.endswith(("市", "縣")):
        return None
    for name in TAIWAN_PLACES:
        if base == name[:-1]:
            return name
    return None


# ---------------------------------------------------------------- 網路

async def _get_json(url: str, params: dict[str, Any]) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise WeatherError(f"天氣服務回應異常（{resp.status}）")
                return await resp.json()
    except WeatherError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        log.warning("天氣服務連線失敗：%s", exc)
        raise WeatherError("天氣服務暫時連不上") from None


GetJson = Callable[[str, dict], Awaitable[dict]]


async def resolve_place(text: str, *, get_json: GetJson | None = None) -> tuple[str, float, float]:
    """把使用者說的地點變成（顯示名稱, 緯度, 經度）。"""
    name = match_taiwan_place(text)
    if name:
        lat, lon = TAIWAN_PLACES[name]
        return name, lat, lon
    query = re.sub(r"\s+", " ", text.strip())[:60]
    if not query:
        raise WeatherError("請告訴我地點，例如：台北")
    data = await (get_json or _get_json)(GEOCODE_URL, {"name": query, "count": 1, "language": "zh", "format": "json"})
    results = data.get("results") or []
    if not results:
        raise WeatherError(f"找不到「{query}」這個地點，換個說法試試（例如加上「市」，或用英文）。")
    r = results[0]
    label = " ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
    try:
        return label[:60], float(r["latitude"]), float(r["longitude"])
    except (KeyError, TypeError, ValueError):
        raise WeatherError("天氣服務回傳的地點資料不完整") from None


_WMO = [
    ((0,), "晴"), ((1, 2), "晴時多雲"), ((3,), "陰"), ((45, 48), "有霧"),
    ((51, 53, 55, 56, 57), "毛毛雨"), ((61, 63, 65, 66, 67), "下雨"), ((71, 73, 75, 77, 85, 86), "下雪"),
    ((80, 81, 82), "陣雨"), ((95, 96, 99), "雷雨"),
]


def describe_code(code: Any) -> str:
    for codes, text in _WMO:
        if code in codes:
            return text
    return ""


@dataclass(frozen=True)
class Weather:
    tmin: float | None
    tmax: float | None
    rain_prob: int | None   # 降雨機率 0–100；None＝不知道
    description: str
    source: str             # 「預報」或「使用者」
    place: str = ""

    @property
    def temp(self) -> float | None:
        if self.tmin is None or self.tmax is None:
            return self.tmax if self.tmax is not None else self.tmin
        return (self.tmin + self.tmax) / 2

    @property
    def rainy(self) -> bool:
        return (self.rain_prob or 0) >= 50

    def summary(self) -> str:
        parts = []
        if self.tmin is not None and self.tmax is not None and round(self.tmin) != round(self.tmax):
            parts.append(f"{self.tmin:.0f}–{self.tmax:.0f}°C")
        elif self.temp is not None:
            parts.append(f"約 {self.temp:.0f}°C")
        if self.rain_prob is not None:
            parts.append(f"降雨機率 {self.rain_prob}%")
        if self.description:
            parts.append(self.description)
        text = "・".join(parts) or "天氣不明"
        return text + ("（你說的）" if self.source == "使用者" else "")


async def forecast(lat: float, lon: float, day: date, today: date, *, place: str = "",
                   get_json: GetJson | None = None) -> Weather:
    if day < today:
        raise WeatherError("推薦穿搭只能從今天起算，過去的日子請直接告訴我當時的天氣。")
    if (day - today).days >= MAX_FORECAST_DAYS:
        raise WeatherError(f"天氣預報只能查今天起 {MAX_FORECAST_DAYS} 天內。")
    data = await (get_json or _get_json)(FORECAST_URL, {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "Asia/Taipei",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
        "forecast_days": MAX_FORECAST_DAYS,
    })
    try:
        daily = data["daily"]
        index = daily["time"].index(day.isoformat())
        tmax, tmin = daily["temperature_2m_max"][index], daily["temperature_2m_min"][index]
        rain = daily["precipitation_probability_max"][index]
        code = daily["weathercode"][index]
    except (KeyError, ValueError, IndexError, TypeError):
        raise WeatherError("天氣服務回傳的資料我看不懂，或查不到那一天。") from None
    if tmax is None or tmin is None:
        raise WeatherError("查不到那一天的氣溫。")
    return Weather(float(tmin), float(tmax), None if rain is None else int(rain), describe_code(code), "預報", place)


# ---------------------------------------------------------------- 使用者自己說的天氣

_TEMP_RANGE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:-|~|～|到|至)\s*(\d{1,2}(?:\.\d)?)\s*(?:度|°C|°|℃|c\b)", re.IGNORECASE)
_TEMP_ONE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:度|°C|°|℃|c\b)", re.IGNORECASE)
_NO_RAIN = re.compile(r"不會下雨|不下雨|沒有雨|沒下雨|不用帶傘|晴天|大太陽|沒雨")
_RAIN = re.compile(r"下大雨|會下雨|有下雨|下雨|雨天|陣雨|雷雨|大雨|有雨|下毛毛雨|濕濕")
_FEEL = [
    (re.compile(r"嚴寒|寒流|凍|超冷|很冷|好冷|冷颼颼"), 10.0), (re.compile(r"炎熱|超熱|很熱|好熱|悶熱|大熱天"), 33.0),
    (re.compile(r"微涼|有點涼|涼爽|很涼"), 20.0), (re.compile(r"溫暖|舒適|剛好"), 25.0),
    (re.compile(r"偏冷|有點冷|冷"), 14.0), (re.compile(r"偏熱|有點熱|熱"), 30.0), (re.compile(r"涼"), 20.0),
]


@dataclass
class ManualWeather:
    temp: float | None = None
    rain: bool | None = None
    leftover: str = ""

    @property
    def any(self) -> bool:
        return self.temp is not None or self.rain is not None


def parse_manual(text: str) -> ManualWeather:
    """從一段話裡抓出使用者說的氣溫與下雨，並回傳「拿掉天氣字眼後剩下的話」。"""
    rest = text
    m = _TEMP_RANGE.search(rest)
    temp: float | None = None
    if m:
        temp = (float(m.group(1)) + float(m.group(2))) / 2
        rest = rest[: m.start()] + " " + rest[m.end():]
    else:
        m = _TEMP_ONE.search(rest)
        if m:
            temp = float(m.group(1))
            rest = rest[: m.start()] + " " + rest[m.end():]
    rain: bool | None = None
    for pattern, value in ((_NO_RAIN, False), (_RAIN, True)):
        if pattern.search(rest):
            rain = value
            rest = pattern.sub(" ", rest)
            break
    if temp is None:
        for pattern, value in _FEEL:
            if pattern.search(rest):
                temp = value
                rest = pattern.sub(" ", rest)
                break
    return ManualWeather(temp, rain, re.sub(r"\s+", " ", rest).strip())


def combine(manual: ManualWeather, predicted: Weather | None) -> Weather | None:
    """使用者說的優先；沒說的部分用預報補。兩邊都沒有氣溫就回 None。"""
    if manual.temp is None and predicted is None:
        return None
    temp = manual.temp
    if temp is not None:
        tmin = tmax = temp
        desc = ""
        place = predicted.place if predicted else ""
        rain = predicted.rain_prob if predicted else None
        source = "使用者"
    else:
        tmin, tmax, desc, place = predicted.tmin, predicted.tmax, predicted.description, predicted.place
        rain, source = predicted.rain_prob, "預報"
    if manual.rain is True:
        rain, desc, source = 100, "下雨", "使用者"
    elif manual.rain is False:
        rain, source = 0, "使用者"
    return Weather(tmin, tmax, rain, desc, source, place)
