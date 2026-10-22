"""啟動點：uv run stylemate"""
from __future__ import annotations

import logging
import logging.handlers
import shutil
import sys

import discord

from .bot import StyleMateBot
from .config import ConfigError, load_config


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"設定有問題：{exc}", file=sys.stderr)
        sys.exit(2)

    if shutil.which("claude") is None:
        print("找不到 claude 指令，請先安裝 Claude Code 並登入。", file=sys.stderr)
        sys.exit(2)

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.users_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logfile = logging.handlers.RotatingFileHandler(config.log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logfile.setFormatter(fmt)  # 日誌只記運作狀況，不記衣服內容、Token 或附件網址
    logging.basicConfig(level=logging.INFO, handlers=[console, logfile])
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger(__name__).info("資料資料夾：%s", config.data_dir)

    bot = StyleMateBot(config)
    try:
        bot.run(config.discord_token, log_handler=None)
    except discord.LoginFailure:
        print("Discord 登入失敗：DISCORD_TOKEN 不正確。請到 Developer Portal 重設 token 再貼進 .env。", file=sys.stderr)
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        print("請到 Developer Portal → Bot，打開 Message Content Intent 並按 Save Changes。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
