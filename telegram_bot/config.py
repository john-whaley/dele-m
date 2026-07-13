import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


DEFAULT_AI_PROMPT = "\u56fe\u7247\u4e2d\u7684\u516c\u5f0f\u53ca\u7ed3\u679c\u662f\u591a\u5c11\uff1f"


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
    bot_ids: set[int] = field(default_factory=set)
    trigger_keywords: list[str] = field(
        default_factory=lambda: [
            "human verification",
            "captcha",
            "verify",
            "calculation result",
            "please select the calculation result",
            "\u4eba\u673a\u9a8c\u8bc1",
            "\u8bf7\u9009\u62e9\u8ba1\u7b97\u7ed3\u679c",
            "\u5b8c\u6210\u9a8c\u8bc1",
            "\u9a8c\u8bc1\u7801",
        ]
    )
    answer_timeout: int = 20
    click_delay: float = 15.0
    ocr_enabled: bool = False
    ai_ocr_enabled: bool = False
    ai_api_key: Optional[str] = None
    ai_base_url: str = "https://api.openai.com/v1/chat/completions"
    ai_model: str = "gpt-4o-mini"
    ai_mode: str = "fallback"
    ai_prompt: str = DEFAULT_AI_PROMPT
    ai_timeout: int = 30
    fallback_guess_enabled: bool = False
    fallback_guess_min_confidence: float = 0.7
    download_dir: Path = Path("downloads")
    stats_interval: int = 60

    @classmethod
    def from_env(cls) -> "CaptchaConfig":
        config = cls()
        config.enabled = parse_bool("CAPTCHA_ENABLED", True)
        config.debug = parse_bool("CAPTCHA_DEBUG", False)
        config.chats = parse_csv(os.getenv("CAPTCHA_CHATS") or os.getenv("WATCH_CHATS"))
        config.bot_ids = {
            int(item)
            for item in parse_csv(os.getenv("CAPTCHA_BOT_IDS"))
        }

        keywords = parse_csv(os.getenv("CAPTCHA_KEYWORDS"))
        if keywords:
            config.trigger_keywords = keywords

        config.ocr_enabled = parse_bool("CAPTCHA_OCR", False)
        config.ai_ocr_enabled = parse_bool("CAPTCHA_AI_OCR", False)
        config.ai_api_key = os.getenv("CAPTCHA_AI_API_KEY") or os.getenv("OPENAI_API_KEY") or None
        config.ai_base_url = os.getenv("CAPTCHA_AI_BASE_URL", config.ai_base_url)
        config.ai_model = os.getenv("CAPTCHA_AI_MODEL", config.ai_model)
        config.ai_mode = os.getenv("CAPTCHA_AI_MODE", config.ai_mode).strip().lower()
        config.ai_prompt = os.getenv("CAPTCHA_AI_PROMPT", config.ai_prompt)
        if config.ai_mode not in {"fallback", "always"}:
            config.ai_mode = "fallback"

        ai_timeout = os.getenv("CAPTCHA_AI_TIMEOUT")
        if ai_timeout:
            config.ai_timeout = int(ai_timeout)

        config.fallback_guess_enabled = parse_bool("CAPTCHA_FALLBACK_GUESS", False)
        guess_confidence = os.getenv("CAPTCHA_FALLBACK_MIN_CONFIDENCE")
        if guess_confidence:
            config.fallback_guess_min_confidence = float(guess_confidence)

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
