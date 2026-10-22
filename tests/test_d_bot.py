"""第四批：Discord 層（用假的訊息物件，不連線）。

涵蓋：只聽指定頻道、忽略其他 bot、保留字 ready 與 /done 完全不碰、⏳ 反應、逐張排隊、
首次同意、按鈕與打字替代、只有發起者能操作、頻率限制。
"""
import asyncio
from datetime import datetime, timezone

import pytest

from stylemate_ai import replies
from stylemate_ai.bot import StyleMateBot
from stylemate_ai.config import Config
from stylemate_ai.engine import Engine
from stylemate_ai.schemas import AnalysisResult, ClothingItem

CHANNEL = 555
JPG = b"\xff\xd8\xff\xe0" + b"0" * 30


def clothing(name="白色棉質襯衫"):
    return ClothingItem(name, "top", ["白"], "棉", "簡約", "素面", ["春", "夏"], 2, 3, "high")


class FakeAnalyzer:
    def __init__(self):
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def __call__(self, config, data, suffix, caption):
        self.calls += 1
        if self.gate:
            await self.gate.wait()
        return AnalysisResult(True, True, "", [clothing()], "白襯衫百搭。")


class Sent:
    _next = 10_000

    def __init__(self, text, kwargs):
        Sent._next += 1
        self.id, self.text, self.kwargs = Sent._next, text, kwargs


class FakeAuthor:
    def __init__(self, uid, name="Alice", bot=False):
        self.id, self.display_name, self.bot, self.dms = uid, name, bot, []

    async def send(self, text):
        self.dms.append(text)


class FakeAttachment:
    def __init__(self, name="a.jpg", ctype="image/jpeg", size=1000, data=JPG):
        self.filename, self.content_type, self.size, self._data = name, ctype, size, data

    async def read(self):
        return self._data


class FakeRef:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeChannel:
    def __init__(self, cid=CHANNEL):
        self.id, self.edits = cid, []

    def get_partial_message(self, mid):
        outer = self

        class P:
            async def edit(self, **kw):
                outer.edits.append((mid, kw))
        return P()


class FakeMessage:
    _ids = 0

    def __init__(self, author, content="", attachments=(), reference=None, channel=None, guild=True):
        FakeMessage._ids += 1
        self.id = FakeMessage._ids
        self.author, self.content = author, content
        self.attachments, self.mentions = list(attachments), []
        self.reference = FakeRef(reference) if reference else None
        self.channel = channel or FakeChannel()
        self.guild = object() if guild else None
        self.created_at = datetime(2026, 10, 23, 6, 5, 33, tzinfo=timezone.utc)  # 台灣時間 14:05:33
        self.sent: list[Sent] = []
        self.reactions: list[str] = []
        self.removed: list[str] = []

    async def reply(self, text, **kwargs):
        s = Sent(text, kwargs)
        self.sent.append(s)
        return s

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def remove_reaction(self, emoji, user):
        self.removed.append(emoji)


@pytest.fixture
def cfg(tmp_path):
    return Config("t", CHANNEL, "haiku", (tmp_path / "data").resolve())


@pytest.fixture
def analyzer():
    return FakeAnalyzer()


@pytest.fixture
def bot(cfg, analyzer):
    engine = Engine(cfg, analyzer=analyzer, retries=2, delay=0)
    return StyleMateBot(cfg, engine)


async def drain(bot):
    """跑一次工作者迴圈，處理完佇列裡的所有工作。"""
    task = asyncio.create_task(bot._worker_loop())
    await asyncio.wait_for(bot.queue.join(), timeout=5)
    task.cancel()


async def onboard(bot, author):
    """讓某位使用者先完成隱私同意。"""
    m = FakeMessage(author, "!說明")
    bot.engine._consented.add(author.id)
    from stylemate_ai import privacy
    privacy.record_consent(bot.engine.store(author.id), datetime(2026, 10, 23, 14, 0, 0))


# ------------------------------------------------------------------ 只聽該聽的

async def test_ignores_other_channel_dm_and_other_bots(bot):
    alice = FakeAuthor(1)
    for msg in (
        FakeMessage(alice, "!說明", channel=FakeChannel(999)),
        FakeMessage(alice, "!說明", guild=False),
        FakeMessage(FakeAuthor(2, bot=True), "!說明"),
    ):
        await bot.on_message(msg)
        assert msg.sent == []


async def test_help_in_right_channel_with_mentions_disabled(bot):
    msg = FakeMessage(FakeAuthor(1), "!說明")
    await bot.on_message(msg)
    assert msg.sent[0].text == replies.HELP
    assert msg.sent[0].kwargs["allowed_mentions"] is replies.ALLOWED_MENTIONS
    assert msg.sent[0].kwargs["mention_author"] is False


async def test_plain_chat_is_ignored(bot):
    for text in ("今天好冷", "大家午餐吃什麼", ""):
        msg = FakeMessage(FakeAuthor(1), text)
        await bot.on_message(msg)
        assert msg.sent == [] and msg.reactions == []


# ------------------------------------------------------------------ ready 與 /done：完全不干擾

@pytest.mark.parametrize("text", ["ready", "Ready", "/done", "/done now", "ready!"])
async def test_reserved_words_get_no_reply_no_reaction_no_job(bot, text):
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    for attachments in ((), (FakeAttachment(),)):  # 就算同一則訊息帶圖片也不碰
        msg = FakeMessage(alice, text, attachments)
        await bot.on_message(msg)
        assert msg.sent == [] and msg.reactions == [] and alice.dms == []
    assert bot.queue.qsize() == 0 and bot.engine.limiter.inflight(1) == 0


async def test_reserved_word_replying_to_a_bot_prompt_does_not_resolve_it(bot):
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    out = bot.engine.run_command(__import__("stylemate_ai.engine", fromlist=["Actor"]).Actor(1, "A"), "!刪除全部資料")
    prompt = FakeMessage(alice, "!刪除全部資料")
    await bot.on_message(prompt)
    bot_msg_id = prompt.sent[0].id
    for text in ("ready", "/done"):
        msg = FakeMessage(alice, text, reference=bot_msg_id)
        await bot.on_message(msg)
        assert msg.sent == []
    assert bot.engine.registry.get(bot_msg_id) is not None  # 待確認的動作原封不動


async def test_bot_never_posts_grader_words_in_any_flow(bot):
    alice = FakeAuthor(1)
    msgs = [FakeMessage(alice, "!說明"), FakeMessage(alice, "!衣櫃"), FakeMessage(alice, "!亂打")]
    for m in msgs:
        await bot.on_message(m)
    for m in msgs:
        for s in m.sent:
            assert s.text.strip().lower() != "ready" and not s.text.strip().lower().startswith("/done")


# ------------------------------------------------------------------ 首次同意 → 上傳 → 選擇

async def test_first_upload_asks_consent_then_resumes_after_typed_agree(bot, cfg, analyzer):
    alice = FakeAuthor(1)
    up = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(up)
    assert "第一次使用" in up.sent[0].text and analyzer.calls == 0 and not cfg.users_dir.exists()
    assert up.reactions == [] and bot.queue.qsize() == 0  # 同意前：不排隊、不處理

    agree = FakeMessage(alice, "我同意", reference=up.sent[0].id)
    await bot.on_message(agree)
    assert "已記錄你的同意" in agree.sent[0].text and (cfg.users_dir / "1" / "wardrobe.xlsx").exists()
    assert up.reactions == ["⏳"] and bot.queue.qsize() == 1  # 同意後自動接著處理那張照片

    await drain(bot)
    assert analyzer.calls == 1 and up.removed == ["⏳"]
    listing = up.sent[-1]
    assert "我看到 1 件" in listing.text and listing.kwargs.get("view") is not None
    assert not (cfg.users_dir / "1" / "photos").exists()  # 還沒選：沒存照片

    pick = FakeMessage(alice, "全部", reference=listing.id)
    await bot.on_message(pick)
    assert pick.sent[0].text == "✅ 已加入 1 件"
    assert any("#" in dm and "1/200" in dm for dm in alice.dms)  # 編號只私訊
    assert len(list((cfg.users_dir / "1" / "photos").iterdir())) == 1


async def test_other_user_cannot_answer_my_prompt_by_typing(bot, cfg):
    alice, bob = FakeAuthor(1), FakeAuthor(2, "Bob")
    await onboard(bot, alice)
    await onboard(bot, bob)
    up = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(up)
    await drain(bot)
    listing = up.sent[-1]
    intruder = FakeMessage(bob, "全部", reference=listing.id)
    await bot.on_message(intruder)
    assert intruder.sent == [] and not (cfg.users_dir / "1" / "photos").exists()
    assert not (cfg.users_dir / "2" / "photos").exists()
    assert bot.engine.registry.get(listing.id) is not None


async def test_typed_reply_that_is_not_an_answer_falls_through_to_commands(bot):
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    up = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(up)
    await drain(bot)
    msg = FakeMessage(alice, "!說明", reference=up.sent[-1].id)  # 回覆 bot 訊息，但說的是別的指令
    await bot.on_message(msg)
    assert msg.sent[0].text == replies.HELP


# ------------------------------------------------------------------ 逐張排隊與限制

async def test_queue_is_one_at_a_time_with_position_message(bot, analyzer):
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    analyzer.gate = asyncio.Event()
    first = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(first)
    worker = asyncio.create_task(bot._worker_loop())
    await asyncio.sleep(0.05)  # 第一張進入處理中（卡在 gate）
    second = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(second)
    assert any("前面還有 1 件" in s.text for s in second.sent) and second.reactions == ["⏳"]
    assert not any("前面還有" in s.text for s in first.sent)  # 第一張前面沒人
    assert analyzer.calls == 1  # 同一時間只有一個分析在跑
    analyzer.gate.set()
    await asyncio.wait_for(bot.queue.join(), timeout=5)
    worker.cancel()
    assert analyzer.calls == 2 and first.removed == ["⏳"] and second.removed == ["⏳"]


async def test_three_images_in_one_message_each_get_a_reply(bot, analyzer):
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    msg = FakeMessage(alice, "", [FakeAttachment() for _ in range(3)])
    await bot.on_message(msg)
    assert msg.reactions == ["⏳"] and bot.queue.qsize() == 3
    await drain(bot)
    assert analyzer.calls == 3 and sum("我看到" in s.text for s in msg.sent) == 3
    assert msg.removed == ["⏳"]  # 最後一張處理完才拿掉


async def test_protection5_blocks_oversized_wrong_type_and_flooding(bot, analyzer):
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    big = FakeMessage(alice, "", [FakeAttachment(size=20 * 1024 * 1024)])
    await bot.on_message(big)
    assert "超過 10 MB" in big.sent[0].text and big.reactions == [] and bot.queue.qsize() == 0
    pdf = FakeMessage(alice, "", [FakeAttachment("a.pdf", "application/pdf")])
    await bot.on_message(pdf)  # 不是圖片：根本不是觸發條件
    assert pdf.sent == []

    msgs = []
    for _ in range(4):
        m = FakeMessage(alice, "", [FakeAttachment()])
        await bot.on_message(m)
        msgs.append(m)
    # 同時進行中最多 3 件：第 4 張被擋
    assert "還有 3 件在處理或等你選擇" in msgs[3].sent[0].text and bot.queue.qsize() == 3


async def test_rate_limit_message_and_other_users_unaffected(bot):
    t = {"now": 0.0}
    bot.engine.limiter._clock = lambda: t["now"]
    alice, bob = FakeAuthor(1), FakeAuthor(2, "Bob")
    await onboard(bot, alice)
    await onboard(bot, bob)
    for _ in range(3):
        await bot.on_message(FakeMessage(alice, "", [FakeAttachment()]))
        bot.engine.limiter.release(1)  # 假裝前面的都處理完了，只測頻率
    over = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(over)
    assert "你傳太快了" in over.sent[0].text
    ok = FakeMessage(bob, "", [FakeAttachment()])
    await bot.on_message(ok)
    assert ok.reactions == ["⏳"]


async def test_non_image_bytes_are_refused_and_slot_released(bot, analyzer):
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    fake = FakeMessage(alice, "", [FakeAttachment("a.jpg", "image/jpeg", 100, data=b"MZ not an image")])
    await bot.on_message(fake)
    await drain(bot)
    assert analyzer.calls == 0 and "讀不出這是圖片" in fake.sent[-1].text
    assert bot.engine.limiter.inflight(1) == 0


# ------------------------------------------------------------------ 按鈕

class FakeResponse:
    def __init__(self):
        self.sent, self.edited = [], []

    async def send_message(self, text, ephemeral=False):
        self.sent.append((text, ephemeral))

    async def edit_message(self, **kw):
        self.edited.append(kw)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, text, **kw):
        s = Sent(text, kw)
        self.sent.append(s)
        return s


class FakeInteraction:
    def __init__(self, user_id, message_id):
        self.user = FakeAuthor(user_id)
        self.message = type("M", (), {"id": message_id})()
        self.response, self.followup, self.edits = FakeResponse(), FakeFollowup(), []

    async def edit_original_response(self, **kw):
        self.edits.append(kw)


async def test_button_owner_vs_other_and_partial_selection(bot, cfg):
    two = FakeAnalyzer()

    async def two_items(config, data, suffix, caption):
        return AnalysisResult(True, True, "", [clothing("白襯衫"), clothing("牛仔褲")], "")

    bot.engine.analyzer = two_items
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    up = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(up)
    await drain(bot)
    listing = up.sent[-1]

    stranger = FakeInteraction(2, listing.id)
    await bot.on_button(stranger, "all")
    assert stranger.response.sent == [("這不是你的操作。", True)]  # 只有他自己看得到的提示
    assert stranger.followup.sent == [] and not (cfg.users_dir / "1" / "photos").exists()

    owner = FakeInteraction(1, listing.id)
    await bot.on_button(owner, "pick:2")
    assert owner.response.edited == [{"view": None}]  # 先收起按鈕表示收到
    assert [s.text for s in owner.followup.sent if not s.kwargs.get("ephemeral")] == ["✅ 已加入 1 件"]
    eph = [s for s in owner.followup.sent if s.kwargs.get("ephemeral")]
    assert eph and "#" in eph[0].text  # 編號只有本人看得到
    assert owner.edits and owner.edits[0]["view"] is not None  # 還剩一件：換上剩下的按鈕

    again = FakeInteraction(1, listing.id)
    await bot.on_button(again, "pick:2")  # 已經加過的
    assert "編號不對" in [s.text for s in again.followup.sent][0]
    last = FakeInteraction(1, listing.id)
    await bot.on_button(last, "all")
    assert [s.text for s in last.followup.sent if not s.kwargs.get("ephemeral")] == ["✅ 已加入 1 件"]
    gone = FakeInteraction(1, listing.id)
    await bot.on_button(gone, "all")
    assert gone.response.sent == [(replies.EXPIRED, True)]  # 已完成：按鈕失效
    assert len(bot.engine.store(1).read("衣櫃")) == 2


async def test_expired_prompt_is_cleaned_up_by_janitor_logic(bot, cfg, monkeypatch):
    from stylemate_ai import pending as pending_mod
    t = {"now": 0.0}
    monkeypatch.setattr(pending_mod, "clock", lambda: t["now"])
    alice = FakeAuthor(1)
    await onboard(bot, alice)
    up = FakeMessage(alice, "", [FakeAttachment()])
    await bot.on_message(up)
    await drain(bot)
    t["now"] = 601.0
    expired = bot.engine.sweep()
    assert len(expired) == 1 and expired[0].message_id == up.sent[-1].id
    assert bot.engine.limiter.inflight(1) == 0 and not (cfg.users_dir / "1" / "photos").exists()
