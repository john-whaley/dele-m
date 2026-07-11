import os

from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.sessions import StringSession


load_dotenv()

api_id = int(os.environ["TELEGRAM_API_ID"])
api_hash = os.environ["TELEGRAM_API_HASH"]

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("Login complete. Put this value into TG_SESSION on your VPS/Docker host:")
    print(client.session.save())
