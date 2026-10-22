"""第三批：頻率限制（防護 5）、公開展示模式、保留字、回覆文字（防護 7）、引擎流程（同意、辨識、選擇、衣櫃、修改、刪除、刪除全部）。"""
from datetime import datetime

import pytest

from stylemate_ai import pending as pending_mod
from stylemate_ai import limits, privacy, replies, wardrobe
from stylemate_ai.config import Config
from stylemate_ai.engine import Actor, Engine
from stylemate_ai.pending import Pending, parse_reply
from stylemate_ai.reserved import is_reserved
from stylemate_ai.schemas import AnalysisError, AnalysisResult, ClothingItem
from stylemate_ai.visibility import PublicMode

NOW = datetime(2026, 10, 23, 14, 5, 33)
JPG = b"\xff\xd8\xff\xe0" + b"0" * 30
ALICE, BOB = Actor(1001, "Alice"), Actor(2002, "Bob")


def item(name="白色棉質襯衫", **kw):
    base = dict(name=name, category="top", colors=["白"], material="棉", style="簡約", pattern="素面",
                seasons=["春", "夏"], warmth=2, formality=3, confidence="high")
    base.update(kw)
    return ClothingItem(**base)


def clothes(*items):
    return AnalysisResult(True, True, "", list(items), "白襯衫百搭。")


class FakeAnalyzer:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def __call__(self, config, data, suffix, caption):
        self.calls.append((suffix, caption))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def cfg(tmp_path):
    return Config("t", 1, "haiku", (tmp_path / "data").resolve())


def make_engine(cfg, analyzer=None, **kw):
    return Engine(cfg, analyzer=analyzer or FakeAnalyzer(clothes(item())), clock=lambda: NOW, retries=2, delay=0, **kw)


def register(engine, out, msg_id):
    """模擬 bot：把 Out 裡的待處理動作登記到某則 bot 訊息上。"""
    if out.pending:
        engine.registry.register(out.pending, msg_id)
    return out.pending


def consent(engine, actor, msg_id=1):
    out = engine.consent_prompt(actor)
    p = register(engine, out, msg_id)
    res = engine.resolve(p, "agree", actor.user_id)
    assert "已記錄你的同意" in res.public


async def upload(engine, actor, msg_id=100, result=None):
    out = await engine.analyze_upload(actor, JPG, ".jpg", "", NOW)
    register(engine, out, msg_id)
    return out


# ------------------------------------------------------------------ 防護 5：限制

def test_protection5_rate_limit_and_window():
    t = {"now": 0.0}
    lim = limits.Limiter(clock=lambda: t["now"])
    assert [limits.check_rate(lim, 1, "upload") for _ in range(3)] == [None, None, None]
    msg = limits.check_rate(lim, 1, "upload")
    assert msg and "每分鐘最多 3" in msg and "60 秒" in msg
    assert limits.check_rate(lim, 2, "upload") is None  # 別人不受影響
    t["now"] = 61.0
    assert limits.check_rate(lim, 1, "upload") is None  # 一分鐘後恢復
    assert [limits.check_rate(lim, 3, "ai") for _ in range(5)] == [None] * 5
    assert limits.check_rate(lim, 3, "ai")


def test_protection5_inflight_cap():
    lim = limits.Limiter()
    for _ in range(3):
        assert limits.check_inflight(lim, 1) is None
        lim.acquire(1)
    assert "還有 3 件" in limits.check_inflight(lim, 1)
    assert limits.check_inflight(lim, 2) is None
    lim.release(1)
    assert limits.check_inflight(lim, 1) is None


@pytest.mark.parametrize("name,ctype,size,ok", [
    ("a.jpg", "image/jpeg", 100, True), ("a.PNG", None, 100, True), ("a.webp", "image/webp", 100, True),
    ("big.jpg", "image/jpeg", 11 * 1024 * 1024, False), ("a.pdf", "application/pdf", 100, False),
    ("a.exe", None, 100, False),
])
def test_protection5_attachment_checks(name, ctype, size, ok):
    assert (limits.check_attachment(name, ctype, size) is None) is ok


# ------------------------------------------------------------------ 公開展示模式（每人各自計時）

def test_public_mode_is_per_user_and_expires():
    t = {"now": 0.0}
    pm = PublicMode(clock=lambda: t["now"])
    pm.activate(1)
    assert pm.active(1) and not pm.active(2)
    t["now"] = 10 * 60
    pm.activate(2)  # 使用者 2 晚 10 分鐘開
    assert pm.banner(1).startswith("🔓 公開顯示中（剩 20 分鐘）")
    assert pm.banner(2).startswith("🔓 公開顯示中（剩 30 分鐘）")
    t["now"] = 30 * 60 + 1
    assert not pm.active(1) and pm.active(2)  # 1 到期不影響 2
    assert pm.banner(1) == ""
    assert pm.end(2) and not pm.active(2) and not pm.end(2)


# ------------------------------------------------------------------ 保留字

@pytest.mark.parametrize("text", ["ready", "Ready", " READY ", "ready!", "<@123> ready", "/done", "/done now", "/DONE", "ready。"])
def test_reserved_words_detected(text):
    assert is_reserved(text)


@pytest.mark.parametrize("text", ["!衣櫃", "我已經ready了嗎", "readyplayer", "done", "全部", "", None, "我要/done"])
def test_non_reserved(text):
    assert not is_reserved(text)


def test_no_reply_template_can_trigger_grader():
    from stylemate_ai import privacy
    texts = [replies.PUBLIC_WARNING, replies.HELP, replies.FAIL_TEXT, replies.STORAGE_FAIL_TEXT, replies.NOT_YOURS,
             replies.EXPIRED, replies.AI_FAIL_TEXT, replies.MENTION_HELP, replies.BLURRY, privacy.NOTICE, replies.not_clothing("杯子"),
             replies.added_public(2)]
    for t in texts:
        assert not is_reserved(t) and not t.strip().lower().startswith(("/done", "ready"))


def test_protection7_mentions_disabled():
    assert replies.ALLOWED_MENTIONS is not None
    assert not replies.ALLOWED_MENTIONS.everyone and not replies.ALLOWED_MENTIONS.users and not replies.ALLOWED_MENTIONS.roles


# ------------------------------------------------------------------ 打字替代

def test_parse_reply_variants():
    sel = Pending("select", 1)
    assert parse_reply(sel, "全部") == "all" and parse_reply(sel, "都不要") == "none"
    assert parse_reply(sel, "1,3") == "pick:1,3" and parse_reply(sel, "1 3") == "pick:1,3" and parse_reply(sel, "1、2") == "pick:1,2"
    assert parse_reply(sel, "隨便聊聊") is None
    assert parse_reply(Pending("confirm_delete", 1), "確認") == "confirm"
    assert parse_reply(Pending("confirm_delete", 1), "取消") == "cancel"
    assert parse_reply(Pending("public_confirm", 1), "確認公開") == "confirm"
    assert parse_reply(Pending("public_confirm", 1), "確認") is None
    assert parse_reply(Pending("consent", 1), "我同意") == "agree"
    d2 = Pending("delete_all_2", 1)
    assert parse_reply(d2, "確認刪除全部") == "execute" and parse_reply(d2, "確認") is None


# ------------------------------------------------------------------ 同意

def test_nothing_is_created_before_consent(cfg):
    e = make_engine(cfg)
    out = e.run_command(ALICE, "!衣櫃")
    assert out.pending and out.pending.kind == "consent" and "第一次使用" in out.public
    assert not cfg.users_dir.exists()  # 沒同意：沒有任何資料夾
    assert e.run_command(ALICE, "!說明").public == replies.HELP  # 說明不需要同意
    register(e, out, 5)
    res = e.resolve(out.pending, "decline", ALICE.user_id)
    assert "不會儲存" in res.public and not cfg.users_dir.exists()


def test_consent_creates_folder_and_resumes(cfg):
    e = make_engine(cfg)
    out = e.consent_prompt(ALICE, resume=["job1"])
    register(e, out, 5)
    res = e.resolve(out.pending, "agree", ALICE.user_id)
    assert res.resume == ["job1"] and (cfg.users_dir / "1001" / "wardrobe.xlsx").exists()
    assert e.has_consent(ALICE.user_id) and not e.has_consent(BOB.user_id)


# ------------------------------------------------------------------ 辨識與選擇

async def test_upload_not_clothing_and_blurry_and_error_store_nothing(cfg):
    e = make_engine(cfg, FakeAnalyzer(AnalysisResult(False, True, "一杯咖啡", [], "")))
    consent(e, ALICE)
    out = await e.analyze_upload(ALICE, JPG, ".jpg", "", NOW)
    assert "不是衣服，是一杯咖啡" in out.public and out.pending is None
    e.analyzer = FakeAnalyzer(AnalysisResult(True, False, "", [], ""))
    assert replies.BLURRY == (await e.analyze_upload(ALICE, JPG, ".jpg", "", NOW)).public
    e.analyzer = FakeAnalyzer(error=AnalysisError("boom"))
    assert (await e.analyze_upload(ALICE, JPG, ".jpg", "", NOW)).public == replies.FAIL_TEXT
    assert not (cfg.users_dir / "1001" / "photos").exists()
    assert wardrobe.active_count(e.store(1001)) == 0


async def test_select_one_then_rest_with_private_details(cfg):
    e = make_engine(cfg, FakeAnalyzer(clothes(item(), item("深藍牛仔褲", category="bottom", colors=["藍"], style="休閒"))))
    consent(e, ALICE)
    out = await upload(e, ALICE)
    assert "我看到 2 件" in out.public and "💡" in out.public
    assert [b.label for b in out.buttons] == ["加入 1", "加入 2", "全部加入", "都不要"]
    assert not (cfg.users_dir / "1001" / "photos").exists()  # 還沒選：照片不在硬碟上

    res = e.resolve(out.pending, "pick:1", ALICE.user_id)
    assert res.public == "✅ 已加入 1 件" and res.keep_pending
    assert [b.label for b in res.update_buttons] == ["加入 2", "全部加入", "都不要"]
    assert "#" in res.private and "1/200" in res.private  # 編號與件數只放在私人訊息
    assert "#" not in res.public
    photos = list((cfg.users_dir / "1001" / "photos").iterdir())
    assert len(photos) == 1 and photos[0].name.startswith("2026-10-23 14-05-33 白色棉質襯衫")

    res2 = e.resolve_text(out.pending, "全部", ALICE.user_id)
    assert res2.public == "✅ 已加入 1 件" and not res2.keep_pending
    assert out.pending.closed and out.pending.data["image"] is None
    assert wardrobe.active_count(e.store(1001)) == 2
    rows = e.store(1001).read("衣櫃")
    assert rows[0]["照片位置"] == rows[1]["照片位置"]  # 同一張照片只存一份、共用
    assert len(list((cfg.users_dir / "1001" / "photos").iterdir())) == 1


async def test_select_none_saves_nothing(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    out = await upload(e, ALICE)
    res = e.resolve(out.pending, "none", ALICE.user_id)
    assert "沒有被儲存" in res.public and out.pending.closed
    assert not (cfg.users_dir / "1001" / "photos").exists() and wardrobe.active_count(e.store(1001)) == 0


async def test_bad_pick_numbers_keep_pending(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    out = await upload(e, ALICE)
    for action in ("pick:5", "pick:0", "pick:1,9"):
        res = e.resolve(out.pending, action, ALICE.user_id)
        assert "編號不對" in res.public and res.keep_pending
    assert not out.pending.closed and wardrobe.active_count(e.store(1001)) == 0


async def test_only_owner_can_resolve(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    out = await upload(e, ALICE)
    res = e.resolve(out.pending, "all", BOB.user_id)
    assert res.denied and res.private == replies.NOT_YOURS
    assert e.resolve_text(out.pending, "全部", BOB.user_id).denied
    assert not out.pending.closed and wardrobe.active_count(e.store(1001)) == 0
    assert not (cfg.users_dir / "2002").exists()


async def test_double_resolution_is_safe(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    out = await upload(e, ALICE)
    e.resolve(out.pending, "all", ALICE.user_id)
    again = e.resolve(out.pending, "all", ALICE.user_id)
    assert again.private == replies.EXPIRED
    assert wardrobe.active_count(e.store(1001)) == 1


async def test_full_wardrobe_keeps_pending_and_leaves_no_photo(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    wardrobe.add_items(e.store(1001), 1001, [item() for _ in range(200)], None, NOW)
    out = await upload(e, ALICE)
    res = e.resolve(out.pending, "all", ALICE.user_id)
    assert "最多 200 件" in res.public and not out.pending.closed
    assert out.pending.data["photo_rel"] is None
    assert not list((cfg.users_dir / "1001" / "photos").glob("*"))  # 沒加成功：不留下任何照片檔


async def test_duplicate_hint_is_private(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    wardrobe.add_items(e.store(1001), 1001, [item("舊白襯衫")], None, NOW)
    out = await upload(e, ALICE)
    res = e.resolve(out.pending, "all", ALICE.user_id)
    assert "類似的「舊白襯衫」" in res.private and "舊白襯衫" not in res.public


async def test_inflight_released_when_pending_closes(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    e.limiter.acquire(ALICE.user_id)
    out = await e.analyze_upload(ALICE, JPG, ".jpg", "", NOW, inflight=True)
    register(e, out, 100)
    assert e.limiter.inflight(ALICE.user_id) == 1
    e.resolve(out.pending, "none", ALICE.user_id)
    assert e.limiter.inflight(ALICE.user_id) == 0


async def test_pending_expiry_discards_photo_and_releases(cfg, monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(pending_mod, "clock", lambda: t["now"])
    e = make_engine(cfg)
    consent(e, ALICE)
    e.limiter.acquire(ALICE.user_id)
    out = await e.analyze_upload(ALICE, JPG, ".jpg", "", NOW, inflight=True)
    register(e, out, 100)
    t["now"] += 599
    assert e.sweep() == []
    t["now"] += 2
    gone = e.sweep()
    assert gone == [out.pending] and out.pending.closed and out.pending.data["image"] is None
    assert e.limiter.inflight(ALICE.user_id) == 0 and e.registry.get(100) is None
    assert not (cfg.users_dir / "1001" / "photos").exists()


# ------------------------------------------------------------------ 公開提醒與衣櫃

async def fill(e, actor, *items):
    wardrobe.add_items(e.store(actor.user_id), actor.user_id, list(items), None, NOW)


async def test_closet_requires_public_confirmation_then_shows_banner(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    await fill(e, ALICE, item("秘密襯衫"))
    out = e.run_command(ALICE, "!衣櫃")
    assert "公開提醒" in out.public and "秘密襯衫" not in out.public and out.pending.kind == "public_confirm"
    register(e, out, 7)
    res = e.resolve_text(out.pending, "確認公開", ALICE.user_id)
    assert res.public.startswith("🔓 公開顯示中") and "秘密襯衫" in res.public and "穿過" not in res.public  # 簡化版不顯示穿著次數
    again = e.run_command(ALICE, "!衣櫃")  # 30 分鐘內不再詢問
    assert again.pending is None and "秘密襯衫" in again.public and again.public.startswith("🔓")
    assert "已結束" in e.run_command(ALICE, "!結束公開").public
    assert e.run_command(ALICE, "!衣櫃").pending.kind == "public_confirm"


async def test_public_confirm_cancel_and_not_owner(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    await fill(e, ALICE, item("秘密襯衫"))
    out = e.run_command(ALICE, "!衣櫃")
    register(e, out, 7)
    assert e.resolve(out.pending, "confirm", BOB.user_id).denied
    assert not e.public_mode.active(ALICE.user_id)
    res = e.resolve(out.pending, "cancel", ALICE.user_id)
    assert "不公開" in res.public and "秘密襯衫" not in res.public and not e.public_mode.active(ALICE.user_id)


async def test_public_mode_does_not_leak_between_users(cfg):
    e = make_engine(cfg)
    consent(e, ALICE, 1)
    consent(e, BOB, 2)
    await fill(e, ALICE, item("Alice的襯衫"))
    await fill(e, BOB, item("Bob的外套", category="outerwear"))
    out = e.run_command(ALICE, "!衣櫃")
    register(e, out, 7)
    e.resolve(out.pending, "confirm", ALICE.user_id)
    assert e.public_mode.active(ALICE.user_id) and not e.public_mode.active(BOB.user_id)
    bob = e.run_command(BOB, "!衣櫃")
    assert bob.pending and "Bob的外套" not in bob.public and "Alice" not in bob.public  # Bob 仍然要先確認


def open_public(e, actor, msg_id=900):
    out = e.run_command(actor, "!衣櫃")
    register(e, out, msg_id)
    e.resolve(out.pending, "confirm", actor.user_id)


async def test_closet_filters_and_paging(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    await fill(e, ALICE, *[item(f"襯衫{i}") for i in range(12)],
               item("黑外套", category="outerwear", colors=["黑"], style="街頭"))
    open_public(e, ALICE)
    page1 = e.run_command(ALICE, "!衣櫃").public
    assert "第 1/2 頁" in page1 and "下一頁：`!衣櫃 2`" in page1
    assert "第 2/2 頁" in e.run_command(ALICE, "!衣櫃 2").public
    only_coat = e.run_command(ALICE, "!衣櫃 外套").public
    assert "黑外套" in only_coat and "襯衫" not in only_coat
    assert "黑外套" in e.run_command(ALICE, "!衣櫃 顏色:黑 風格:街頭").public
    assert "沒有符合" in e.run_command(ALICE, "!衣櫃 顏色:紫").public


async def test_view_item_with_photo_history_and_isolation(cfg):
    e = make_engine(cfg)
    consent(e, ALICE, 1)
    consent(e, BOB, 2)
    out = await upload(e, ALICE)
    e.resolve(out.pending, "all", ALICE.user_id)
    item_id = e.store(1001).read("衣櫃")[0]["衣服ID"]
    open_public(e, ALICE)
    view = e.run_command(ALICE, f"!查看 #{item_id.lower()}")
    assert item_id in view.public and "修改歷史" in view.public and view.file and view.file.exists()
    assert view.file.resolve().is_relative_to((cfg.users_dir / "1001").resolve())
    open_public(e, BOB, 901)
    other = e.run_command(BOB, f"!查看 {item_id}")
    assert "找不到" in other.public and other.file is None and "白色棉質襯衫" not in other.public


# ------------------------------------------------------------------ 修改、刪除、復原、刪圖

async def test_modify_via_command_private_vs_public(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    await fill(e, ALICE, item("白襯衫"))
    iid = e.store(1001).read("衣櫃")[0]["衣服ID"]
    res = e.run_command(ALICE, f"!修改 {iid} 材質=亞麻 保暖=1 原因:洗過縮水")
    assert "已更新" in res.public and "亞麻" not in res.public and "白襯衫" not in res.public  # 沒公開：只講欄位
    row = wardrobe.get_item(e.store(1001), iid)
    assert row["材質"] == "亞麻" and row["保暖程度"] == 1 and row["AI信心"] == "使用者指定"
    assert wardrobe.item_history(e.store(1001), iid)[-1]["原因"] == "洗過縮水"
    open_public(e, ALICE)
    res2 = e.run_command(ALICE, f"!修改 {iid} 材質=絲")
    assert "亞麻 → 絲" in res2.public and res2.public.startswith("🔓")


async def test_modify_errors_are_friendly(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    await fill(e, ALICE, item())
    iid = e.store(1001).read("衣櫃")[0]["衣服ID"]
    assert "用法" in e.run_command(ALICE, "!修改").public
    assert "用法" in e.run_command(ALICE, f"!修改 {iid}").public
    assert "保暖程度請填 1 到 5" in e.run_command(ALICE, f"!修改 {iid} 保暖=99").public
    assert "不能修改" in e.run_command(ALICE, f"!修改 {iid} 密碼=1").public
    assert "找不到" in e.run_command(ALICE, "!修改 ZZZZ 材質=棉").public
    assert len(wardrobe.item_history(e.store(1001), iid)) == 1


async def test_delete_needs_confirmation_and_restore_works(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    await fill(e, ALICE, item())
    iid = e.store(1001).read("衣櫃")[0]["衣服ID"]
    out = e.run_command(ALICE, f"!刪除 {iid}")
    assert out.pending.kind == "confirm_delete" and "白色棉質襯衫" not in out.public
    register(e, out, 50)
    assert wardrobe.get_item(e.store(1001), iid)["狀態"] == "正常"  # 還沒確認：沒刪
    assert e.resolve(out.pending, "confirm", BOB.user_id).denied
    cancel = e.run_command(ALICE, f"!刪除 {iid}")
    register(e, cancel, 51)
    assert "沒有刪除" in e.resolve_text(cancel.pending, "取消", ALICE.user_id).public
    res = e.resolve_text(out.pending, "確認", ALICE.user_id)
    assert "已刪除" in res.public
    assert wardrobe.get_item(e.store(1001), iid, allow_deleted=True)["狀態"] == "已刪除"
    assert "找不到" in e.run_command(ALICE, f"!刪除 {iid}").public  # 已刪除不能再刪
    assert "已復原" in e.run_command(ALICE, f"!復原 {iid}").public
    assert wardrobe.get_item(e.store(1001), iid)["狀態"] == "正常"
    assert [h["動作"] for h in wardrobe.item_history(e.store(1001), iid)] == ["新增", "刪除", "復原"]


async def test_delete_photo_flow(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    out = await upload(e, ALICE)
    e.resolve(out.pending, "all", ALICE.user_id)
    iid = e.store(1001).read("衣櫃")[0]["衣服ID"]
    folder = cfg.users_dir / "1001" / "photos"
    assert len(list(folder.iterdir())) == 1
    ask = e.run_command(ALICE, f"!刪圖 {iid}")
    register(e, ask, 60)
    assert len(list(folder.iterdir())) == 1  # 確認前不刪
    assert "照片還在" not in e.resolve(ask.pending, "confirm", ALICE.user_id).public
    assert list(folder.iterdir()) == [] and wardrobe.get_item(e.store(1001), iid)["名稱"] == "白色棉質襯衫"
    assert "沒有照片" in e.run_command(ALICE, f"!刪圖 {iid}").public


async def test_delete_all_two_step_and_isolation(cfg):
    e = make_engine(cfg)
    consent(e, ALICE, 1)
    consent(e, BOB, 2)
    a_out = await upload(e, ALICE, 100)
    await fill(e, BOB, item("Bob襯衫"))
    pending_select = a_out.pending  # Alice 有一張待選擇的照片

    step1 = e.run_command(ALICE, "!刪除全部資料")
    register(e, step1, 70)
    assert e.resolve(step1.pending, "confirm", BOB.user_id).denied  # 別人不能代按
    assert (cfg.users_dir / "1001").exists()
    step2 = e.resolve(step1.pending, "confirm", ALICE.user_id)
    assert step2.pending.kind == "delete_all_2" and "確認刪除全部" in step2.public
    register(e, step2, 71)
    assert (cfg.users_dir / "1001").exists()  # 第一道確認後仍未刪

    assert e.resolve_text(step2.pending, "確認", ALICE.user_id) is None  # 打錯字：不處理
    assert (cfg.users_dir / "1001").exists()
    assert e.resolve(step2.pending, "execute", BOB.user_id).denied
    done = e.resolve_text(step2.pending, "確認刪除全部", ALICE.user_id)
    assert "已永久刪除" in done.public
    assert not (cfg.users_dir / "1001").exists() and (cfg.users_dir / "2002" / "wardrobe.xlsx").exists()
    assert wardrobe.active_count(e.store(2002)) == 1
    assert pending_select.closed and not e.has_consent(1001) and e.has_consent(2002)
    assert "第一次使用" in e.run_command(ALICE, "!衣櫃").public  # 刪完後再用要重新同意


async def test_delete_all_cancel_and_timeout(cfg, monkeypatch):
    t = {"now": 0.0}
    monkeypatch.setattr(pending_mod, "clock", lambda: t["now"])
    e = make_engine(cfg)
    consent(e, ALICE)
    await fill(e, ALICE, item())
    s1 = e.run_command(ALICE, "!刪除全部資料")
    register(e, s1, 70)
    s2 = e.resolve(s1.pending, "confirm", ALICE.user_id)
    register(e, s2, 71)
    t["now"] += 61
    assert [p.kind for p in e.sweep()] == ["delete_all_2"]  # 60 秒沒打：自動取消
    assert e.resolve(s2.pending, "execute", ALICE.user_id).private == replies.EXPIRED
    assert wardrobe.active_count(e.store(1001)) == 1


def test_unknown_commands_and_empty(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    assert "不認得" in e.run_command(ALICE, "!亂打").public
    assert e.run_command(ALICE, "!").public == replies.HELP


def test_commands_never_touch_other_users_even_if_ids_collide(cfg):
    e = make_engine(cfg)
    consent(e, ALICE, 1)
    consent(e, BOB, 2)
    wardrobe.add_items(e.store(1001), 1001, [item("A的")], None, NOW)
    iid = e.store(1001).read("衣櫃")[0]["衣服ID"]
    for cmd in (f"!修改 {iid} 材質=x", f"!刪除 {iid}", f"!復原 {iid}", f"!刪圖 {iid}"):
        assert "找不到" in e.run_command(BOB, cmd).public
    assert wardrobe.get_item(e.store(1001), iid)["材質"] == "棉"
