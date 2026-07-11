import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def parse_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass
class AppConfig:
    api_id: int
    api_hash: str
    tg_session: Optional[str] = None
    session_name: str = "my_account"

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        return cls(
            api_id=int(require_env("TELEGRAM_API_ID")),
            api_hash=require_env("TELEGRAM_API_HASH"),
            tg_session=os.getenv("TG_SESSION") or None,
            session_name=os.getenv("TELEGRAM_SESSION_NAME", "my_account"),
        )


@dataclass
class CaptchaConfig:
    enabled: bool = True
    debug: bool = False
    chats: list[str] = field(default_factory=list)
    bot_usernames: set[str] = field(default_factory=set)
    trigger_keywords: list[str] = field(
        default_factory=lambda: [
            "human verification",
            "captcha",
            "verify",
            "calculation result",
            "please select the calculation result",
            "人机验证",
            "请选择计算结果",
            "完成验证",
            "验证码",
        ]
    )
    answer_timeout: int = 20
    click_delay: float = 15.0
    ocr_enabled: bool = False
    download_dir: Path = Path("downloads")
    stats_interval: int = 60

    @classmethod
    def from_env(cls) -> "CaptchaConfig":
        config = cls()
        config.enabled = parse_bool("CAPTCHA_ENABLED", True)
        config.debug = parse_bool("CAPTCHA_DEBUG", False)
        config.chats = parse_csv(os.getenv("CAPTCHA_CHATS") or os.getenv("WATCH_CHATS"))
        config.bot_usernames = {
            item.lstrip("@").lower()
            for item in parse_csv(os.getenv("CAPTCHA_BOTS") or os.getenv("BOT_USERNAMES"))
        }

        keywords = parse_csv(os.getenv("CAPTCHA_KEYWORDS"))
        if keywords:
            config.trigger_keywords = keywords

        config.ocr_enabled = parse_bool("CAPTCHA_OCR", False)
        config.download_dir = Path(os.getenv("DOWNLOAD_DIR", "downloads"))

        timeout = os.getenv("CAPTCHA_TIMEOUT")
        if timeout:
            config.answer_timeout = int(timeout)

        click_delay = os.getenv("CAPTCHA_CLICK_DELAY")
        if click_delay:
            config.click_delay = float(click_delay)

        stats_interval = os.getenv("STATS_INTERVAL")
        if stats_interval:
            config.stats_interval = int(stats_interval)

        return config
