import json
import logging
import os
from pathlib import Path
from threading import RLock
from typing import Dict, List, Optional, Set

from dotenv import load_dotenv
from telegram import Chat, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
LOGGER = logging.getLogger(__name__)

load_dotenv()

class SubscriptionStore:
    """Thread-safe JSON-backed storage for target chat IDs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._data: Dict[str, Dict[str, Dict[str, str]]] = {"chats": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as src:
                raw = json.load(src)
        except json.JSONDecodeError:
            LOGGER.warning("Subscription file is corrupt. Starting with an empty list.")
            raw = {}
        chats = raw.get("chats") if isinstance(raw, dict) else None
        if isinstance(chats, dict):
            self._data["chats"] = chats

    def _flush(self) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as dst:
            json.dump(self._data, dst, indent=2, ensure_ascii=False)
        tmp_path.replace(self.path)

    def add_chat(self, chat: Chat) -> bool:
        title = (
            chat.title
            or getattr(chat, "full_name", None)
            or (chat.username and f"@{chat.username}")
            or str(chat.id)
        )
        payload = {"title": title, "type": chat.type}
        key = str(chat.id)
        with self._lock:
            existing = self._data["chats"].get(key)
            self._data["chats"][key] = payload
            self._flush()
        return existing is None

    def remove_chat(self, chat_id: int) -> bool:
        key = str(chat_id)
        with self._lock:
            if key not in self._data["chats"]:
                return False
            del self._data["chats"][key]
            self._flush()
            return True

    def list_chats(self) -> List[Dict[str, str]]:
        with self._lock:
            items = [
                {"id": int(chat_id), **meta}
                for chat_id, meta in self._data["chats"].items()
            ]
        return sorted(items, key=lambda item: item["title"].lower())

    def get_chat(self, chat_id: int) -> Optional[Dict[str, str]]:
        with self._lock:
            payload = self._data["chats"].get(str(chat_id))
            if payload is None:
                return None
            return {"id": chat_id, **payload}

    def count(self) -> int:
        with self._lock:
            return len(self._data["chats"])


def parse_allowed_user_ids(raw_value: Optional[str]) -> Set[int]:
    if not raw_value:
        return set()
    allowed: Set[int] = set()
    tokens = raw_value.replace(";", ",").split(",")
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        try:
            allowed.add(int(token))
        except ValueError:
            LOGGER.warning("Ignoring invalid user id value: %s", token)
    return allowed


def ensure_store(context: ContextTypes.DEFAULT_TYPE) -> SubscriptionStore:
    store = context.bot_data.get("store")
    if not isinstance(store, SubscriptionStore):
        raise RuntimeError("SubscriptionStore not configured.")
    return store


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_type = update.effective_chat.type if update.effective_chat else "private"
    intro = [
        "Hi! I relay announcements to every chat that subscribed via /subscribe.",
        "Use /subscribe or /unsubscribe inside a group/channel where I'm an admin.",
        "Send any non-command message here in a private chat to broadcast it everywhere.",
    ]
    allowed_users: Set[int] = context.bot_data.get("allowed_user_ids", set())
    if allowed_users:
        intro.append("Only authorized users can broadcast via DM.")
    if chat_type != ChatType.PRIVATE:
        intro.append("For best results, DM me with /start for detailed help.")
    await update.effective_message.reply_text("\n".join(intro))


async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return
    if chat.type == ChatType.PRIVATE:
        await message.reply_text(
            "Use /subscribe inside a group, supergroup, or channel where I'm an admin."
        )
        return

    if chat.type != ChatType.CHANNEL and update.effective_user:
        member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
        if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
            await message.reply_text("Only chat admins can subscribe me.")
            return

    store = ensure_store(context)
    is_new = store.add_chat(chat)
    if is_new:
        await message.reply_text("Subscribed! I'll broadcast here.")
    else:
        await message.reply_text("Already subscribed.")


async def unsubscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return
    if chat.type == ChatType.PRIVATE:
        await message.reply_text(
            "Use /unsubscribe inside a group, supergroup, or channel to remove it."
        )
        return
    if chat.type != ChatType.CHANNEL and update.effective_user:
        member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
        if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
            await message.reply_text("Only chat admins can unsubscribe me.")
            return
    store = ensure_store(context)
    removed = store.remove_chat(chat.id)
    if removed:
        await message.reply_text("Removed from the broadcast list.")
    else:
        await message.reply_text("This chat was not subscribed.")


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    store = ensure_store(context)
    chats = store.list_chats()
    if not chats:
        await message.reply_text("No chats subscribed yet.")
        return
    lines = [
        f"{idx}. {chat['title']} ({chat['type']})"
        for idx, chat in enumerate(chats, start=1)
    ]
    await message.reply_text("Broadcast targets:\n" + "\n".join(lines))


async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    allowed_users: Set[int] = context.bot_data.get("allowed_user_ids", set())
    user = update.effective_user
    if allowed_users and (user is None or user.id not in allowed_users):
        await message.reply_text("You're not allowed to broadcast with this bot.")
        return
    store = ensure_store(context)
    targets = store.list_chats()
    if not targets:
        await message.reply_text("No chats have subscribed yet. Use /subscribe first.")
        return

    sent = 0
    failures = []
    for target in targets:
        chat_id = target["id"]
        try:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
            sent += 1
        except Forbidden:
            store.remove_chat(chat_id)
            failures.append(
                f"Lost access to {target['title']} ({target['type']}). Removed from list."
            )
        except TelegramError as exc:
            LOGGER.warning("Failed to forward to %s: %s", chat_id, exc)
            failures.append(f"{target['title']}: {exc.message if hasattr(exc, 'message') else exc}")

    summary = [f"Broadcast delivered to {sent} chat(s)."]
    if failures:
        summary.append("Issues:\n- " + "\n- ".join(failures))
    await message.reply_text("\n".join(summary))


def build_application(token: str, store: SubscriptionStore, allowed_users: Set[int]):
    application = ApplicationBuilder().token(token).build()
    application.bot_data["store"] = store
    application.bot_data["allowed_user_ids"] = allowed_users

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(
        CommandHandler(
            "subscribe",
            subscribe_handler,
            filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL,
        )
    )
    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe_handler,
            filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL,
        )
    )
    application.add_handler(
        CommandHandler("list", list_handler, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND, broadcast_handler
        )
    )
    return application


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set the TELEGRAM_BOT_TOKEN environment variable.")
    allowed_users = parse_allowed_user_ids(os.environ.get("TELEGRAM_ALLOWED_USER_IDS"))
    storage_path = Path(
        os.environ.get("SUBSCRIPTIONS_FILE", "data/subscriptions.json")
    )
    store = SubscriptionStore(storage_path)
    if allowed_users:
        LOGGER.info("Broadcast restricted to Telegram user IDs: %s", sorted(allowed_users))
    else:
        LOGGER.warning(
            "No TELEGRAM_ALLOWED_USER_IDS set. Anyone who DMs the bot can broadcast."
        )
    app = build_application(token, store, allowed_users)
    LOGGER.info("Bot is starting. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
