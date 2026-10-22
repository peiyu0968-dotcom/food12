"""衣櫃統計：全部由程式算，不靠 AI。

- 衣櫃使用率 = 最近 N 天內「至少穿過一次」的衣服件數 ÷ 衣櫃裡狀態正常的總件數（N 預設 30）
- 最常穿：最近 N 天內使用次數最多的前 5 件
- 很久沒穿：超過 60 天沒穿的衣服，取最久的 5 件（入庫後從未穿過的另外標示）
- 常穿風格、常穿顏色：依「穿搭紀錄」的次數加權，不是依衣櫃裡有幾件
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .excel_store import OUTFITS, WARDROBE, UserStore
from .wardrobe import ACTIVE

IDLE_DAYS = 60
TOP_N = 5


@dataclass
class IdleItem:
    item_id: str
    name: str
    days: int
    never_worn: bool


@dataclass
class Stats:
    days: int
    total: int                      # 衣櫃裡狀態正常的件數
    worn: int                       # 期間內穿過的件數
    usage_rate: float               # 0–1
    outfit_count: int               # 期間內的穿搭紀錄筆數
    most_worn: list[tuple[str, str, int]] = field(default_factory=list)   # (id, 名稱, 次數)
    idle: list[IdleItem] = field(default_factory=list)
    styles: list[tuple[str, float]] = field(default_factory=list)         # (風格, 占比)
    colors: list[tuple[str, float]] = field(default_factory=list)

    @property
    def enough_data(self) -> bool:
        return self.total > 0 and self.outfit_count > 0


def _ids(raw) -> list[str]:
    return [p for p in str(raw or "").replace(" ", "").split(",") if p]


def _date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _shares(counter: Counter, limit: int = 5) -> list[tuple[str, float]]:
    total = sum(counter.values())
    if not total:
        return []
    return [(k, v / total) for k, v in counter.most_common(limit)]


def compute(store: UserStore, today: date, days: int = 30) -> Stats:
    days = max(1, min(days, 365))
    start = today - timedelta(days=days - 1)
    garments = store.read(WARDROBE)
    by_id = {g["衣服ID"]: g for g in garments}
    active = [g for g in garments if g["狀態"] == ACTIVE]

    last_worn: dict[str, date] = {}
    period_counts: Counter = Counter()
    style_counts: Counter = Counter()
    color_counts: Counter = Counter()
    outfit_count = 0

    for row in store.read(OUTFITS):
        if row["狀態"] != ACTIVE:
            continue
        day = _date(row["日期"])
        if day is None or day > today:
            continue
        ids = _ids(row["衣服ID清單"])
        for item_id in ids:
            if item_id not in last_worn or day > last_worn[item_id]:
                last_worn[item_id] = day
        if start <= day <= today:
            outfit_count += 1
            for item_id in ids:
                period_counts[item_id] += 1
                g = by_id.get(item_id)
                if g:  # 已刪除的衣服也算它當時被穿的風格與顏色
                    if g.get("風格"):
                        style_counts[g["風格"]] += 1
                    main_color = str(g.get("顏色") or "").split("、")[0]
                    if main_color:
                        color_counts[main_color] += 1

    active_ids = {g["衣服ID"] for g in active}
    worn = sum(1 for i in period_counts if i in active_ids)
    most_worn = [(i, by_id[i]["名稱"], n) for i, n in period_counts.most_common() if i in active_ids][:TOP_N]

    idle: list[IdleItem] = []
    for g in active:
        item_id = g["衣服ID"]
        if item_id in last_worn:
            since, never = last_worn[item_id], False
        else:
            added = _date(str(g.get("入庫時間") or "")[:10])
            if added is None:
                continue
            since, never = added, True
        gap = (today - since).days
        if gap >= IDLE_DAYS:
            idle.append(IdleItem(item_id, g["名稱"], gap, never))
    idle.sort(key=lambda x: x.days, reverse=True)

    return Stats(
        days=days, total=len(active), worn=worn,
        usage_rate=(worn / len(active)) if active else 0.0, outfit_count=outfit_count,
        most_worn=most_worn, idle=idle[:TOP_N],
        styles=_shares(style_counts), colors=_shares(color_counts),
    )


def learned_preferences(store: UserStore, today: date, days: int = 90) -> dict:
    """從穿搭紀錄學到的習慣（只當推薦時的參考）。"""
    s = compute(store, today, days)
    return {"styles": [k for k, _ in s.styles[:3]], "colors": [k for k, _ in s.colors[:3]]}
