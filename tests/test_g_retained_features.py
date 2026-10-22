"""保留但預設關閉的進階程式（天氣、設定、含天氣的推薦）：確認它們沒有壞掉。這些功能目前不對使用者開放。"""
from datetime import date, datetime

import pytest

from stylemate_ai import recommend, settings, weather
from stylemate_ai.config import Config
from stylemate_ai.excel_store import UserStore
from stylemate_ai.schemas import ClothingItem
from stylemate_ai import wardrobe
from stylemate_ai.weather import ManualWeather, Weather, WeatherError

NOW = datetime(2026, 10, 23, 14, 5, 33)
TODAY = NOW.date()


@pytest.mark.parametrize("text,expected", [
    ("台北", "台北市"), ("臺北市", "台北市"), ("Taipei", "台北市"), ("高雄", "高雄市"), ("新北", "新北市"),
    ("新竹", "新竹市"), ("新竹縣", "新竹縣"), ("嘉義", "嘉義市"), ("花蓮", "花蓮縣"), ("台中", "台中市"),
])
def test_taiwan_places_matched_offline(text, expected):
    assert weather.match_taiwan_place(text) == expected


def test_non_taiwan_place_is_not_matched():
    assert weather.match_taiwan_place("東京") is None
    assert len(weather.TAIWAN_PLACES) == 22


async def test_resolve_place_uses_table_then_api():
    async def api(url, params):
        assert "geocoding" in url and params["name"] == "東京"
        return {"results": [{"name": "東京", "admin1": "東京都", "country": "日本", "latitude": 35.68, "longitude": 139.69}]}

    assert (await weather.resolve_place("台北", get_json=api))[0] == "台北市"     # 台灣縣市不呼叫網路
    label, lat, lon = await weather.resolve_place("東京", get_json=api)
    assert "日本" in label and (lat, lon) == (35.68, 139.69)

    async def none(url, params):
        return {"generationtime_ms": 1}

    with pytest.raises(WeatherError, match="找不到"):
        await weather.resolve_place("不存在的地方", get_json=none)


async def test_forecast_parsing_and_errors():
    async def api(url, params):
        return {"daily": {"time": ["2026-10-23", "2026-10-24"], "temperature_2m_max": [25.9, 27.1],
                          "temperature_2m_min": [22.7, 23.2], "precipitation_probability_max": [100, 20],
                          "weathercode": [96, 1]}}

    w = await weather.forecast(25.0, 121.5, date(2026, 10, 24), TODAY, place="台北市", get_json=api)
    assert (w.tmin, w.tmax, w.rain_prob, w.description, w.source) == (23.2, 27.1, 20, "晴時多雲", "預報")
    assert "23–27°C" in w.summary() and not w.rainy
    rainy = await weather.forecast(25.0, 121.5, TODAY, TODAY, get_json=api)
    assert rainy.rainy and rainy.description == "雷雨"
    with pytest.raises(WeatherError):
        await weather.forecast(25.0, 121.5, date(2026, 10, 22), TODAY, get_json=api)        # 過去
    with pytest.raises(WeatherError):
        await weather.forecast(25.0, 121.5, date(2026, 12, 30), TODAY, get_json=api)         # 太遠
    with pytest.raises(WeatherError):
        await weather.forecast(25.0, 121.5, date(2026, 10, 25), TODAY, get_json=api)         # 不在回傳資料裡

    async def broken(url, params):
        return {"oops": 1}

    with pytest.raises(WeatherError):
        await weather.forecast(25.0, 121.5, TODAY, TODAY, get_json=broken)


@pytest.mark.parametrize("text,temp,rain", [
    ("明天面試 18度 下雨", 18.0, True), ("18-24度", 21.0, None), ("23°C 不會下雨", 23.0, False),
    ("今天很冷", 10.0, None), ("超熱 還下大雨", 33.0, True), ("約會", None, None),
])
def test_manual_weather_parsing(text, temp, rain):
    m = weather.parse_manual(text)
    assert m.temp == temp and m.rain == rain


def test_manual_weather_wins_over_forecast():
    predicted = Weather(20.0, 26.0, 10, "晴", "預報", "台北市")
    assert weather.combine(ManualWeather(), predicted).source == "預報"
    mixed = weather.combine(ManualWeather(temp=15.0, rain=True), predicted)
    assert mixed.temp == 15.0 and mixed.rainy and mixed.source == "使用者"
    assert weather.combine(ManualWeather(rain=False), predicted).tmax == 26.0
    assert weather.combine(ManualWeather(), None) is None


def test_settings_roundtrip_and_history(tmp_path):
    s = UserStore(tmp_path, 1001, retries=2, delay=0)
    assert settings.get(s) == {"城市": None, "緯度": None, "經度": None, "偏好": ""}
    settings.set_city(s, 1001, "台北市", 25.033, 121.5654, NOW)
    settings.set_preferences(s, 1001, "喜歡深色\n不穿裙子", NOW)
    got = settings.get(s)
    assert got["城市"] == "台北市" and got["緯度"] == 25.033 and got["偏好"] == "喜歡深色 不穿裙子"
    settings.set_city(s, 1001, "高雄市", 22.6, 120.3, NOW)
    settings.set_preferences(s, 1001, "", NOW)
    assert settings.get(s)["城市"] == "高雄市" and settings.get(s)["偏好"] == ""
    actions = [(h["對象ID"], h["動作"]) for h in s.read("修改歷史") if h["對象"] == "設定"]
    assert actions == [("城市", "新增"), ("偏好", "新增"), ("城市", "修改"), ("偏好", "刪除")]


async def test_recommend_with_weather_mode_uses_city_forecast(tmp_path):
    cfg = Config("t", 1, "haiku", (tmp_path / "data").resolve())
    store = UserStore(cfg.data_dir, 1001, retries=2, delay=0)
    items = [ClothingItem("白襯衫", "top", ["白"], "棉", "簡約", "素面", ["春", "夏", "秋", "冬"], 2, 3, "high"),
             ClothingItem("牛仔褲", "bottom", ["藍"], "丹寧", "休閒", "素面", ["春", "夏", "秋", "冬"], 3, 1, "high")]
    ids = [c["衣服ID"] for c in wardrobe.add_items(store, 1001, items, None, NOW)]
    settings.set_city(store, 1001, "台北市", 25.0, 121.5, NOW)
    settings.set_preferences(store, 1001, "喜歡簡約", NOW)
    seen = {}

    async def api(url, params):
        return {"daily": {"time": [TODAY.isoformat()], "temperature_2m_max": [24.0], "temperature_2m_min": [20.0],
                          "precipitation_probability_max": [80], "weathercode": [61]}}

    async def ask(config, *, instruction, blocks, schema, validate):
        seen.update(dict(blocks))
        return validate({"outfit": {"item_ids": ids, "reason": "舒適"}, "missing": [], "note": ""})

    req = recommend.parse_request("面試 地點:台北", TODAY)
    reco = await recommend.recommend(cfg, store, req, TODAY, use_weather=True, get_json=api, ask=ask)
    assert reco.weather.rainy and reco.weather.source == "預報" and "降雨機率 80%" in seen["使用者文字"]
    assert "喜歡簡約" in seen["偏好資料"]
    with pytest.raises(recommend.NeedWeather):                      # 沒設定城市、也沒說天氣
        other = UserStore(cfg.data_dir, 2002, retries=2, delay=0)
        wardrobe.add_items(other, 2002, items, None, NOW)
        await recommend.recommend(cfg, other, recommend.parse_request("面試", TODAY), TODAY, use_weather=True, ask=ask)
