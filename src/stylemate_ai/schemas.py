"""衣服照片分析的 JSON 格式與驗證，以及使用者修改欄位時的檢查。

claude -p 回來的任何東西都不可信：這裡逐欄位檢查，任何不對勁都丟 AnalysisError，
呼叫端接住後回覆「分析失敗」，不會讓程式當掉。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

CATEGORY_LABELS = {
    "top": "上衣", "bottom": "褲子", "skirt": "裙子", "outerwear": "外套",
    "dress": "洋裝", "shoes": "鞋子", "accessory": "配件",
}
CATEGORY_BY_LABEL = {label: key for key, label in CATEGORY_LABELS.items()}
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
USER_SPECIFIED = "使用者指定"
SEASONS = ("春", "夏", "秋", "冬")
MAX_ITEMS_PER_PHOTO = 6

LIMITS = {"name": 40, "material": 20, "style": 10, "pattern": 10, "color": 10, "tip": 60,
          "note": 100, "brand": 30, "what": 40}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_clothing": {"type": "boolean"},
        "identifiable": {"type": "boolean"},
        "what_it_is": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORY_LABELS)},
                    "colors": {"type": "array", "items": {"type": "string"}},
                    "material": {"type": "string"},
                    "style": {"type": "string"},
                    "pattern": {"type": "string"},
                    "seasons": {"type": "array", "items": {"type": "string", "enum": list(SEASONS)}},
                    "warmth": {"type": "integer", "minimum": 1, "maximum": 5},
                    "formality": {"type": "integer", "minimum": 1, "maximum": 5},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE_LABELS)},
                },
                "required": ["name", "category", "colors", "material", "style", "pattern",
                             "seasons", "warmth", "formality", "confidence"],
            },
        },
        "general_tip": {"type": "string"},
    },
    "required": ["is_clothing", "identifiable", "what_it_is", "items", "general_tip"],
}

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class AnalysisError(Exception):
    """分析失敗（claude 出錯、逾時、或回傳的內容通不過檢查）。"""


class EditError(ValueError):
    """使用者要修改的欄位或值不合法。"""


@dataclass(frozen=True)
class ClothingItem:
    name: str
    category: str  # 英文代碼（top、bottom…）
    colors: list[str]
    material: str
    style: str
    pattern: str
    seasons: list[str]
    warmth: int
    formality: int
    confidence: str  # high / medium / low

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]

    @property
    def confidence_label(self) -> str:
        return CONFIDENCE_LABELS[self.confidence]


@dataclass(frozen=True)
class AnalysisResult:
    is_clothing: bool
    identifiable: bool
    what_it_is: str
    items: list[ClothingItem] = field(default_factory=list)
    general_tip: str = ""


# ---------------------------------------------------------------- 欄位檢查

def clean_text(value: object, limit: int, field_name: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise AnalysisError(f"{field_name} 不是文字")
    text = _CONTROL_RE.sub(" ", value)
    text = re.sub(r"\s+", " ", text).strip()[:limit]
    if required and not text:
        raise AnalysisError(f"{field_name} 是空的")
    return text


def _int_1_5(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise AnalysisError(f"{field_name} 不是 1–5 的整數")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise AnalysisError(f"{field_name} 不是 1–5 的整數") from None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or float(value) != int(value):
        raise AnalysisError(f"{field_name} 不是 1–5 的整數")
    number = int(value)
    if not 1 <= number <= 5:
        raise AnalysisError(f"{field_name} 超出 1–5")
    return number


def _string_list(value: object, field_name: str, limit: int, max_len: int) -> list[str]:
    if isinstance(value, str):
        value = [p for p in re.split(r"[、,，;；/\s]+", value) if p]
    if not isinstance(value, list):
        raise AnalysisError(f"{field_name} 不是清單")
    out = []
    for item in value[:max_len]:
        text = clean_text(item, limit, field_name)
        if text and text not in out:
            out.append(text)
    return out


def _validate_item(payload: object) -> ClothingItem:
    if not isinstance(payload, dict):
        raise AnalysisError("items 裡有不是物件的東西")
    for key in ("name", "category", "colors", "material", "style", "pattern",
                "seasons", "warmth", "formality", "confidence"):
        if key not in payload:
            raise AnalysisError(f"衣服缺少欄位 {key}")
    category = payload["category"]
    if category not in CATEGORY_LABELS:
        raise AnalysisError(f"category 不在允許清單：{category!r}")
    confidence = payload["confidence"]
    if confidence not in CONFIDENCE_LABELS:
        raise AnalysisError(f"confidence 不在允許清單：{confidence!r}")
    colors = _string_list(payload["colors"], "colors", LIMITS["color"], 3)
    if not colors:
        raise AnalysisError("colors 是空的")
    seasons = _string_list(payload["seasons"], "seasons", 2, 4)
    seasons = [s for s in seasons if s in SEASONS] or list(SEASONS)
    return ClothingItem(
        name=clean_text(payload["name"], LIMITS["name"], "name", required=True),
        category=category,
        colors=colors,
        material=clean_text(payload["material"], LIMITS["material"], "material") or "不明",
        style=clean_text(payload["style"], LIMITS["style"], "style") or "不明",
        pattern=clean_text(payload["pattern"], LIMITS["pattern"], "pattern") or "素面",
        seasons=seasons,
        warmth=_int_1_5(payload["warmth"], "warmth"),
        formality=_int_1_5(payload["formality"], "formality"),
        confidence=confidence,
    )


def validate_result(payload: object) -> AnalysisResult:
    if not isinstance(payload, dict):
        raise AnalysisError("回傳的不是 JSON 物件")
    for key in ("is_clothing", "identifiable", "items"):
        if key not in payload:
            raise AnalysisError(f"缺少欄位 {key}")
    is_clothing, identifiable = payload["is_clothing"], payload["identifiable"]
    if not isinstance(is_clothing, bool) or not isinstance(identifiable, bool):
        raise AnalysisError("is_clothing／identifiable 不是 true/false")
    what = clean_text(payload.get("what_it_is", ""), LIMITS["what"], "what_it_is")
    tip = clean_text(payload.get("general_tip", ""), LIMITS["tip"], "general_tip")
    raw_items = payload["items"]
    if not isinstance(raw_items, list):
        raise AnalysisError("items 不是清單")

    if not (is_clothing and identifiable):
        return AnalysisResult(is_clothing, identifiable, what, [], "")  # 不是衣服或看不清楚：不收任何東西

    if not raw_items:
        raise AnalysisError("說是衣服，卻沒有列出任何一件")
    items = [_validate_item(i) for i in raw_items[:MAX_ITEMS_PER_PHOTO]]
    return AnalysisResult(True, True, what, items, tip)


def strip_fences(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def parse_claude_output(stdout: str) -> AnalysisResult:
    """解析 `claude -p --output-format json` 的輸出。"""
    try:
        envelope = json.loads(stdout)
    except ValueError:
        raise AnalysisError("claude 的輸出不是 JSON") from None

    payload = envelope
    if isinstance(envelope, dict) and envelope.get("type") == "result":
        if envelope.get("is_error"):
            raise AnalysisError(f"claude 回報錯誤：{str(envelope.get('result'))[:200]}")
        payload = envelope.get("structured_output")
        if payload is None:
            payload = envelope.get("result")

    if isinstance(payload, str):
        try:
            payload = json.loads(strip_fences(payload))
        except ValueError:
            raise AnalysisError("claude 的回答不是合法 JSON") from None

    return validate_result(payload)


# ---------------------------------------------------------------- 使用者修改欄位

FIELD_ALIASES = {
    "名稱": "名稱", "種類": "種類", "類別": "種類", "顏色": "顏色", "材質": "材質", "風格": "風格",
    "圖案": "圖案", "季節": "適合季節", "適合季節": "適合季節", "保暖": "保暖程度", "保暖程度": "保暖程度",
    "正式": "正式程度", "正式程度": "正式程度", "備註": "備註", "品牌": "品牌",
}
AI_JUDGED_FIELDS = {"名稱", "種類", "顏色", "材質", "風格", "圖案", "適合季節", "保暖程度", "正式程度"}


def _edit_text(text: str, limit: int, label: str, *, required: bool = False) -> str:
    cleaned = re.sub(r"\s+", " ", _CONTROL_RE.sub(" ", text)).strip()[:limit]
    if required and not cleaned:
        raise EditError(f"{label}不能是空的")
    return cleaned


def normalize_edit(field_name: str, text: str) -> tuple[str, object]:
    """把使用者打的「欄位=值」轉成（試算表欄位名, 可寫入的值）；不合法就丟 EditError。"""
    column = FIELD_ALIASES.get(field_name.strip())
    if column is None:
        raise EditError(f"不能修改「{field_name}」。可以改的有：名稱、種類、顏色、材質、風格、圖案、季節、保暖、正式、備註、品牌")
    text = text.strip()
    if column == "名稱":
        return column, _edit_text(text, LIMITS["name"], "名稱", required=True)
    if column == "種類":
        label = CATEGORY_LABELS.get(text.lower()) or (text if text in CATEGORY_BY_LABEL else None)
        if label is None:
            raise EditError("種類只能是：" + "、".join(CATEGORY_BY_LABEL))
        return column, label
    if column == "顏色":
        colors = [c for c in re.split(r"[、,，/\s]+", text) if c]
        colors = [_edit_text(c, LIMITS["color"], "顏色") for c in colors[:3]]
        if not colors:
            raise EditError("顏色不能是空的")
        return column, "、".join(colors)
    if column == "適合季節":
        if text in ("四季", "全年", "全部"):
            return column, "、".join(SEASONS)
        chosen = [s for s in SEASONS if s in text]
        if not chosen:
            raise EditError("季節請用 春、夏、秋、冬 其中幾個（例如：春夏，或 四季）")
        return column, "、".join(chosen)
    if column in ("保暖程度", "正式程度"):
        try:
            return column, _int_1_5(text, column)
        except AnalysisError:
            raise EditError(f"{column}請填 1 到 5 的整數") from None
    limits = {"材質": "material", "風格": "style", "圖案": "pattern", "備註": "note", "品牌": "brand"}
    return column, _edit_text(text, LIMITS[limits[column]], column)
