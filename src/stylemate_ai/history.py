"""修改歷史：只增不改。衣服、穿搭紀錄、個人設定的每次變動都在這裡新增一列。"""
from __future__ import annotations

from datetime import datetime

from . import excel_store, timeutil
from .excel_store import HISTORY


def add(wb, *, target_type: str, target_id: str, now: datetime, actor_id: int, action: str,
        reason: str, fields: str = "", before: str = "", after: str = "") -> None:
    number = len(excel_store.sheet_rows(wb, HISTORY)) + 1
    excel_store.append_row(wb, HISTORY, {
        "歷史ID": f"H{number:05d}", "對象": target_type, "對象ID": target_id,
        "修改時間": timeutil.fmt(now), "修改者ID": str(actor_id), "動作": action, "原因": reason,
        "變更欄位": fields, "修改前": before, "修改後": after,
    })


def describe(changes: dict) -> str:
    return "；".join(f"{k}={v}" for k, v in changes.items())
