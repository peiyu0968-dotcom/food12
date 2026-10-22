"""等使用者回應的待處理動作（選擇要加入哪幾件、確認刪除、同意隱私說明…），以及打字替代的解析。

每個待處理動作綁定「一則 bot 訊息」與「發起者的 Discord User ID」。按鈕與打字都走同一套：
由 bot 訊息 ID 找到待處理動作，再由引擎確認是不是發起者本人。
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

TTL = {
    "select": 600.0,           # 選擇要加入哪幾件：10 分鐘
    "consent": 600.0,
    "public_confirm": 120.0,
    "confirm_delete": 120.0,
    "confirm_delete_photo": 120.0,
    "delete_all_1": 120.0,
    "delete_all_2": 60.0,      # 第二道確認：60 秒
    "choose_item": 120.0,      # 名稱有好幾件符合：請使用者選
    "confirm_delete_outfit": 120.0,
    "reco": 1800.0,            # 推薦穿搭：30 分鐘內可以按「今天穿這套」
}

clock: Callable[[], float] = time.monotonic  # 測試時可以換掉


@dataclass
class Pending:
    kind: str
    owner_id: int
    data: dict[str, Any] = field(default_factory=dict)
    message_id: int | None = None
    message_text: str = ""
    created: float = field(default_factory=lambda: clock())
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def ttl(self) -> float:
        return TTL[self.kind]

    def expired(self, now: float | None = None) -> bool:
        return (clock() if now is None else now) - self.created >= self.ttl


class Registry:
    def __init__(self) -> None:
        self._by_message: dict[int, Pending] = {}
        self._lock = threading.Lock()

    def register(self, pending: Pending, message_id: int) -> None:
        pending.message_id = message_id
        with self._lock:
            self._by_message[message_id] = pending

    def get(self, message_id: int | None) -> Pending | None:
        if message_id is None:
            return None
        with self._lock:
            pending = self._by_message.get(message_id)
        return pending if pending and not pending.closed else None

    def remove(self, pending: Pending) -> None:
        with self._lock:
            if pending.message_id is not None:
                self._by_message.pop(pending.message_id, None)

    def due(self, now: float | None = None) -> list[Pending]:
        with self._lock:
            return [p for p in self._by_message.values() if not p.closed and p.expired(now)]

    def of_user(self, user_id: int) -> list[Pending]:
        with self._lock:
            return [p for p in self._by_message.values() if p.owner_id == user_id and not p.closed]

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_message)


# ---------------------------------------------------------------- 打字替代

_NUMBERS = re.compile(r"^[\d\s,，、]+$")
_YES = {"確認", "確定", "確認刪除", "yes", "y"}
_NO = {"取消", "不要", "否", "no", "n"}


def parse_reply(pending: Pending, text: str) -> str | None:
    """把使用者回覆的文字轉成動作代碼；看不懂就回 None（不處理）。"""
    t = re.sub(r"<@!?\d+>", "", text).strip().lower().rstrip("。.!！")
    kind = pending.kind
    if kind == "select":
        if t in ("全部", "全部加入", "都加入"):
            return "all"
        if t in ("都不要", "不要", "取消", "不加入"):
            return "none"
        if _NUMBERS.match(t):
            nums = [n for n in re.split(r"[\s,，、]+", t) if n]
            return "pick:" + ",".join(nums) if nums else None
        return None
    if kind == "choose_item":
        if _NUMBERS.match(t) and len(re.split(r"[\s,，、]+", t.strip(" ,，、"))) == 1:
            return "pick:" + t.strip(" ,，、")
        return "cancel" if t in _NO else None
    if kind == "reco":
        if t in ("穿這套", "我穿這套", "今天穿這套", "記錄", "記錄穿搭"):
            return "wear"
        if t in ("再來一套", "再一套", "換一套", "下一套"):
            return "more"
        return None
    if kind in ("confirm_delete", "confirm_delete_photo", "confirm_delete_outfit", "delete_all_1"):
        return "confirm" if t in _YES else "cancel" if t in _NO else None
    if kind == "delete_all_2":
        if text.strip() == "確認刪除全部":  # 必須一字不差
            return "execute"
        return "cancel" if t in _NO else None
    if kind == "public_confirm":
        return "confirm" if t == "確認公開" else "cancel" if t in _NO else None
    if kind == "consent":
        if t == "我同意":
            return "agree"
        return "decline" if t in ("不同意", "取消", "不要") else None
    return None
