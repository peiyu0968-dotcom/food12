"""從 .env 讀設定。缺項或還是範例字就直接說明並停下，不用預設值硬跑。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_KEYS = ("DISCORD_TOKEN", "CHANNEL_ID", "CLAUDE_MODEL", "DATA_DIR")
PLACEHOLDERS = ("你的bot_token", "你的頻道ID")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    channel_id: int
    claude_model: str
    data_dir: Path

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "agent.log"


def load_config(env_path: Path | None = None) -> Config:
    env_path = env_path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise ConfigError(f"找不到設定檔 {env_path}，請照 docs/discord-bot-setup.md 建立 .env")

    values = {k: (v or "").strip() for k, v in dotenv_values(env_path).items()}

    missing = [k for k in REQUIRED_KEYS if not values.get(k)]
    if missing:
        raise ConfigError(f".env 缺少這些項目：{', '.join(missing)}")

    for key in ("DISCORD_TOKEN", "CHANNEL_ID"):
        if values[key] in PLACEHOLDERS:
            raise ConfigError(f".env 的 {key} 還是範例文字，請換成真正的值")

    try:
        channel_id = int(values["CHANNEL_ID"])
    except ValueError:
        raise ConfigError("CHANNEL_ID 必須是一串數字（在 Discord 對頻道按右鍵 → 複製頻道 ID）") from None

    data_dir = Path(values["DATA_DIR"])
    if not data_dir.is_absolute():
        data_dir = env_path.parent / data_dir  # 相對路徑以 .env 所在資料夾為基準
    data_dir = data_dir.resolve()

    return Config(
        discord_token=values["DISCORD_TOKEN"],
        channel_id=channel_id,
        claude_model=values["CLAUDE_MODEL"],
        data_dir=data_dir,
    )
