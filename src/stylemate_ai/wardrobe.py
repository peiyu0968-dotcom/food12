"""電子衣櫃：加入、列表、查看、修改、軟刪除、復原、刪照片，並寫修改歷史。

每個函式都要帶 store（某位使用者自己的儲存）與 actor_id（發起者）。
本檔含一段防護：
  防護 8：衣服 ID 必須存在且屬於提問者（validate_ids，放在檔案上半部）
框外的 validate_ids 是「沒有防護」時的預設行為（不檢查），所以把整段註解掉，程式仍能執行。
在第一階段，ID 來自使用者打的指令；之後 AI 回傳的 ID（推薦穿搭等）也會走同一道檢查。
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Iterable

from . import access, excel_store, history, photos, timeutil
from .excel_store import HISTORY, OUTFITS, WARDROBE, UserStore
from .schemas import AI_JUDGED_FIELDS, USER_SPECIFIED, ClothingItem, normalize_edit

MAX_ITEMS = 200
PAGE_SIZE = 10
ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉容易看錯的 0 O 1 I
ID_LENGTH = 4
ACTIVE, DELETED = "正常", "已刪除"


class WardrobeError(Exception):
    """衣櫃操作失敗；訊息可以直接給使用者看。"""


class WardrobeFull(WardrobeError):
    def __init__(self, remaining: int) -> None:
        super().__init__(f"衣櫃最多 {MAX_ITEMS} 件，目前只剩 {remaining} 個位置；請先刪除一些舊的。")
        self.remaining = remaining


class NotFound(WardrobeError):
    pass


def normalize_id(raw: str) -> str:
    return raw.strip().lstrip("#＃").upper()


def validate_ids(ids: Iterable[str], rows: list[dict], *, allow_deleted: bool = False) -> list[str]:
    return list(ids)  # 沒有防護時：不檢查，來什麼 ID 就用什麼


# ==== 防護 8：衣服 ID 必須存在且屬於提問者　開始 ====
# rows 是「提問者自己的衣櫃」；只留下真的存在、狀態對得上的 ID，其餘（編造的、別人的）全部丟掉。
def validate_ids(ids: Iterable[str], rows: list[dict], *, allow_deleted: bool = False) -> list[str]:  # noqa: F811
    valid = {r["衣服ID"] for r in rows if allow_deleted or r["狀態"] == ACTIVE}
    return [i for i in ids if i in valid]
# ==== 防護 8 結束 ====


def _new_id(existing: set[str]) -> str:
    while True:
        candidate = "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
        if candidate not in existing:
            return candidate


def _history(wb, *, target_id: str, now: datetime, actor_id: int, action: str, reason: str,
             fields: str = "", before: str = "", after: str = "") -> None:
    history.add(wb, target_type="衣服", target_id=target_id, now=now, actor_id=actor_id, action=action,
                reason=reason, fields=fields, before=before, after=after)


def _describe(changes: dict) -> str:
    return history.describe(changes)


def _active(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["狀態"] == ACTIVE]


def _find(rows: list[dict], item_id: str, *, allow_deleted: bool) -> dict:
    ids = validate_ids([item_id], rows, allow_deleted=allow_deleted)
    for row in rows:
        if ids and row["衣服ID"] == ids[0]:
            return row
    raise NotFound(f"找不到衣服 #{item_id}")


# ---------------------------------------------------------------- 加入

def add_items(store: UserStore, actor_id: int, items: list[ClothingItem], photo_rel: str | None,
              now: datetime) -> list[dict]:
    """把使用者確認要加入的衣服寫進衣櫃，每件一列＋一筆歷史。超過上限就整批不加。"""
    assert access.is_owner(actor_id, store.user_id)

    def work(wb):
        rows = excel_store.sheet_rows(wb, WARDROBE)
        remaining = MAX_ITEMS - len(_active(rows))
        if len(items) > remaining:
            raise WardrobeFull(max(remaining, 0))
        taken = {r["衣服ID"] for r in rows}
        created = []
        for item in items:
            item_id = _new_id(taken)
            taken.add(item_id)
            record = {
                "衣服ID": item_id, "名稱": item.name, "種類": item.category_label,
                "顏色": "、".join(item.colors), "材質": item.material, "風格": item.style,
                "圖案": item.pattern, "適合季節": "、".join(item.seasons),
                "保暖程度": item.warmth, "正式程度": item.formality, "AI信心": item.confidence_label,
                "備註": "", "品牌": "", "照片位置": photo_rel,
                "入庫時間": timeutil.fmt(now), "狀態": ACTIVE, "最後修改時間": timeutil.fmt(now),
            }
            excel_store.append_row(wb, WARDROBE, record)
            _history(wb, target_id=item_id, now=now, actor_id=actor_id, action="新增", reason="加入衣櫃",
                     after=f"名稱={item.name}；種類={item.category_label}")
            created.append(record)
        return created

    return store.transact(work)


def active_count(store: UserStore) -> int:
    return len(_active(store.read(WARDROBE)))


# ---------------------------------------------------------------- 查詢

def wear_count(wb_or_rows_outfits: list[dict], item_id: str) -> int:
    """使用次數 = 穿搭紀錄（狀態正常）裡包含這件衣服的列數。"""
    total = 0
    for row in wb_or_rows_outfits:
        if row.get("狀態") == ACTIVE and item_id in str(row.get("衣服ID清單") or "").replace(" ", "").split(","):
            total += 1
    return total


def list_items(store: UserStore, *, category: str | None = None, color: str | None = None,
               style: str | None = None, keyword: str | None = None, page: int = 1) -> dict:
    rows = _active(store.read(WARDROBE))
    outfits = store.read(OUTFITS)

    def keep(r: dict) -> bool:
        if category and r["種類"] != category:
            return False
        if color and color not in str(r["顏色"] or ""):
            return False
        if style and style not in str(r["風格"] or ""):
            return False
        if keyword and keyword not in str(r["名稱"] or ""):
            return False
        return True

    matched = [r for r in rows if keep(r)]
    pages = max(1, -(-len(matched) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    chunk = matched[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return {
        "items": [dict(r, 使用次數=wear_count(outfits, r["衣服ID"])) for r in chunk],
        "total": len(matched), "all_active": len(rows), "page": page, "pages": pages,
    }


def get_item(store: UserStore, item_id: str, *, allow_deleted: bool = False) -> dict:
    rows = store.read(WARDROBE)
    row = _find(rows, item_id, allow_deleted=allow_deleted)
    return dict(row, 使用次數=wear_count(store.read(OUTFITS), item_id))


def item_history(store: UserStore, item_id: str, limit: int = 10) -> list[dict]:
    rows = [r for r in store.read(HISTORY) if r["對象"] == "衣服" and r["對象ID"] == item_id]
    return rows[-limit:]


def find_similar(store: UserStore, item: ClothingItem) -> list[dict]:
    """可能重複的衣服：同種類、主色相同、風格相同。"""
    return [
        r for r in _active(store.read(WARDROBE))
        if r["種類"] == item.category_label
        and str(r["顏色"] or "").split("、")[:1] == item.colors[:1]
        and r["風格"] == item.style
    ]


# ---------------------------------------------------------------- 修改、刪除、復原

def modify_item(store: UserStore, actor_id: int, item_id: str, raw_changes: dict[str, str],
                reason: str, now: datetime) -> dict:
    """修改欄位。回傳 {"before": {...}, "after": {...}}。每次修改都新增一筆歷史，不覆蓋舊的。"""
    assert access.is_owner(actor_id, store.user_id)
    changes = dict(normalize_edit(k, v) for k, v in raw_changes.items())
    if not changes:
        raise WardrobeError("沒有要修改的欄位")

    def work(wb):
        rows = excel_store.sheet_rows(wb, WARDROBE)
        row = _find(rows, item_id, allow_deleted=False)
        updates = dict(changes)
        if AI_JUDGED_FIELDS & set(changes):
            updates["AI信心"] = USER_SPECIFIED  # 使用者直接指定的值，AI 信心不再適用
        updates["最後修改時間"] = timeutil.fmt(now)
        before = {k: row.get(k) for k in updates if k != "最後修改時間"}
        excel_store.update_row(wb, WARDROBE, row["_row"], updates)
        after = {k: v for k, v in updates.items() if k != "最後修改時間"}
        _history(wb, target_id=row["衣服ID"], now=now, actor_id=actor_id, action="修改",
                 reason=reason, fields="、".join(after), before=_describe(before), after=_describe(after))
        return {"before": before, "after": after, "name": updates.get("名稱", row["名稱"])}

    return store.transact(work)


def _set_status(store: UserStore, actor_id: int, item_id: str, now: datetime, *, to: str,
                action: str, reason: str) -> dict:
    assert access.is_owner(actor_id, store.user_id)

    def work(wb):
        rows = excel_store.sheet_rows(wb, WARDROBE)
        row = _find(rows, item_id, allow_deleted=(to == ACTIVE))
        if to == ACTIVE:
            if row["狀態"] != DELETED:
                raise WardrobeError(f"#{row['衣服ID']} 沒有被刪除，不需要復原")
            if len(_active(rows)) >= MAX_ITEMS:
                raise WardrobeFull(0)
        excel_store.update_row(wb, WARDROBE, row["_row"], {"狀態": to, "最後修改時間": timeutil.fmt(now)})
        _history(wb, target_id=row["衣服ID"], now=now, actor_id=actor_id, action=action, reason=reason,
                 fields="狀態", before=f"狀態={row['狀態']}", after=f"狀態={to}")
        return {"id": row["衣服ID"], "name": row["名稱"]}

    return store.transact(work)


def soft_delete(store: UserStore, actor_id: int, item_id: str, now: datetime, reason: str = "使用者刪除") -> dict:
    """軟刪除：只標記「已刪除」，資料與照片都還在，可復原。"""
    return _set_status(store, actor_id, item_id, now, to=DELETED, action="刪除", reason=reason)


def restore(store: UserStore, actor_id: int, item_id: str, now: datetime, reason: str = "使用者復原") -> dict:
    return _set_status(store, actor_id, item_id, now, to=ACTIVE, action="復原", reason=reason)


def delete_photo(store: UserStore, actor_id: int, item_id: str, now: datetime) -> dict:
    """只刪照片、保留文字資料。同一張照片若還有別的衣服在用，只清掉這件的連結，檔案保留。"""
    assert access.is_owner(actor_id, store.user_id)

    def work(wb):
        rows = excel_store.sheet_rows(wb, WARDROBE)
        row = _find(rows, item_id, allow_deleted=True)
        rel = row.get("照片位置")
        if not rel:
            raise WardrobeError(f"#{row['衣服ID']} 目前沒有照片")
        shared = any(r is not row and r.get("照片位置") == rel for r in rows)
        excel_store.update_row(wb, WARDROBE, row["_row"], {"照片位置": None, "最後修改時間": timeutil.fmt(now)})
        _history(wb, target_id=row["衣服ID"], now=now, actor_id=actor_id, action="刪圖", reason="使用者刪除照片",
                 fields="照片位置", before=f"照片位置={rel}", after="照片位置=（已刪除）")
        return {"id": row["衣服ID"], "name": row["名稱"], "rel": rel, "shared": shared}

    result = store.transact(work)
    if not result["shared"]:  # 先改好 Excel，成功後才真的刪檔
        path = store.dir / result["rel"]
        try:
            photos.delete_photo_file(store.photos_dir, path)
        except ValueError:
            pass  # 路徑不在照片資料夾內：寧可不刪
    return result
