"""個人設定：城市（含座標，用來查天氣）和偏好。存在使用者自己 Excel 的「個人設定」工作表（項目／值）。"""
from __future__ import annotations

import re
from datetime import datetime

from . import access, excel_store, history, timeutil
from .excel_store import SETTINGS, UserStore

MAX_PREF_CHARS = 300


def _rows(store: UserStore) -> dict[str, str]:
    return {r["項目"]: ("" if r["值"] is None else str(r["值"])) for r in store.read(SETTINGS)}


def get(store: UserStore) -> dict:
    """回傳 {"城市": str|None, "緯度": float|None, "經度": float|None, "偏好": str}。"""
    rows = _rows(store)

    def num(key: str):
        try:
            return float(rows[key])
        except (KeyError, ValueError):
            return None

    return {"城市": rows.get("城市") or None, "緯度": num("緯度"), "經度": num("經度"), "偏好": rows.get("偏好", "")}


def _upsert(wb, key: str, value: str) -> str | None:
    """寫入一個設定；回傳舊值（沒有就 None）。"""
    rows = excel_store.sheet_rows(wb, SETTINGS)
    existing = next((r for r in rows if r.get("項目") == key), None)
    if existing:
        excel_store.update_row(wb, SETTINGS, existing["_row"], {"值": value})
        return "" if existing["值"] is None else str(existing["值"])
    excel_store.append_row(wb, SETTINGS, {"項目": key, "值": value})
    return None


def set_city(store: UserStore, actor_id: int, label: str, lat: float, lon: float, now: datetime) -> None:
    assert access.is_owner(actor_id, store.user_id)

    def work(wb):
        before = _upsert(wb, "城市", label)
        _upsert(wb, "緯度", f"{lat:.4f}")
        _upsert(wb, "經度", f"{lon:.4f}")
        history.add(wb, target_type="設定", target_id="城市", now=now, actor_id=actor_id, action="修改" if before else "新增",
                    reason="設定城市", fields="城市", before=f"城市={before or ''}", after=f"城市={label}")

    store.transact(work)


def clean_preferences(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", text or "")).strip()[:MAX_PREF_CHARS]


def set_preferences(store: UserStore, actor_id: int, text: str, now: datetime) -> str:
    """寫入偏好文字（空字串＝清除）。回傳存下來的文字。"""
    assert access.is_owner(actor_id, store.user_id)
    cleaned = clean_preferences(text)

    def work(wb):
        before = _upsert(wb, "偏好", cleaned)
        action = "刪除" if not cleaned else ("修改" if before else "新增")
        history.add(wb, target_type="設定", target_id="偏好", now=now, actor_id=actor_id, action=action,
                    reason="設定偏好", fields="偏好", before=f"偏好={before or ''}", after=f"偏好={cleaned}")

    store.transact(work)
    return cleaned
