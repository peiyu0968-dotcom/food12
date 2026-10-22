"""公開展示模式：使用者確認「接下來的內容會被頻道所有人看到」之後，30 分鐘內可以連續公開顯示。

每位使用者依 Discord User ID 各自計時，互不影響；只存在記憶體，bot 重啟就結束。
"""
from __future__ import annotations

import math
import threading
import time
from typing import Callable

DURATION_SECONDS = 30 * 60


class PublicMode:
    def __init__(self, duration: float = DURATION_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.duration = duration
        self._clock = clock
        self._until: dict[int, float] = {}
        self._lock = threading.Lock()

    def activate(self, user_id: int) -> None:
        with self._lock:
            self._until[user_id] = self._clock() + self.duration

    def end(self, user_id: int) -> bool:
        with self._lock:
            return self._until.pop(user_id, None) is not None

    def remaining(self, user_id: int) -> float:
        with self._lock:
            until = self._until.get(user_id)
            if until is None:
                return 0.0
            left = until - self._clock()
            if left <= 0:
                del self._until[user_id]
                return 0.0
            return left

    def active(self, user_id: int) -> bool:
        return self.remaining(user_id) > 0

    def banner(self, user_id: int) -> str:
        left = self.remaining(user_id)
        if left <= 0:
            return ""
        return f"🔓 公開顯示中（剩 {max(1, math.ceil(left / 60))} 分鐘）\n"
