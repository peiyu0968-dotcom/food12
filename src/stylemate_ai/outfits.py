"""穿搭紀錄：每次「今天穿了什麼」一列。可補記過去、同一天可記多次、可修改／軟刪除／復原，全部寫修改歷史。

衣服的「使用次數」不另外存，而是由這張表即時算出（見 wardrobe.wear_count）。
"""
from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta

from . import access, excel_store, history, timeutil, wardrobe
from .excel_store import OUTFITS, WARDROBE, UserStore
from .wardrobe import ACTIVE, DELETED, ID_ALPHABET, NotFound, WardrobeError

MAX_ITEMS_PER_OUTFIT = 10
MAX_DAYS_BACK = 365
SOURCE_SELF, SOURCE_RECO = "自選", "推薦"

_DAY_WORDS = {"今天": 0, "今日": 0, "昨天": -1, "昨日": -1, "前天": -2, "明天": 1, "明日": 1, "後天": 2, "后天": 2}


def parse_day_word(token: str, today: date) -> date | None:
    """今天／昨天／前天／明天／後天、2026-10-20、10/20、10月20日；看不懂回 None。"""
    t = token.strip()
    if t in _DAY_WORDS:
        return today + timedelta(days=_DAY_WORDS[t])
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/月.](\d{1,2})日?", t)
        if not m:
            return None
        mo, d = map(int, m.groups())
        y = today.year
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _new_id(existing: set[str]) -> str:
    while True:
        candidate = "O" + "".join(secrets.choice(ID_ALPHABET) for _ in range(4))
        if candidate not in existing:
            return candidate


def normalize_outfit_id(raw: str) -> str:
    return raw.strip().lstrip("#＃").upper()


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", text or "")).strip()[:limit]


def _split_ids(raw) -> list[str]:
    return [p for p in str(raw or "").replace(" ", "").split(",") if p]


def _validate_day(day: date, now: datetime) -> None:
    today = now.date()
    if day > today:
        raise WardrobeError("不能記錄未來的日子，請填今天或過去的日期。")
    if (today - day).days > MAX_DAYS_BACK:
        raise WardrobeError("最多只能補記一年內的穿搭。")


def _active_ids(wb) -> list[dict]:
    return excel_store.sheet_rows(wb, WARDROBE)


def log_outfit(store: UserStore, actor_id: int, item_ids: list[str], day: date, now: datetime, *,
               occasion: str = "", weather: str = "", source: str = SOURCE_SELF) -> dict:
    """記錄一次穿搭。衣服 ID 必須都是這位使用者衣櫃裡「正常」的衣服（防護 8）。"""
    assert access.is_owner(actor_id, store.user_id)
    _validate_day(day, now)
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise WardrobeError("沒有指定任何衣服。")
    if len(ids) > MAX_ITEMS_PER_OUTFIT:
        raise WardrobeError(f"一次最多記 {MAX_ITEMS_PER_OUTFIT} 件。")

    def work(wb):
        rows = _active_ids(wb)
        valid = wardrobe.validate_ids(ids, rows)
        missing = [i for i in ids if i not in valid]
        if missing:
            raise NotFound("找不到這些衣服（或已被刪除）：" + "、".join(f"#{i}" for i in missing))
        taken = {r["紀錄ID"] for r in excel_store.sheet_rows(wb, OUTFITS)}
        outfit_id = _new_id(taken)
        record = {
            "紀錄ID": outfit_id, "日期": day.isoformat(), "記錄時間": timeutil.fmt(now),
            "衣服ID清單": ",".join(ids), "場合": _clean(occasion, 40), "天氣": _clean(weather, 60),
            "來源": source, "狀態": ACTIVE, "最後修改時間": timeutil.fmt(now),
        }
        excel_store.append_row(wb, OUTFITS, record)
        history.add(wb, target_type="穿搭紀錄", target_id=outfit_id, now=now, actor_id=actor_id, action="新增",
                    reason=f"記錄穿搭（{source}）", after=f"日期={day.isoformat()}；衣服={','.join(ids)}")
        return record

    return store.transact(work)


def _names(rows: list[dict]) -> dict[str, str]:
    return {r["衣服ID"]: r["名稱"] for r in rows}


def list_outfits(store: UserStore, today: date, days: int = 30) -> list[dict]:
    """最近 N 天（含今天）狀態正常的穿搭紀錄，由新到舊；附上衣服名稱。"""
    days = max(1, min(days, 365))
    start = today - timedelta(days=days - 1)
    names = _names(store.read(WARDROBE))
    out = []
    for r in store.read(OUTFITS):
        if r["狀態"] != ACTIVE:
            continue
        try:
            d = date.fromisoformat(str(r["日期"]))
        except ValueError:
            continue
        if start <= d <= today:
            ids = _split_ids(r["衣服ID清單"])
            out.append(dict(r, 日期物件=d, 衣服=[(i, names.get(i, "（已不在衣櫃）")) for i in ids]))
    out.sort(key=lambda r: (r["日期物件"], r["記錄時間"]), reverse=True)
    return out


def get_outfit(store: UserStore, outfit_id: str, *, allow_deleted: bool = False) -> dict:
    for r in store.read(OUTFITS):
        if r["紀錄ID"] == outfit_id and (allow_deleted or r["狀態"] == ACTIVE):
            names = _names(store.read(WARDROBE))
            return dict(r, 衣服=[(i, names.get(i, "（已不在衣櫃）")) for i in _split_ids(r["衣服ID清單"])])
    raise NotFound(f"找不到穿搭紀錄 #{outfit_id}")


def _find(rows: list[dict], outfit_id: str, *, allow_deleted: bool) -> dict:
    for r in rows:
        if r["紀錄ID"] == outfit_id and (allow_deleted or r["狀態"] == ACTIVE):
            return r
    raise NotFound(f"找不到穿搭紀錄 #{outfit_id}")


def modify_outfit(store: UserStore, actor_id: int, outfit_id: str, changes: dict, reason: str, now: datetime) -> dict:
    """changes 可含：日期（date）、場合、天氣、衣服（ID 清單）。每次修改新增一筆歷史。"""
    assert access.is_owner(actor_id, store.user_id)
    if not changes:
        raise WardrobeError("沒有要修改的欄位")

    def work(wb):
        rows = excel_store.sheet_rows(wb, OUTFITS)
        row = _find(rows, outfit_id, allow_deleted=False)
        updates: dict = {}
        if "日期" in changes:
            _validate_day(changes["日期"], now)
            updates["日期"] = changes["日期"].isoformat()
        if "場合" in changes:
            updates["場合"] = _clean(changes["場合"], 40)
        if "天氣" in changes:
            updates["天氣"] = _clean(changes["天氣"], 60)
        if "衣服" in changes:
            ids = list(dict.fromkeys(changes["衣服"]))
            if not ids or len(ids) > MAX_ITEMS_PER_OUTFIT:
                raise WardrobeError(f"衣服要 1 到 {MAX_ITEMS_PER_OUTFIT} 件。")
            valid = wardrobe.validate_ids(ids, _active_ids(wb))
            missing = [i for i in ids if i not in valid]
            if missing:
                raise NotFound("找不到這些衣服（或已被刪除）：" + "、".join(f"#{i}" for i in missing))
            updates["衣服ID清單"] = ",".join(ids)
        updates["最後修改時間"] = timeutil.fmt(now)
        before = {k: row.get(k) for k in updates if k != "最後修改時間"}
        excel_store.update_row(wb, OUTFITS, row["_row"], updates)
        after = {k: v for k, v in updates.items() if k != "最後修改時間"}
        history.add(wb, target_type="穿搭紀錄", target_id=outfit_id, now=now, actor_id=actor_id, action="修改",
                    reason=reason, fields="、".join(after), before=history.describe(before), after=history.describe(after))
        return {"before": before, "after": after}

    return store.transact(work)


def _set_status(store: UserStore, actor_id: int, outfit_id: str, now: datetime, *, to: str, action: str, reason: str) -> dict:
    assert access.is_owner(actor_id, store.user_id)

    def work(wb):
        rows = excel_store.sheet_rows(wb, OUTFITS)
        row = _find(rows, outfit_id, allow_deleted=(to == ACTIVE))
        if to == ACTIVE and row["狀態"] != DELETED:
            raise WardrobeError(f"#{outfit_id} 沒有被刪除，不需要復原")
        excel_store.update_row(wb, OUTFITS, row["_row"], {"狀態": to, "最後修改時間": timeutil.fmt(now)})
        history.add(wb, target_type="穿搭紀錄", target_id=outfit_id, now=now, actor_id=actor_id, action=action,
                    reason=reason, fields="狀態", before=f"狀態={row['狀態']}", after=f"狀態={to}")
        return {"id": outfit_id, "date": row["日期"]}

    return store.transact(work)


def soft_delete_outfit(store: UserStore, actor_id: int, outfit_id: str, now: datetime, reason: str = "使用者刪除") -> dict:
    return _set_status(store, actor_id, outfit_id, now, to=DELETED, action="刪除", reason=reason)


def restore_outfit(store: UserStore, actor_id: int, outfit_id: str, now: datetime, reason: str = "使用者復原") -> dict:
    return _set_status(store, actor_id, outfit_id, now, to=ACTIVE, action="復原", reason=reason)
