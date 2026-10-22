"""時間一律用台灣時間（Asia/Taipei），存成不帶時區的字串。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
FORMAT = "%Y-%m-%d %H:%M:%S"


def now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def to_taipei(moment: datetime) -> datetime:
    """Discord 給的時間是 UTC（帶時區）；轉成台灣時間再去掉時區資訊。"""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(TZ).replace(tzinfo=None)


def fmt(moment: datetime) -> str:
    return moment.strftime(FORMAT)
