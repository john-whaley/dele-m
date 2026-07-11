import asyncio
import logging

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from telegram_bot.captcha_solver import CaptchaSolver
from telegram_bot.config import AppConfig, CaptchaConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class TelegramCaptchaBot:
    def __init__(self) -> None:
        self.app_config = AppConfig.from_env()
        self.captcha_config = CaptchaConfig.from_env()
        self.client = self._create_client()
        self.solver = CaptchaSolver(self.captcha_config)

    def _create_client(self) -> TelegramClient:
        if self.app_config.tg_session:
            logger.info("Using TG_SESSION from environment")
            session = StringSession(self.app_config.tg_session)
        else:
            logger.info("Using file session: %s", self.app_config.session_name)
            session = self.app_config.session_name

        return TelegramClient(session, self.app_config.api_id, self.app_config.api_hash)

    async def start(self) -> None:
        await self.client.start()
        self._register_handlers()

        logger.info("Telegram captcha listener started")
        logger.info("CAPTCHA_ENABLED=%s", self.captcha_config.enabled)
        logger.info("CAPTCHA_DEBUG=%s", self.captcha_config.debug)
        logger.info("CAPTCHA_CHATS=%s", self.captcha_config.chats or "all visible chats")
        logger.info("CAPTCHA_BOTS=%s", sorted(self.captcha_config.bot_usernames) or "all senders")
        logger.info("CAPTCHA_CLICK_DELAY=%ss", self.captcha_config.click_delay)
        logger.info("CAPTCHA_OCR=%s", self.captcha_config.ocr_enabled)

        asyncio.create_task(self.print_stats_periodically())
        await self.client.run_until_disconnected()

    def _register_handlers(self) -> None:
        @self.client.on(events.NewMessage)
        async def message_handler(event: events.NewMessage.Event) -> None:
            await self.handle_message(event)

    async def handle_message(self, event: events.NewMessage.Event) -> None:
        if not await self.is_watched_chat(event):
            return

        if not await self.is_watched_sender(event):
            return

        if self.captcha_config.enabled:
            await self.solver.handle_captcha(event)
        elif self.captcha_config.debug:
            await self.solver.print_debug_message(event)

    async def is_watched_chat(self, event: events.NewMessage.Event) -> bool:
        if not self.captcha_config.chats:
            return True

        chat = await event.get_chat()
        chat_id = str(event.chat_id or getattr(chat, "id", ""))
        chat_username = (getattr(chat, "username", None) or "").lower()

        for watched in self.captcha_config.chats:
            watched_value = watched.strip().lstrip("@").lower()
            if watched_value == chat_username:
                return True
            if watched_value == chat_id.lower() or watched_value == chat_id.lstrip("-").lower():
                return True

        return False

    async def is_watched_sender(self, event: events.NewMessage.Event) -> bool:
        if not self.captcha_config.bot_usernames:
            return True

        sender = await event.get_sender()
        username = getattr(sender, "username", None)
        if not username:
            return False

        return username.lower() in self.captcha_config.bot_usernames

    async def print_stats_periodically(self) -> None:
        while True:
            await asyncio.sleep(self.captcha_config.stats_interval)
            stats = self.solver.get_stats()
            logger.info(
                "Stats: total=%s success=%s failed=%s skipped=%s",
                stats["total"],
                stats["success"],
                stats["failed"],
                stats["skipped"],
            )


def main() -> None:
    bot = TelegramCaptchaBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Stopped")


if __name__ == "__main__":
    main()
