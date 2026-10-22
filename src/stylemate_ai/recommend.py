"""AI 穿搭推薦：只從「提問者衣櫃裡真正存在的衣服」推薦，AI 回傳的每個 ID 都要驗證（防護 8）。

流程：解析請求（場合、日期、天氣、地點）→ 取得天氣（使用者說的優先）→ 程式先篩選衣服
→ 把這位使用者自己的衣服清單交給 claude -p（不給任何工具）→ 檢查回傳 → 組出回覆。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable

from . import claude_client, outfits, settings, stats, weather, wardrobe
from .config import Config
from .excel_store import WARDROBE, UserStore
from .schemas import AnalysisError, clean_text
from .weather import ManualWeather, Weather, WeatherError

MAX_CANDIDATES = 80
MAX_OUTFIT_ITEMS = 8
SEASON_BY_MONTH = {12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏", 9: "秋", 10: "秋", 11: "秋"}
SLOT_ORDER = ["洋裝", "上衣", "褲子", "裙子", "外套", "鞋子", "配件"]
SLOT_LABEL = {"洋裝": "洋裝", "上衣": "上衣", "褲子": "下著", "裙子": "下著", "外套": "外套", "鞋子": "鞋子", "配件": "配件"}

RECO_SCHEMA = {
    "type": "object",
    "properties": {
        "outfit": {
            "type": "object",
            "properties": {
                "item_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["item_ids", "reason"],
        },
        "missing": {
            "type": "array",
            "items": {"type": "object", "properties": {"type": {"type": "string"}, "why": {"type": "string"}},
                      "required": ["type", "why"]},
        },
        "note": {"type": "string"},
    },
    "required": ["outfit", "missing", "note"],
}

INSTRUCTION = (
    "你是穿搭助理。請只用 <衣櫃資料> 裡列出的衣服，替使用者配出「一套」完整造型。\n"
    "規則：\n"
    "1. item_ids 只能填 <衣櫃資料> 裡每行開頭的衣服 ID（原樣照抄），絕對不可以編造不在清單裡的 ID。\n"
    "2. 一套至少要有「洋裝」，或「上衣＋褲子（或裙子）」；需要時加外套、鞋子、配件；總共不超過 {max_items} 件。\n"
    "3. 考慮：場合的正式程度，以及使用者提到的天氣、氣溫、需求；若有 <偏好資料> 也一併參考（使用者寫的優先，習慣只是參考）。\n"
    "4. reason：用繁體中文說明為什麼這樣搭（120 字內），要提到場合與天氣，並用衣服名稱而不是 ID。\n"
    "5. missing：如果缺少某類衣服會讓這套更適合（例如下雨缺防水外套），列出缺的「類型」與原因（只說類型，例如「一件防水外套」，"
    "不要寫商品、品牌、網址或價格）；沒有就給空陣列。\n"
    "6. note：補充一句提醒（60 字內），沒有就給空字串。\n"
    "7. 如果真的湊不出完整一套，item_ids 給空陣列，並在 reason 說明原因、在 missing 列出缺什麼。\n"
    "{exclude}"
    "只用指定的 JSON 格式回答。"
)


class NeedWeather(Exception):
    """還不知道天氣，需要使用者補充；訊息可以直接給使用者看。"""


@dataclass
class RecoRequest:
    day: date
    occasion: str = ""
    manual: ManualWeather = field(default_factory=ManualWeather)
    place_text: str | None = None


@dataclass
class Recommendation:
    item_ids: list[str]
    reason: str
    missing: list[tuple[str, str]]
    note: str
    weather: Weather | None
    request: RecoRequest

    @property
    def complete(self) -> bool:
        return bool(self.item_ids)


# ---------------------------------------------------------------- 解析請求

_PLACE = re.compile(r"(?:地點|城市|位置)\s*[:：]\s*(\S+)")
_OCCASION = re.compile(r"(?:場合|情境)\s*[:：]\s*(\S+)")
_DAY_WORDS = ("今天", "今日", "明天", "明日", "後天", "后天")


def parse_request(text: str, today: date) -> RecoRequest:
    rest = text or ""
    place = None
    m = _PLACE.search(rest)
    if m:
        place = m.group(1)
        rest = rest[: m.start()] + " " + rest[m.end():]
    occasion = ""
    m = _OCCASION.search(rest)
    if m:
        occasion = m.group(1)
        rest = rest[: m.start()] + " " + rest[m.end():]
    day = today
    for word in _DAY_WORDS:
        if word in rest:
            day = outfits.parse_day_word(word, today) or today
            rest = rest.replace(word, " ", 1)
            break
    else:
        m = re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}", rest)
        if m and (parsed := outfits.parse_day_word(m.group(0), today)):
            day = parsed
            rest = rest[: m.start()] + " " + rest[m.end():]
    manual = weather.parse_manual(rest)
    if not occasion:
        occasion = re.sub(r"[，,。.、;；!！?？]+", " ", manual.leftover).strip()
    return RecoRequest(day=day, occasion=clean_text_safe(occasion, 40), manual=manual, place_text=place)


def clean_text_safe(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", text or "")).strip()[:limit]


# ---------------------------------------------------------------- 天氣

async def gather_weather(store: UserStore, request: RecoRequest, today: date, *,
                         get_json: weather.GetJson | None = None) -> Weather:
    """使用者說的優先；沒說的用預報補。拿不到氣溫就丟 NeedWeather。"""
    lat = lon = None
    place = ""
    if request.place_text:
        try:
            place, lat, lon = await weather.resolve_place(request.place_text, get_json=get_json)
        except WeatherError as exc:
            if request.manual.temp is None:
                raise NeedWeather(f"{exc}\n你也可以直接告訴我氣溫，例如 `!推薦 明天面試 18度 下雨`。") from None
    else:
        cfg = settings.get(store)
        if cfg["緯度"] is not None and cfg["經度"] is not None:
            place, lat, lon = cfg["城市"] or "", cfg["緯度"], cfg["經度"]

    predicted: Weather | None = None
    if request.manual.temp is None and lat is not None:
        try:
            predicted = await weather.forecast(lat, lon, request.day, today, place=place, get_json=get_json)
        except WeatherError as exc:
            raise NeedWeather(f"查不到天氣：{exc}\n請告訴我大概幾度、會不會下雨，例如 `!推薦 明天面試 18度 下雨`。") from None
    combined = weather.combine(request.manual, predicted)
    if combined is None:
        raise NeedWeather(
            "還不知道天氣：請先用 `!設定 城市 台北` 設定城市（我會自動查天氣），"
            "或直接告訴我，例如 `!推薦 明天面試 18度 下雨`、`!推薦 約會 地點:台中`。"
        )
    return combined


# ---------------------------------------------------------------- 篩選與提示詞

def target_warmth(temp: float) -> int:
    for threshold, level in ((30, 1), (26, 2), (21, 3), (16, 3), (11, 4)):
        if temp >= threshold:
            return level
    return 5


def prefilter(rows: list[dict], day: date, w: Weather) -> list[dict]:
    """程式先排除明顯不合適的衣服（季節差太多、保暖度差太多），降低額度與出錯機率。"""
    active = [r for r in rows if r["狀態"] == wardrobe.ACTIVE]
    season = SEASON_BY_MONTH[day.month]
    target = target_warmth(w.temp if w.temp is not None else 22)

    def fits(r: dict) -> bool:
        seasons = str(r.get("適合季節") or "")
        if seasons and season not in seasons and len(seasons.split("、")) < 4:
            return False
        try:
            return abs(int(r["保暖程度"]) - target) <= 2 or r["種類"] in ("鞋子", "配件")
        except (TypeError, ValueError):
            return True

    picked = [r for r in active if fits(r)]
    if not _can_make_outfit(picked):
        picked = active  # 篩得太乾淨就湊不出一套：改給 AI 全部（它看得到天氣）
    picked.sort(key=lambda r: abs((int(r["保暖程度"]) if str(r["保暖程度"]).isdigit() else target) - target))
    return picked[:MAX_CANDIDATES]


def _can_make_outfit(rows: list[dict]) -> bool:
    kinds = {r["種類"] for r in rows}
    return "洋裝" in kinds or ("上衣" in kinds and ("褲子" in kinds or "裙子" in kinds))


def _line(r: dict) -> str:
    note = clean_text_safe(str(r.get("備註") or ""), 40)
    return (f"{r['衣服ID']}｜{r['名稱']}｜{r['種類']}｜{r['顏色']}｜{r['材質']}｜{r['風格']}｜{r['圖案']}｜"
            f"季節:{r['適合季節']}｜保暖{r['保暖程度']}｜正式{r['正式程度']}" + (f"｜備註:{note}" if note else ""))


def _preference_text(store: UserStore, today: date) -> str:
    pref = settings.get(store)["偏好"]
    learned = stats.learned_preferences(store, today)
    lines = []
    if pref:
        lines.append(f"使用者寫的偏好（優先）：{pref}")
    if learned["styles"] or learned["colors"]:
        lines.append("從穿搭紀錄看出的習慣（只當參考）：常穿風格 " + "、".join(learned["styles"] or ["（不明）"]) +
                     "；常穿顏色 " + "、".join(learned["colors"] or ["（不明）"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- 驗證 AI 的回答

def _make_validator(allowed: list[dict], shown: list[frozenset]) -> Callable[[Any], dict]:
    by_id = {r["衣服ID"]: r for r in allowed}

    def validate(payload: Any) -> dict:
        if not isinstance(payload, dict) or not isinstance(payload.get("outfit"), dict):
            raise AnalysisError("回傳的格式不對")
        raw_ids = payload["outfit"].get("item_ids")
        if not isinstance(raw_ids, list) or not all(isinstance(i, str) for i in raw_ids):
            raise AnalysisError("item_ids 不是文字清單")
        ids = list(dict.fromkeys(i.strip().lstrip("#").upper() for i in raw_ids))
        if len(ids) > MAX_OUTFIT_ITEMS:
            raise AnalysisError("一套的件數太多")
        valid = wardrobe.validate_ids(ids, allowed)  # 防護 8：必須存在、屬於提問者、狀態正常
        if len(valid) != len(ids):
            raise AnalysisError("回傳了不存在或不屬於使用者的衣服 ID")
        if ids:
            kinds = {by_id[i]["種類"] for i in ids}
            if not ("洋裝" in kinds or ("上衣" in kinds and ("褲子" in kinds or "裙子" in kinds))):
                raise AnalysisError("這一套不完整（要有洋裝，或上衣加下著）")
            if frozenset(ids) in shown:
                raise AnalysisError("跟之前推薦過的重複")
        reason = clean_text(payload["outfit"].get("reason", ""), 160, "reason")
        if not reason:
            raise AnalysisError("沒有說明理由")
        missing = []
        for m in (payload.get("missing") or [])[:3]:
            if isinstance(m, dict):
                t = re.sub(r"https?://\S+", "", clean_text(m.get("type", ""), 20, "missing.type"))
                why = re.sub(r"https?://\S+", "", clean_text(m.get("why", ""), 60, "missing.why"))
                if t:
                    missing.append((t, why))
        note = re.sub(r"https?://\S+", "", clean_text(payload.get("note", ""), 80, "note")) if isinstance(payload.get("note", ""), str) else ""
        return {"ids": ids, "reason": reason, "missing": missing, "note": note}

    return validate


# ---------------------------------------------------------------- 主流程

def parse_core_request(text: str, today: date) -> RecoRequest:
    """核心版：整段話都當成「場合」交給 AI（使用者提到的天氣、溫度也在裡面），不另外查天氣。"""
    return RecoRequest(day=today, occasion=clean_text_safe(text, 100))


async def recommend(config: Config, store: UserStore, request: RecoRequest, today: date, *,
                    shown: list[frozenset] | None = None, use_weather: bool = False,
                    get_json: weather.GetJson | None = None,
                    ask: Callable[..., Awaitable[Any]] | None = None) -> Recommendation:
    """use_weather=False（核心版）：只依「場合」和衣櫃現有衣服推薦，不查天氣 API、不看穿搭紀錄與偏好設定。
    use_weather=True（進階，預設關閉）：查天氣、程式先篩選衣服、參考偏好與穿搭習慣。"""
    shown = shown or []
    rows = store.read(WARDROBE)
    w: Weather | None = None
    if use_weather:
        w = await gather_weather(store, request, today, get_json=get_json)
        candidates = prefilter(rows, request.day, w)
    else:
        candidates = [r for r in rows if r["狀態"] == wardrobe.ACTIVE][:MAX_CANDIDATES * 3]  # 衣櫃上限 200 件，全部交給 AI
    if not candidates:
        raise NeedWeather("你的衣櫃還是空的，先傳幾張衣服照片再來推薦吧！")

    exclude = ""
    if shown:
        exclude = "8. 這些組合已經推薦過，請給不同的搭配（衣服 ID 組合不可相同）：" + \
                  "；".join(",".join(sorted(s)) for s in shown) + "\n"
    instruction = INSTRUCTION.format(max_items=MAX_OUTFIT_ITEMS, exclude=exclude)
    situation = f"日期：{request.day.isoformat()}（{SEASON_BY_MONTH[request.day.month]}季）\n場合與需求：{request.occasion or '日常'}"
    if w is not None:
        situation += f"\n天氣：{w.summary()}" + (f"（{w.place}）" if w.place else "")
    blocks = [
        ("使用者文字", situation),
        ("衣櫃資料", "\n".join(_line(r) for r in candidates)),
    ]
    if use_weather:
        blocks.append(("偏好資料", _preference_text(store, today)))
    data = await (ask or claude_client.ask_json)(
        config, instruction=instruction, blocks=blocks, schema=RECO_SCHEMA,
        validate=_make_validator(candidates, shown),
    )
    return Recommendation(data["ids"], data["reason"], data["missing"], data["note"], w, request)


def describe_outfit(rows_by_id: dict[str, dict], ids: list[str]) -> list[str]:
    """依種類排序，每行「上衣：白色棉質襯衫（#K7M2）」。"""
    ordered = sorted(ids, key=lambda i: SLOT_ORDER.index(rows_by_id[i]["種類"]) if rows_by_id[i]["種類"] in SLOT_ORDER else 99)
    return [f"{SLOT_LABEL.get(rows_by_id[i]['種類'], rows_by_id[i]['種類'])}：{rows_by_id[i]['名稱']}（#{i}）" for i in ordered]
