"""引擎：把「誰（Actor）說了什麼」變成「要回覆什麼（Out）」。不碰 Discord 物件，所以可以單獨測試。

所有資料存取都只用發起者自己的 UserStore；按鈕與打字確認都經過 resolve()，
由 access.is_owner（防護 6）確認是發起者本人。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable

from . import access, claude_client, outfits, photos, privacy, recommend, replies, stats, timeutil, wardrobe
from .config import Config
from .excel_store import WARDROBE, StorageError, UserStore
from .limits import Limiter
from .pending import Pending, Registry, parse_reply
from .schemas import (CATEGORY_BY_LABEL, AnalysisError, AnalysisResult, ClothingItem, EditError)
from .visibility import PublicMode

log = logging.getLogger(__name__)

# 會顯示個人資料的指令：在公開頻道要先提醒、確認（私訊與斜線指令的私人回覆不需要）
GATED_COMMANDS = {"衣櫃", "查看", "推薦", "再來一套", "穿搭紀錄", "統計"}

# 簡化版只提供三個核心功能：辨識加入衣櫃、衣櫃管理、AI 推薦穿搭。
# 下面這些進階功能的程式都保留著，但預設關閉（Engine(advanced=False)）；測試或之後想開放時用 advanced=True。
ADVANCED_COMMANDS = {"穿搭", "穿搭紀錄", "改穿搭", "刪除穿搭", "復原穿搭", "統計", "設定", "偏好"}
RECO_TTL_SECONDS = 1800


@dataclass(frozen=True)
class Actor:
    user_id: int
    name: str


@dataclass
class Button:
    label: str
    action: str
    style: str = "secondary"  # primary / secondary / success / danger


@dataclass
class Out:
    public: str | None = None
    private: str | None = None
    file: Path | None = None
    pending: Pending | None = None                 # 這則回覆要等使用者回應：bot 發出後登記
    buttons: list[Button] = field(default_factory=list)
    update_buttons: list[Button] | None = None     # resolve() 之後，原訊息要換成哪組按鈕（None＝移除按鈕）
    keep_pending: bool = False                     # 原本的待處理動作還沒結束
    denied: bool = False                           # 不是發起者本人
    resume: list | None = None                     # 同意後要接著處理的上傳（由 bot 解讀）
    ai_job: Callable[[], Awaitable["Out"]] | None = None  # 需要 claude -p 的工作：bot 排進隊伍再執行


@dataclass
class Ctx:
    """一次請求的情境：誰、是不是私人回覆（私訊／斜線指令預設私下）、原始文字。"""
    actor: Actor
    private: bool
    text: str
    reveal: bool    # 可以直接顯示個人資料（私人回覆，或已確認公開）
    banner: str     # 公開顯示中的橫幅


@dataclass
class Resolved:
    item_id: str | None = None
    out: Out | None = None


Analyzer = Callable[[Config, bytes, str, str], Awaitable[AnalysisResult]]


def _clean_arg(text: str, limit: int = 100) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", text or "")).strip()[:limit]


def _days_arg(args: str, default: int = 30) -> int:
    m = re.search(r"(\d{1,3})", args or "")
    return max(1, min(int(m.group(1)), 365)) if m else default


class Engine:
    def __init__(self, config: Config, *, registry: Registry | None = None, limiter: Limiter | None = None,
                 public_mode: PublicMode | None = None, analyzer: Analyzer | None = None,
                 clock: Callable[[], datetime] = timeutil.now, retries: int = 10, delay: float = 3.0,
                 advanced: bool = False, ask=None, get_json=None) -> None:
        self.config = config
        self.advanced = advanced      # 進階功能（穿搭紀錄、統計、天氣、偏好）是否開放；預設關閉
        self._ask, self._get_json = ask, get_json
        self._last_reco: dict[int, dict] = {}
        self.registry = registry or Registry()
        self.limiter = limiter or Limiter()
        self.public_mode = public_mode or PublicMode()
        self.analyzer = analyzer or claude_client.analyze_image
        self.clock = clock
        self._retries, self._delay = retries, delay
        self._consented: set[int] = set()

    # ---------------------------------------------------------------- 基本工具

    def store(self, user_id: int) -> UserStore:
        return UserStore(self.config.data_dir, user_id, retries=self._retries, delay=self._delay)

    def has_consent(self, user_id: int) -> bool:
        if user_id in self._consented:
            return True
        if privacy.has_consent(self.store(user_id)):
            self._consented.add(user_id)
            return True
        return False

    def consent_prompt(self, actor: Actor, resume: list | None = None) -> Out:
        pending = Pending("consent", actor.user_id, {"resume": resume or [], "actor_name": actor.name})
        return Out(public=privacy.NOTICE, pending=pending,
                   buttons=[Button("我同意", "agree", "success"), Button("不同意", "decline", "secondary")])

    def _ctx(self, actor: Actor, text: str, private: bool) -> Ctx:
        active = self.public_mode.active(actor.user_id)
        return Ctx(actor, private, text, reveal=private or active,
                   banner="" if private else self.public_mode.banner(actor.user_id))

    def _close(self, pending: Pending) -> None:
        if pending.closed:
            return
        pending.closed = True
        self.registry.remove(pending)
        if pending.kind == "select":
            pending.data["image"] = None  # 沒存的照片從記憶體丟掉
            if pending.data.get("inflight"):
                self.limiter.release(pending.owner_id)
                pending.data["inflight"] = False

    def sweep(self) -> list[Pending]:
        """關掉逾時的待處理動作，回傳給 bot 去更新訊息。"""
        expired = self.registry.due()
        for p in expired:
            with p.lock:
                self._close(p)
        return expired

    # ---------------------------------------------------------------- 文字指令

    def _command_table(self) -> dict[str, Callable[[Ctx, str], Out]]:
        return {
            "衣櫃": self._cmd_closet, "查看": self._cmd_view, "修改": self._cmd_modify,
            "刪除": self._cmd_delete, "復原": self._cmd_restore, "刪圖": self._cmd_delete_photo,
            "刪除全部資料": self._cmd_delete_all,
            "穿搭": self._cmd_wear, "穿搭紀錄": self._cmd_outfits, "改穿搭": self._cmd_edit_outfit,
            "刪除穿搭": self._cmd_delete_outfit, "復原穿搭": self._cmd_restore_outfit,
            "統計": self._cmd_stats,
            "推薦": self._cmd_recommend, "再來一套": self._cmd_more_outfit,
        }

    def run_command(self, actor: Actor, text: str, *, private: bool = False) -> Out:
        body = text.strip().lstrip("!！").strip()
        if not body:
            return Out(public=replies.HELP)
        cmd, _, args = body.partition(" ")
        cmd, args = cmd.strip(), args.strip()

        if cmd in ("說明", "幫助", "help"):
            return Out(public=replies.HELP if not self.advanced else replies.HELP_ADVANCED)
        if cmd in ADVANCED_COMMANDS and not self.advanced:
            return Out(public=replies.NOT_AVAILABLE)
        if not self.has_consent(actor.user_id):
            return self.consent_prompt(actor)
        if cmd == "結束公開":
            ended = self.public_mode.end(actor.user_id)
            return Out(public="✅ 已結束公開顯示模式。" if ended else "目前沒有開啟公開顯示模式。")

        handler = self._command_table().get(cmd)
        if handler is None:
            return Out(public=f"我不認得「!{cmd}」。輸入 `!說明` 看可以用的指令。")

        if cmd in GATED_COMMANDS and not private and not self.public_mode.active(actor.user_id):
            return self._public_warning(actor, text)
        ctx = self._ctx(actor, text, private)
        try:
            return handler(ctx, args)
        except (wardrobe.WardrobeError, EditError) as exc:
            return Out(public=str(exc))
        except StorageError as exc:
            log.error("Excel 寫入失敗（使用者 %s）：%s", actor.user_id, exc)
            return Out(public=replies.STORAGE_FAIL_TEXT)

    def _public_warning(self, actor: Actor, original_text: str) -> Out:
        pending = Pending("public_confirm", actor.user_id, {"command": original_text, "actor_name": actor.name})
        return Out(public=replies.PUBLIC_WARNING, pending=pending,
                   buttons=[Button("確認公開", "confirm", "danger"), Button("取消", "cancel")])

    # ---- 用名稱或編號找衣服

    def _resolve_ref(self, ctx: Ctx, token: str, *, allow_deleted: bool = False) -> Resolved:
        """token 可以是衣服 ID，或名稱的一部分。名稱對到好幾件時，請使用者選。"""
        rows = self.store(ctx.actor.user_id).read(WARDROBE)
        cands = [r for r in rows if allow_deleted or r["狀態"] == wardrobe.ACTIVE]
        wanted = wardrobe.normalize_id(token)
        for r in cands:
            if r["衣服ID"] == wanted:
                return Resolved(item_id=r["衣服ID"])
        low = token.strip().lower()
        if not low:
            return Resolved(out=Out(public="請告訴我是哪一件衣服（編號或名稱）。"))
        exact = [r for r in cands if str(r["名稱"]).lower() == low]
        if len(exact) == 1:
            return Resolved(item_id=exact[0]["衣服ID"])
        part = [r for r in cands if low in str(r["名稱"]).lower()]
        if len(part) == 1:
            return Resolved(item_id=part[0]["衣服ID"])
        if not part:
            return Resolved(out=Out(public=f"找不到「{_clean_arg(token, 30)}」。用 `!衣櫃` 看你有哪些衣服。"))
        if not ctx.reveal:
            return Resolved(out=Out(public=f"有 {len(part)} 件都符合「{_clean_arg(token, 30)}」，請改用編號（ID）。"))
        shown = part[:8]
        pending = Pending("choose_item", ctx.actor.user_id, {
            "command": ctx.text, "token": token, "private": ctx.private,
            "ids": [r["衣服ID"] for r in shown], "actor_name": ctx.actor.name,
        })
        lines = [f"{ctx.banner}有 {len(part)} 件都符合「{_clean_arg(token, 30)}」，請選一件（按按鈕，或回覆編號）："]
        lines += [f"{i}. #{r['衣服ID']} {r['名稱']}｜{r['顏色']}・{r['風格']}" for i, r in enumerate(shown, start=1)]
        return Resolved(out=Out(
            public="\n".join(lines), pending=pending,
            buttons=[Button(f"{i}", f"pick:{i}", "primary") for i in range(1, len(shown) + 1)] + [Button("取消", "cancel")],
        ))

    def _first_arg(self, args: str) -> str:
        parts = args.split()
        return parts[0] if parts else ""

    # ---- 衣櫃、查看

    def _cmd_closet(self, ctx: Ctx, args: str) -> Out:
        category = color = style = keyword = None
        page = 1
        for token in args.split():
            m = re.fullmatch(r"(?:頁[:：]?|第)?(\d+)頁?", token)
            if m:
                page = int(m.group(1))
            elif token in CATEGORY_BY_LABEL:
                category = token
            elif re.match(r"^(顏色|色)[:：]", token):
                color = re.split(r"[:：]", token, 1)[-1]
            elif re.match(r"^風格[:：]", token):
                style = re.split(r"[:：]", token, 1)[-1]
            elif re.match(r"^種類[:：]", token):
                category = re.split(r"[:：]", token, 1)[-1]
            else:
                keyword = token
        filters = " ".join(x for x in (category, f"顏色:{color}" if color else None,
                                       f"風格:{style}" if style else None, keyword) if x)
        data = wardrobe.list_items(self.store(ctx.actor.user_id), category=category, color=color,
                                   style=style, keyword=keyword, page=page)
        return Out(public=replies.closet(data, filters, ctx.banner, show_wear=self.advanced))

    def _cmd_view(self, ctx: Ctx, args: str) -> Out:
        if not self._first_arg(args):
            return Out(public="用法：`!查看 <衣服ID或名稱>`，例如 `!查看 K7M2`")
        res = self._resolve_ref(ctx, self._first_arg(args), allow_deleted=True)
        if res.out:
            return res.out
        store = self.store(ctx.actor.user_id)
        item = wardrobe.get_item(store, res.item_id, allow_deleted=True)
        history = wardrobe.item_history(store, res.item_id)
        out = Out(public=replies.detail(item, history, ctx.banner, show_wear=self.advanced))
        rel = item.get("照片位置")
        if rel:
            path = (store.dir / rel).resolve()
            if path.is_relative_to(store.photos_dir.resolve()) and path.exists():
                out.file = path
        return out

    # ---- 修改、刪除、復原、刪圖

    def _cmd_modify(self, ctx: Ctx, args: str) -> Out:
        usage = "用法：`!修改 <ID> 材質=亞麻 保暖=2 原因:洗過縮水`"
        parts = args.split(None, 1)
        if len(parts) < 2:
            return Out(public=usage)
        reason = "使用者修改"
        rest = parts[1]
        m = re.search(r"原因\s*[:：]\s*(.*)$", rest, re.DOTALL)
        if m:
            reason = _clean_arg(m.group(1)) or reason
            rest = rest[: m.start()]
        pairs = re.findall(r"(\S+?)[=＝](\S+)", rest)
        if not pairs:
            return Out(public=usage)
        res = self._resolve_ref(ctx, parts[0])
        if res.out:
            return res.out
        result = wardrobe.modify_item(self.store(ctx.actor.user_id), ctx.actor.user_id, res.item_id,
                                      dict(pairs), reason, self.clock())
        if ctx.reveal:
            diff = "\n".join(f"• {k}：{result['before'].get(k)} → {v}" for k, v in result["after"].items())
            return Out(public=f"{ctx.banner}✅ 已更新 #{res.item_id}（{result['name']}）\n{diff}")
        return Out(public=f"✅ 已更新 #{res.item_id} 的 {'、'.join(result['after'])}。修改歷史已記錄。")

    def _cmd_delete(self, ctx: Ctx, args: str) -> Out:
        if not self._first_arg(args):
            return Out(public="用法：`!刪除 <衣服ID或名稱>`")
        res = self._resolve_ref(ctx, self._first_arg(args))
        if res.out:
            return res.out
        item = wardrobe.get_item(self.store(ctx.actor.user_id), res.item_id)
        label = f"#{res.item_id}（{item['名稱']}）" if ctx.reveal else f"#{res.item_id}"
        pending = Pending("confirm_delete", ctx.actor.user_id, {"item_id": res.item_id, "actor_name": ctx.actor.name})
        return Out(public=f"⚠️ 要刪除 {label} 嗎？這是軟刪除，之後可以用 `!復原 {res.item_id}` 救回。\n"
                          "請按「確認刪除」或回覆「確認」；不要的話按「取消」或回覆「取消」。",
                   pending=pending, buttons=[Button("確認刪除", "confirm", "danger"), Button("取消", "cancel")])

    def _cmd_restore(self, ctx: Ctx, args: str) -> Out:
        if not self._first_arg(args):
            return Out(public="用法：`!復原 <衣服ID或名稱>`")
        res = self._resolve_ref(ctx, self._first_arg(args), allow_deleted=True)
        if res.out:
            return res.out
        done = wardrobe.restore(self.store(ctx.actor.user_id), ctx.actor.user_id, res.item_id, self.clock())
        label = f"#{done['id']}（{done['name']}）" if ctx.reveal else f"#{done['id']}"
        return Out(public=f"{ctx.banner}✅ 已復原 {label}。")

    def _cmd_delete_photo(self, ctx: Ctx, args: str) -> Out:
        if not self._first_arg(args):
            return Out(public="用法：`!刪圖 <衣服ID或名稱>`（只刪照片，文字資料保留）")
        res = self._resolve_ref(ctx, self._first_arg(args), allow_deleted=True)
        if res.out:
            return res.out
        item = wardrobe.get_item(self.store(ctx.actor.user_id), res.item_id, allow_deleted=True)
        if not item.get("照片位置"):
            return Out(public=f"#{res.item_id} 目前沒有照片。")
        pending = Pending("confirm_delete_photo", ctx.actor.user_id, {"item_id": res.item_id, "actor_name": ctx.actor.name})
        return Out(public=f"⚠️ 要刪除 #{res.item_id} 的照片嗎？文字資料會保留，照片刪掉就無法救回。\n"
                          "請按「確認刪除」或回覆「確認」。",
                   pending=pending, buttons=[Button("確認刪除", "confirm", "danger"), Button("取消", "cancel")])

    def _cmd_delete_all(self, ctx: Ctx, args: str) -> Out:
        pending = Pending("delete_all_1", ctx.actor.user_id, {"actor_name": ctx.actor.name})
        return Out(public="⚠️ **刪除你的全部資料**\n"
                          "這會永久刪除你的所有衣服照片、衣櫃、穿搭紀錄、修改歷史和設定，**無法復原**。\n"
                          "要繼續請按「繼續」或回覆「確認」；之後還會再確認一次。",
                   pending=pending, buttons=[Button("繼續", "confirm", "danger"), Button("取消", "cancel")])

    # ---- 第二階段：穿搭紀錄與統計

    def _today(self) -> date:
        return self.clock().date()

    def _cmd_wear(self, ctx: Ctx, args: str) -> Out:
        """!穿搭 白襯衫 牛仔褲 [昨天|10/20] [場合:上班]"""
        usage = "用法：`!穿搭 白襯衫 牛仔褲`（可加 `昨天`、`10/20`、`場合:上班`）"
        day, occasion, tokens = self._today(), "", []
        for tok in [t for t in re.split(r"[\s、,，]+", args) if t]:
            parsed = outfits.parse_day_word(tok, self._today())
            m = re.match(r"^(?:場合|情境)[:：](.+)$", tok)
            if parsed:
                day = parsed
            elif m:
                occasion = m.group(1)
            else:
                tokens.append(tok)
        if not tokens:
            return Out(public=usage)
        ids = []
        for tok in tokens:
            res = self._resolve_ref(ctx, tok)
            if res.out:
                return res.out
            ids.append(res.item_id)
        store = self.store(ctx.actor.user_id)
        rec = outfits.log_outfit(store, ctx.actor.user_id, ids, day, self.clock(), occasion=occasion)
        if ctx.reveal:
            names = "、".join(n for _, n in outfits.get_outfit(store, rec["紀錄ID"])["衣服"])
            return Out(public=f"{ctx.banner}✅ 已記錄 {day.isoformat()} 的穿搭（#{rec['紀錄ID']}）：{names}")
        return Out(public=f"✅ 已記錄 {day.isoformat()} 的穿搭（{len(ids)} 件，紀錄 #{rec['紀錄ID']}）。")

    def _cmd_outfits(self, ctx: Ctx, args: str) -> Out:
        days = _days_arg(args)
        records = outfits.list_outfits(self.store(ctx.actor.user_id), self._today(), days)
        return Out(public=replies.outfit_list(records, days, ctx.banner))

    def _cmd_edit_outfit(self, ctx: Ctx, args: str) -> Out:
        usage = "用法：`!改穿搭 <紀錄ID> 日期=昨天 場合=上班 衣服=白襯衫,牛仔褲 原因:記錯了`"
        parts = args.split(None, 1)
        if len(parts) < 2:
            return Out(public=usage)
        outfit_id = outfits.normalize_outfit_id(parts[0])
        rest, reason = parts[1], "使用者修改"
        m = re.search(r"原因\s*[:：]\s*(.*)$", rest, re.DOTALL)
        if m:
            reason = _clean_arg(m.group(1)) or reason
            rest = rest[: m.start()]
        pairs = dict(re.findall(r"(\S+?)[=＝](\S+)", rest))
        if not pairs:
            return Out(public=usage)
        changes: dict = {}
        for key, value in pairs.items():
            if key in ("日期", "日"):
                day = outfits.parse_day_word(value, self._today())
                if day is None:
                    return Out(public=f"看不懂日期「{_clean_arg(value, 20)}」。可以寫 昨天、10/20 或 2026-10-20。")
                changes["日期"] = day
            elif key in ("場合", "情境"):
                changes["場合"] = value
            elif key == "天氣":
                changes["天氣"] = value
            elif key == "衣服":
                ids = []
                for tok in [t for t in re.split(r"[、,，]+", value) if t]:
                    res = self._resolve_ref(ctx, tok)
                    if res.out or res.item_id is None:
                        return res.out or Out(public=f"找不到「{_clean_arg(tok, 20)}」。")
                    ids.append(res.item_id)
                changes["衣服"] = ids
            else:
                return Out(public=f"不能修改「{_clean_arg(key, 20)}」。可以改：日期、場合、天氣、衣服。")
        outfits.modify_outfit(self.store(ctx.actor.user_id), ctx.actor.user_id, outfit_id, changes, reason, self.clock())
        return Out(public=f"{ctx.banner}✅ 已更新穿搭紀錄 #{outfit_id} 的 {'、'.join(changes)}。修改歷史已記錄。")

    def _cmd_delete_outfit(self, ctx: Ctx, args: str) -> Out:
        if not self._first_arg(args):
            return Out(public="用法：`!刪除穿搭 <紀錄ID>`")
        outfit_id = outfits.normalize_outfit_id(self._first_arg(args))
        outfits.get_outfit(self.store(ctx.actor.user_id), outfit_id)  # 不存在就丟 NotFound
        pending = Pending("confirm_delete_outfit", ctx.actor.user_id, {"outfit_id": outfit_id, "actor_name": ctx.actor.name})
        return Out(public=f"⚠️ 要刪除穿搭紀錄 #{outfit_id} 嗎？這是軟刪除，之後可以用 `!復原穿搭 {outfit_id}` 救回；"
                          "刪除後，那次穿搭不再計入使用次數與統計。\n請按「確認刪除」或回覆「確認」。",
                   pending=pending, buttons=[Button("確認刪除", "confirm", "danger"), Button("取消", "cancel")])

    def _cmd_restore_outfit(self, ctx: Ctx, args: str) -> Out:
        if not self._first_arg(args):
            return Out(public="用法：`!復原穿搭 <紀錄ID>`")
        outfit_id = outfits.normalize_outfit_id(self._first_arg(args))
        done = outfits.restore_outfit(self.store(ctx.actor.user_id), ctx.actor.user_id, outfit_id, self.clock())
        return Out(public=f"✅ 已復原穿搭紀錄 #{done['id']}（{done['date']}）。")

    def _cmd_stats(self, ctx: Ctx, args: str) -> Out:
        result = stats.compute(self.store(ctx.actor.user_id), self._today(), _days_arg(args))
        return Out(public=replies.stats_text(result, ctx.banner))

    # ---- 第三個核心功能：AI 推薦穿搭（只從使用者自己衣櫃裡真正存在的衣服推薦）

    def _cmd_recommend(self, ctx: Ctx, args: str) -> Out:
        if not args.strip():
            return Out(public="告訴我場合和需求吧，例如：`!推薦 明天面試`、`!推薦 週末約會 天氣很冷`。")
        if wardrobe.active_count(self.store(ctx.actor.user_id)) == 0:
            return Out(public="你的衣櫃還是空的，先傳幾張衣服照片再來推薦吧！")
        request = recommend.parse_core_request(args, self._today())
        actor, private = ctx.actor, ctx.private
        return Out(ai_job=lambda: self._do_recommend(actor, private, request, []))

    def _cmd_more_outfit(self, ctx: Ctx, args: str) -> Out:
        last = self._last_reco.get(ctx.actor.user_id)
        if not last or last["expires"] < self.clock().timestamp():
            return Out(public="先用 `!推薦 <場合>` 告訴我場合，我才知道要再配哪一套。")
        actor, private = ctx.actor, ctx.private
        return Out(ai_job=lambda: self._do_recommend(actor, private, last["request"], last["shown"]))

    async def _do_recommend(self, actor: Actor, private: bool, request: recommend.RecoRequest,
                            shown: list[frozenset]) -> Out:
        store = self.store(actor.user_id)
        try:
            reco = await recommend.recommend(self.config, store, request, self._today(), shown=shown,
                                             use_weather=self.advanced, get_json=self._get_json, ask=self._ask)
        except recommend.NeedWeather as exc:
            return Out(public=str(exc))
        except AnalysisError as exc:
            log.warning("推薦失敗（使用者 %s）：%s", actor.user_id, exc)
            return Out(public=replies.AI_FAIL_TEXT)
        except StorageError as exc:
            log.error("讀取衣櫃失敗（使用者 %s）：%s", actor.user_id, exc)
            return Out(public=replies.STORAGE_FAIL_TEXT)

        banner = "" if private else self.public_mode.banner(actor.user_id)
        rows_by_id = {r["衣服ID"]: r for r in store.read(WARDROBE)}
        text = replies.reco_text(reco, recommend.describe_outfit(rows_by_id, reco.item_ids) if reco.complete else [],
                                 banner)
        if not reco.complete:
            return Out(public=text)
        new_shown = shown + [frozenset(reco.item_ids)]
        self._last_reco[actor.user_id] = {"request": request, "shown": new_shown,
                                          "expires": self.clock().timestamp() + RECO_TTL_SECONDS}
        pending = Pending("reco", actor.user_id, {
            "ids": reco.item_ids, "request": request, "shown": new_shown, "private": private,
            "actor_name": actor.name, "occasion": request.occasion,
            "weather": reco.weather.summary() if reco.weather else "",
        })
        buttons = [Button("再來一套", "more", "primary")]
        if self.advanced:
            buttons.insert(0, Button("今天穿這套", "wear", "success"))
        return Out(public=text, pending=pending, buttons=buttons)

    def _resolve_reco(self, p: Pending, action: str, actor: Actor) -> Out:
        if action == "more":
            self._close(p)
            request, shown, private = p.data["request"], p.data["shown"], p.data.get("private", False)
            return Out(ai_job=lambda: self._do_recommend(actor, private, request, shown))
        if action == "wear" and self.advanced:
            self._close(p)
            rec = outfits.log_outfit(self.store(actor.user_id), actor.user_id, p.data["ids"], self._today(),
                                     self.clock(), occasion=p.data.get("occasion", ""),
                                     weather=p.data.get("weather", ""), source=outfits.SOURCE_RECO)
            return Out(public=f"✅ 已記錄今天的穿搭（紀錄 #{rec['紀錄ID']}）。")
        return Out(public=replies.EXPIRED)

    # ---------------------------------------------------------------- 分析上傳的照片

    async def analyze_upload(self, actor: Actor, data: bytes, suffix: str, caption: str,
                             taken_at: datetime, *, inflight: bool = False) -> Out:
        try:
            result = await self.analyzer(self.config, data, suffix, caption)
        except AnalysisError as exc:
            log.warning("分析失敗（使用者 %s）：%s", actor.user_id, exc)
            return Out(public=replies.FAIL_TEXT)
        if not result.is_clothing:
            return Out(public=replies.not_clothing(result.what_it_is))
        if not result.identifiable:
            return Out(public=replies.BLURRY)

        pending = Pending("select", actor.user_id, {
            "items": result.items, "image": data, "suffix": suffix, "taken_at": taken_at,
            "added": set(), "photo_rel": None, "inflight": inflight, "actor_name": actor.name,
        })
        buttons = [Button(f"加入 {i}", f"pick:{i}", "primary") for i in range(1, len(result.items) + 1)]
        buttons += [Button("全部加入", "all", "success"), Button("都不要", "none")]
        return Out(public=replies.analysis_listing(result), pending=pending, buttons=buttons)

    # ---------------------------------------------------------------- 按鈕與打字確認

    def resolve_text(self, pending: Pending, text: str, actor_id: int) -> Out | None:
        action = parse_reply(pending, text)
        if action is None:
            return None
        return self.resolve(pending, action, actor_id)

    def resolve(self, pending: Pending, action: str, actor_id: int) -> Out:
        if not access.is_owner(actor_id, pending.owner_id):  # 防護 6：只認發起者本人
            return Out(private=replies.NOT_YOURS, denied=True, keep_pending=True)
        with pending.lock:
            if pending.closed:
                return Out(private=replies.EXPIRED)
            actor = Actor(pending.owner_id, pending.data.get("actor_name", ""))
            try:
                handler = {
                    "select": self._resolve_select, "consent": self._resolve_consent,
                    "public_confirm": self._resolve_public, "confirm_delete": self._resolve_delete,
                    "confirm_delete_photo": self._resolve_delete_photo,
                    "confirm_delete_outfit": self._resolve_delete_outfit, "choose_item": self._resolve_choose,
                    "reco": self._resolve_reco,
                    "delete_all_1": self._resolve_delete_all_1, "delete_all_2": self._resolve_delete_all_2,
                }[pending.kind]
                return handler(pending, action, actor)
            except wardrobe.WardrobeFull as exc:
                if pending.kind == "select":  # 衣櫃滿了：先別丟掉這次的選擇，讓使用者刪掉舊的再按一次（10 分鐘內）
                    return Out(public=str(exc), keep_pending=True, update_buttons=self._select_buttons(pending))
                self._close(pending)
                return Out(public=str(exc))
            except wardrobe.WardrobeError as exc:
                self._close(pending)
                return Out(public=str(exc))
            except StorageError as exc:
                log.error("Excel 寫入失敗（使用者 %s）：%s", actor.user_id, exc)
                return Out(public=replies.STORAGE_FAIL_TEXT, keep_pending=True,
                           update_buttons=self._select_buttons(pending) if pending.kind == "select" else None)

    # ---- 同意隱私說明

    def _resolve_consent(self, p: Pending, action: str, actor: Actor) -> Out:
        self._close(p)
        if action == "agree":
            privacy.record_consent(self.store(actor.user_id), self.clock())
            self._consented.add(actor.user_id)
            return Out(public="✅ 已記錄你的同意，可以開始使用了。直接傳衣服照片，或輸入 `!說明` 看指令。",
                       resume=p.data.get("resume") or None)
        return Out(public="好的，沒有同意就不會儲存任何東西。想使用時再傳照片或輸入指令即可。")

    # ---- 公開提醒

    def _resolve_public(self, p: Pending, action: str, actor: Actor) -> Out:
        self._close(p)
        if action != "confirm":
            return Out(public="好的，不公開。你的資料沒有顯示。")
        self.public_mode.activate(actor.user_id)
        return self.run_command(actor, p.data["command"])

    # ---- 名稱對到好幾件：請使用者選

    def _resolve_choose(self, p: Pending, action: str, actor: Actor) -> Out:
        if action == "cancel":
            self._close(p)
            return Out(public="好的，取消了。")
        if not action.startswith("pick:"):
            return Out(public=replies.EXPIRED)
        try:
            index = int(action[5:]) - 1
            if index < 0:
                raise IndexError
            item_id = p.data["ids"][index]
        except (ValueError, IndexError):
            return Out(public=f"編號不對，請輸入 1 到 {len(p.data['ids'])}。", keep_pending=True, update_buttons=None)
        self._close(p)
        command = p.data["command"].replace(p.data["token"], item_id, 1)  # 把名稱換成編號，重跑原本的指令
        return self.run_command(actor, command, private=p.data.get("private", False))

    # ---- 選擇要加入哪幾件

    def _select_buttons(self, p: Pending) -> list[Button]:
        remaining = [i for i in range(len(p.data["items"])) if i not in p.data["added"]]
        buttons = [Button(f"加入 {i + 1}", f"pick:{i + 1}", "primary") for i in remaining]
        buttons += [Button("全部加入", "all", "success"), Button("都不要", "none")]
        return buttons

    def _resolve_select(self, p: Pending, action: str, actor: Actor) -> Out:
        items: list[ClothingItem] = p.data["items"]
        added: set[int] = p.data["added"]
        remaining = [i for i in range(len(items)) if i not in added]

        if action == "none":
            self._close(p)
            return Out(public="好的，這次沒有加入任何衣服，照片也沒有被儲存。" if not added else "好的，其餘的不加入了。")
        if action == "all":
            chosen = remaining
        elif action.startswith("pick:"):
            try:
                chosen = sorted({int(n) - 1 for n in action[5:].split(",") if n})
            except ValueError:
                chosen = []
            bad = [c for c in chosen if c not in remaining]
            if not chosen or bad:
                return Out(public=f"編號不對。可以選的編號：{', '.join(str(i + 1) for i in remaining)}。",
                           keep_pending=True, update_buttons=self._select_buttons(p))
        else:
            return Out(public=replies.EXPIRED)

        store = self.store(actor.user_id)
        now = self.clock()
        saved_here: Path | None = None
        if p.data["photo_rel"] is None:
            saved_here = photos.save_photo(store.photos_dir, p.data["taken_at"], items[chosen[0]].name, p.data["image"])
            p.data["photo_rel"] = f"photos/{saved_here.name}"
        similar = {i: wardrobe.find_similar(store, items[i]) for i in chosen}
        try:
            created = wardrobe.add_items(store, actor.user_id, [items[i] for i in chosen], p.data["photo_rel"], now)
        except Exception:
            if saved_here is not None:  # 沒加成功就不留下這張照片
                saved_here.unlink(missing_ok=True)
                p.data["photo_rel"] = None
            raise
        added.update(chosen)

        notes = [f"#{c['衣服ID']} {c['名稱']}" for c in created]
        private = "你的新衣服編號：" + "、".join(notes) + f"\n衣櫃目前 {wardrobe.active_count(store)}/{wardrobe.MAX_ITEMS} 件。"
        for i in chosen:
            if similar[i]:
                private += f"\n提醒：你的衣櫃已有類似的「{similar[i][0]['名稱']}」（#{similar[i][0]['衣服ID']}）。"

        if len(added) == len(items):
            self._close(p)
            return Out(public=replies.added_public(len(created)), private=private)
        return Out(public=replies.added_public(len(created)), private=private, keep_pending=True,
                   update_buttons=self._select_buttons(p))

    # ---- 刪除相關

    def _resolve_delete(self, p: Pending, action: str, actor: Actor) -> Out:
        self._close(p)
        if action != "confirm":
            return Out(public="好的，沒有刪除。")
        item_id = p.data["item_id"]
        wardrobe.soft_delete(self.store(actor.user_id), actor.user_id, item_id, self.clock())
        return Out(public=f"🗑️ 已刪除 #{item_id}。要救回來請輸入 `!復原 {item_id}`。")

    def _resolve_delete_photo(self, p: Pending, action: str, actor: Actor) -> Out:
        self._close(p)
        if action != "confirm":
            return Out(public="好的，沒有刪除照片。")
        item_id = p.data["item_id"]
        wardrobe.delete_photo(self.store(actor.user_id), actor.user_id, item_id, self.clock())
        return Out(public=f"🗑️ 已刪除 #{item_id} 的照片，文字資料還在。")

    def _resolve_delete_outfit(self, p: Pending, action: str, actor: Actor) -> Out:
        self._close(p)
        if action != "confirm":
            return Out(public="好的，沒有刪除。")
        outfit_id = p.data["outfit_id"]
        outfits.soft_delete_outfit(self.store(actor.user_id), actor.user_id, outfit_id, self.clock())
        return Out(public=f"🗑️ 已刪除穿搭紀錄 #{outfit_id}。要救回來請輸入 `!復原穿搭 {outfit_id}`。")

    def _resolve_delete_all_1(self, p: Pending, action: str, actor: Actor) -> Out:
        self._close(p)
        if action != "confirm":
            return Out(public="好的，沒有刪除。")
        nxt = Pending("delete_all_2", actor.user_id, {"actor_name": actor.name})
        return Out(public="⚠️ **最後確認**\n請在 60 秒內「回覆這則訊息」，親自打出這幾個字：`確認刪除全部`\n"
                          "（打別的或超過時間，就會取消，什麼都不會刪。）",
                   pending=nxt)

    def _resolve_delete_all_2(self, p: Pending, action: str, actor: Actor) -> Out:
        self._close(p)
        if action != "execute":
            return Out(public="好的，已取消，沒有刪除任何東西。")
        for other in self.registry.of_user(actor.user_id):  # 待選擇的照片也一併丟掉
            with other.lock:
                self._close(other)
        deleted = privacy.delete_all(self.config.data_dir, actor.user_id, actor.user_id)
        self._consented.discard(actor.user_id)
        self.public_mode.end(actor.user_id)
        return Out(public="✅ 已永久刪除你的全部資料。" if deleted else "你目前沒有任何資料可以刪除。")
