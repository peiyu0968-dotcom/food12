"""Discord 端：只聽指定頻道、⏳ 反應、逐張排隊、按鈕與打字替代、回覆。

這一層只負責「Discord 物件 ↔ 引擎」的翻譯；實際的判斷與資料處理都在 engine.py。
保留字 `ready`、`/done`（老師的評分流程）一律略過；bot 不會主動在頻道發任何啟動訊息。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import discord

from . import limits, photos, replies, timeutil
from .access import is_owner
from .config import Config
from .engine import Actor, Button, Engine, Out
from .reserved import is_reserved

log = logging.getLogger(__name__)

WAIT_EMOJI = "⏳"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
SWEEP_SECONDS = 30
STYLES = {
    "primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger,
}


@dataclass
class Job:
    message: discord.Message
    attachment: discord.Attachment
    actor: Actor
    last_of_message: bool  # 同一則訊息的最後一張處理完，才拿掉 ⏳


@dataclass
class AiWork:
    """需要 claude -p 的工作（例如推薦穿搭）。和照片分析共用同一個隊伍。"""
    actor: Actor
    run: Callable[[], Awaitable[Out]]
    deliver: Callable[[Out], Awaitable[None]]
    message: discord.Message | None = None  # 在這則使用者訊息上加 ⏳


def _looks_like_image(attachment: discord.Attachment) -> bool:
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True
    return attachment.filename.lower().endswith(IMAGE_EXTENSIONS)


class ActionButton(discord.ui.Button):
    def __init__(self, bot: "StyleMateBot", spec: Button) -> None:
        super().__init__(label=spec.label, style=STYLES.get(spec.style, discord.ButtonStyle.secondary))
        self.bot, self.action = bot, spec.action

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.on_button(interaction, self.action)


def make_view(bot: "StyleMateBot", buttons: list[Button]) -> discord.ui.View | None:
    if not buttons:
        return None
    view = discord.ui.View(timeout=None)  # 逾時由 bot 自己的清理程序處理
    for spec in buttons:
        view.add_item(ActionButton(bot, spec))
    return view


class StyleMateBot(discord.Client):
    def __init__(self, config: Config, engine: Engine | None = None) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # 要在 Developer Portal 也打開 Message Content Intent
        super().__init__(intents=intents)
        self.config = config
        self.engine = engine or Engine(config)
        self.queue: asyncio.Queue[Job | AiWork] = asyncio.Queue()
        self._busy = False

    async def setup_hook(self) -> None:
        self._worker = asyncio.create_task(self._worker_loop())
        self._janitor = asyncio.create_task(self._janitor_loop())

    async def on_ready(self) -> None:
        log.info("已登入為 %s，只聽頻道 %s", self.user, self.config.channel_id)  # 只寫日誌，不在頻道發訊息
        if self.get_channel(self.config.channel_id) is None:
            log.warning("⚠️ 看不到 CHANNEL_ID=%s 這個頻道：請確認編號正確，且 bot 已被邀進該 Server", self.config.channel_id)

    # ---------------------------------------------------------------- 收訊息

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:  # 其他 bot（含自己）與私訊：這個階段不處理
            return
        if message.channel.id != self.config.channel_id:
            return
        content = message.content or ""
        if is_reserved(content):  # ready、/done：老師的評分流程，完全不碰
            return
        actor = Actor(message.author.id, message.author.display_name)

        ref_id = message.reference.message_id if message.reference else None
        pending = self.engine.registry.get(ref_id)
        if pending is not None and content.strip():
            out = await asyncio.to_thread(self.engine.resolve_text, pending, content, actor.user_id)
            if out is not None:
                if not out.denied:  # 不是發起者本人：靜靜略過
                    await self._deliver(message, actor, out, pending_msg_id=pending.message_id)
                return

        images = [a for a in message.attachments if _looks_like_image(a)]
        if images:
            await self._on_images(message, actor, images)
        elif content.startswith(("!", "！")):
            out = await asyncio.to_thread(self.engine.run_command, actor, content)
            await self._deliver(message, actor, out)
        elif self.user is not None and self.user in message.mentions:
            await self._deliver(message, actor, Out(public=replies.MENTION_HELP))

    async def _on_images(self, message: discord.Message, actor: Actor, images: list[discord.Attachment]) -> None:
        if not await asyncio.to_thread(self.engine.has_consent, actor.user_id):
            out = self.engine.consent_prompt(actor, resume=[(message, a) for a in images])
            await self._deliver(message, actor, out)
            return
        await self._enqueue(message, actor, images)

    async def _enqueue(self, message: discord.Message, actor: Actor, images: list[discord.Attachment]) -> None:
        limiter, accepted, refusal = self.engine.limiter, [], None
        for attachment in images:
            reason = (
                limits.check_attachment(attachment.filename, attachment.content_type, attachment.size)
                or limits.check_inflight(limiter, actor.user_id)
                or limits.check_rate(limiter, actor.user_id, "upload")
            )
            if reason:
                refusal = reason
                break
            limiter.acquire(actor.user_id)
            accepted.append(attachment)
        if refusal:
            await self._deliver(message, actor, Out(public=refusal))
        if not accepted:
            return

        ahead = self.queue.qsize() + (1 if self._busy else 0)
        try:
            await message.add_reaction(WAIT_EMOJI)
        except discord.HTTPException as exc:
            log.warning("加 ⏳ 失敗（bot 需要「Add Reactions」權限）：%s", exc)
        if ahead > 0:
            await self._deliver(message, actor, Out(public=f"⏳ 處理中，前面還有 {ahead} 件"))
        for index, attachment in enumerate(accepted):
            await self.queue.put(Job(message, attachment, actor, index == len(accepted) - 1))

    # ---------------------------------------------------------------- 逐張排隊處理

    async def _worker_loop(self) -> None:
        """照片分析與 AI 推薦共用同一個隊伍，一次只處理一件（所以同時只會有一個 claude -p）。"""
        while True:
            job = await self.queue.get()
            self._busy = True
            try:
                if isinstance(job, AiWork):
                    await self._run_ai(job)
                else:
                    await self._run_photo(job)
            finally:
                self._busy = False
                self.queue.task_done()

    async def _run_photo(self, job: Job) -> None:
        try:
            await self._process(job)
        except Exception:
            log.exception("處理照片時發生未預期的錯誤")
            self.engine.limiter.release(job.actor.user_id)
            await self._deliver(job.message, job.actor, Out(public=replies.FAIL_TEXT))
        finally:
            if job.last_of_message:
                await self._remove_wait(job.message)

    async def _remove_wait(self, message: discord.Message | None) -> None:
        if message is None:
            return
        try:
            await message.remove_reaction(WAIT_EMOJI, self.user)
        except discord.HTTPException:
            pass

    async def _run_ai(self, work: "AiWork") -> None:
        try:
            out = await work.run()
            while out.ai_job is not None:  # 一個請求接著要再做一次 AI 工作：在同一個名額裡接著做
                out = await out.ai_job()
        except Exception:
            log.exception("AI 工作發生未預期的錯誤")
            out = Out(public=replies.AI_FAIL_TEXT)
        finally:
            self.engine.limiter.release(work.actor.user_id)
            await self._remove_wait(work.message)
        await work.deliver(out)

    async def _enqueue_ai(self, actor: Actor, run, deliver, *, message: discord.Message | None = None,
                          refuse=None) -> None:
        """把需要 claude -p 的工作排進隊伍。先檢查同時進行上限與每分鐘上限（防護 5）。"""
        limiter = self.engine.limiter
        reason = limits.check_inflight(limiter, actor.user_id) or limits.check_rate(limiter, actor.user_id, "ai")
        if reason:
            await (refuse or (lambda text: deliver(Out(public=text))))(reason)
            return
        limiter.acquire(actor.user_id)
        ahead = self.queue.qsize() + (1 if self._busy else 0)
        if message is not None:
            try:
                await message.add_reaction(WAIT_EMOJI)
            except discord.HTTPException as exc:
                log.warning("加 ⏳ 失敗（bot 需要「Add Reactions」權限）：%s", exc)
            if ahead > 0:
                await deliver(Out(public=f"⏳ 處理中，前面還有 {ahead} 件"))
        await self.queue.put(AiWork(actor, run, deliver, message))

    async def _process(self, job: Job) -> None:
        data = await job.attachment.read()
        suffix = photos.detect_image_suffix(data)
        if suffix is None:
            self.engine.limiter.release(job.actor.user_id)
            await self._deliver(job.message, job.actor, Out(public="讀不出這是圖片，請再傳一次。"))
            return
        taken_at = timeutil.to_taipei(job.message.created_at)
        out = await self.engine.analyze_upload(
            job.actor, data, suffix, job.message.content or "", taken_at, inflight=True
        )
        if out.pending is None:  # 沒有等著使用者選擇的東西：歸還名額
            self.engine.limiter.release(job.actor.user_id)
        await self._deliver(job.message, job.actor, out)

    # ---------------------------------------------------------------- 回覆

    async def _deliver(self, message: discord.Message, actor: Actor, out: Out, *, pending_msg_id: int | None = None) -> None:
        """打字路徑：用「回覆那則訊息」的方式回在頻道裡；私人內容改傳私訊。"""
        if pending_msg_id is not None:
            await self._refresh_original(message.channel, pending_msg_id, out)
        if out.public:
            kwargs = {"allowed_mentions": replies.ALLOWED_MENTIONS} if replies.ALLOWED_MENTIONS else {}
            if out.file:
                kwargs["file"] = discord.File(out.file)
            view = make_view(self, out.buttons)
            if view:
                kwargs["view"] = view
            try:
                sent = await message.reply(replies.clip(out.public), mention_author=False, **kwargs)
            except discord.HTTPException:
                log.exception("回覆失敗")
                return
            self._register(out, sent.id)
        if out.private:
            try:
                await message.author.send(replies.clip(out.private))
            except discord.HTTPException:
                await self._deliver(message, actor, Out(public="（我沒辦法私訊你。想看編號請用 `!衣櫃`。）"))
        if out.resume:
            await self._resume_jobs(out.resume)
        if out.ai_job is not None:
            async def deliver(result: Out) -> None:
                await self._deliver(message, actor, result)
            await self._enqueue_ai(actor, out.ai_job, deliver, message=message)

    def _register(self, out: Out, message_id: int) -> None:
        if out.pending is not None:
            out.pending.message_text = out.public or ""
            self.engine.registry.register(out.pending, message_id)

    async def _refresh_original(self, channel, message_id: int, out: Out) -> None:
        """換掉（或移除）原本 bot 訊息上的按鈕。"""
        if out.denied:
            return
        try:
            partial = channel.get_partial_message(message_id)
            new_view = make_view(self, out.update_buttons or []) if out.keep_pending else None
            await partial.edit(view=new_view)
        except discord.HTTPException:
            pass

    async def _resume_jobs(self, resume: list) -> None:
        by_message: dict[int, tuple[discord.Message, Actor, list]] = {}
        for message, attachment in resume:
            actor = Actor(message.author.id, message.author.display_name)
            by_message.setdefault(message.id, (message, actor, []))[2].append(attachment)
        for message, actor, attachments in by_message.values():
            await self._enqueue(message, actor, attachments)

    # ---------------------------------------------------------------- 按鈕

    async def on_button(self, interaction: discord.Interaction, action: str) -> None:
        pending = self.engine.registry.get(interaction.message.id if interaction.message else None)
        if pending is None:
            await interaction.response.send_message(replies.EXPIRED, ephemeral=True)
            return
        if not is_owner(interaction.user.id, pending.owner_id):  # 只有發起者按得動
            await interaction.response.send_message(replies.NOT_YOURS, ephemeral=True)
            return
        await interaction.response.edit_message(view=None)  # 先確認收到，處理期間先收起按鈕
        out = await asyncio.to_thread(self.engine.resolve, pending, action, interaction.user.id)

        if out.keep_pending and out.update_buttons is not None:
            try:
                await interaction.edit_original_response(view=make_view(self, out.update_buttons))
            except discord.HTTPException:
                pass
        await self._send_followup(interaction, out)
        if out.ai_job is not None:  # 例如按「再來一套」：排進隊伍，結果會用新訊息送出
            actor = Actor(interaction.user.id, interaction.user.display_name)

            async def deliver(result: Out) -> None:
                await self._send_followup(interaction, result)

            async def refuse(text: str) -> None:
                await interaction.followup.send(text, ephemeral=True)

            await self._enqueue_ai(actor, out.ai_job, deliver, refuse=refuse)
            if self.queue.qsize() or self._busy:
                await interaction.followup.send("⏳ 處理中，請稍等…", ephemeral=True)

    async def _send_followup(self, interaction: discord.Interaction, out: Out) -> None:
        """按鈕路徑：公開訊息用新訊息送出；個人細節用「只有你看得到」的訊息。"""
        if out.public:
            kwargs = {"allowed_mentions": replies.ALLOWED_MENTIONS} if replies.ALLOWED_MENTIONS else {}
            if out.file:
                kwargs["file"] = discord.File(out.file)
            view = make_view(self, out.buttons)
            if view:
                kwargs["view"] = view
            try:
                sent = await interaction.followup.send(replies.clip(out.public), wait=True, **kwargs)
            except discord.HTTPException:
                log.exception("回覆失敗")
                return
            self._register(out, sent.id)
        if out.private:
            try:
                await interaction.followup.send(replies.clip(out.private), ephemeral=True)  # 只有本人看得到
            except discord.HTTPException:
                log.exception("私人回覆失敗")
        if out.resume:
            await self._resume_jobs(out.resume)

    # ---------------------------------------------------------------- 逾時清理

    async def _janitor_loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            try:
                expired = await asyncio.to_thread(self.engine.sweep)
                channel = self.get_channel(self.config.channel_id)
                for p in expired:
                    if channel is not None and p.message_id is not None:
                        try:
                            tail = "⌛ 按鈕已失效（想再配一套請輸入 `!再來一套`）。" if p.kind == "reco" else "⌛ 已逾時取消，什麼都沒有儲存。"
                            note = replies.clip((p.message_text + "\n" if p.message_text else "") + tail)
                            await channel.get_partial_message(p.message_id).edit(content=note, view=None)
                        except discord.HTTPException:
                            pass
            except Exception:
                log.exception("清理逾時的待處理動作時出錯")
