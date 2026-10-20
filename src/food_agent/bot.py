"""Discord 端：只聽指定頻道、加 ⏳、排隊一張一張處理、回覆。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import discord

from .analyzer import AnalysisError, analyze_image
from .config import Config
from .photos import detect_image_suffix, save_photo
from .replies import FAIL_TEXT, format_reply
from .storage import StorageError, append_record

log = logging.getLogger(__name__)

WAIT_EMOJI = "⏳"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@dataclass
class Job:
    message: discord.Message
    attachment: discord.Attachment
    last_of_message: bool  # 同一則訊息的最後一張處理完，才拿掉 ⏳


def _looks_like_image(attachment: discord.Attachment) -> bool:
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True
    return attachment.filename.lower().endswith(IMAGE_EXTENSIONS)


class FoodBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # 要在 Developer Portal 也打開 Message Content Intent
        super().__init__(intents=intents)
        self.config = config
        self.queue: asyncio.Queue[Job] = asyncio.Queue()

    async def setup_hook(self) -> None:
        self._worker = asyncio.create_task(self._worker_loop())

    async def on_ready(self) -> None:
        log.info("已登入為 %s，只聽頻道 %s", self.user, self.config.channel_id)
        if self.get_channel(self.config.channel_id) is None:
            log.warning("⚠️ 看不到 CHANNEL_ID=%s 這個頻道：請確認編號正確，且 bot 已被邀進該 Server", self.config.channel_id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:  # 包含 bot 自己
            return
        if message.channel.id != self.config.channel_id:
            return
        images = [a for a in message.attachments if _looks_like_image(a)]
        if not images:
            return

        try:
            await message.add_reaction(WAIT_EMOJI)
        except discord.HTTPException as exc:
            log.warning("加 ⏳ 失敗（bot 需要「Add Reactions」權限）：%s", exc)

        for index, attachment in enumerate(images):
            await self.queue.put(Job(message, attachment, last_of_message=index == len(images) - 1))

    # 一次只處理一個，所以同時只會有一個 claude -p
    async def _worker_loop(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._handle(job)
            except Exception:
                log.exception("處理照片時發生未預期的錯誤")
                await self._safe_reply(job.message, FAIL_TEXT)
            finally:
                if job.last_of_message:
                    try:
                        await job.message.remove_reaction(WAIT_EMOJI, self.user)
                    except discord.HTTPException:
                        pass
                self.queue.task_done()

    async def _safe_reply(self, message: discord.Message, text: str) -> None:
        try:
            await message.reply(text, mention_author=False)
        except discord.HTTPException:
            log.exception("回覆失敗")

    async def _handle(self, job: Job) -> None:
        message, attachment = job.message, job.attachment

        if attachment.size > MAX_IMAGE_BYTES:
            await self._safe_reply(message, f"{FAIL_TEXT}（圖片超過 10 MB）")
            return
        data = await attachment.read()
        suffix = detect_image_suffix(data)
        if suffix is None:
            await self._safe_reply(message, f"{FAIL_TEXT}（讀不出這是圖片）")
            return

        try:
            result = await analyze_image(self.config, data, suffix, caption=message.content or "")
        except AnalysisError as exc:
            log.warning("分析失敗：%s", exc)
            await self._safe_reply(message, FAIL_TEXT)
            return

        taken_at = message.created_at.astimezone().replace(tzinfo=None)  # 我電腦的本地時間
        food_desc = result.name if result.is_food else "不是食物"
        ingredients = "、".join(result.ingredients) if result.is_food else result.name

        photo_path = await asyncio.to_thread(
            save_photo, self.config.photos_dir, taken_at, food_desc, data
        )

        recorded = True
        try:
            daily_total = await asyncio.to_thread(
                append_record,
                self.config.xlsx_path,
                taken_at=taken_at,
                uploader=message.author.display_name,
                food_desc=food_desc,
                ingredients=ingredients,
                weight_g=result.weight_g,
                calories=result.calories_kcal,
                photo_path=str(photo_path),
            )
        except StorageError as exc:
            log.error("寫入 Excel 失敗：%s", exc)
            recorded = False
            daily_total = None

        await self._safe_reply(message, format_reply(result, daily_total, recorded))
