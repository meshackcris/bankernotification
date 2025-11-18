# Telegram Broadcast Bot

Auto-forward anything you DM to the bot into every group, supergroup, or channel that subscribed with `/subscribe`. Chats opt in/out themselves, so no chat IDs are hard-coded.

## Features
- `/subscribe` and `/unsubscribe` inside a chat where the bot is an admin decide whether broadcasts go there.
- `/list` (in a private DM) shows the chats that will receive broadcasts.
- Any non-command message you send in a private DM is copied to every subscribed chat (supports text, media, polls, etc.).
- Automatically drops chats it can no longer post to.

## Prerequisites
1. Python 3.11+.
2. A Telegram Bot token from [@BotFather](https://t.me/BotFather).

## Setup
```powershell
cd C:\Projects\test\bankernotification
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Configuration comes from environment variables. The easiest workflow is to copy `.env.example` to `.env` and fill in:

- `TELEGRAM_BOT_TOKEN`: BotFather token (required).
- `TELEGRAM_ALLOWED_USER_IDS`: optional comma-separated Telegram numeric IDs (e.g., from @userinfobot) allowed to DM broadcast; leave blank to allow anyone.
- `SUBSCRIPTIONS_FILE`: optional custom path; defaults to `data/subscriptions.json`.

`main.py` loads the `.env` file automatically via `python-dotenv`, so once the file is populated you can just run `python main.py` inside the virtual environment.

## Usage
1. Add the bot as an admin (with permission to send messages) in every group/channel that should receive announcements.
2. In each chat, send `/subscribe`. Only admins can run the command in groups.
3. DM the bot and send your announcement as a normal message -- no command needed. The bot copies that message into every subscribed chat.
4. Use `/unsubscribe` in a chat to remove it, or `/list` in a DM to review all active targets.

If a broadcast fails because the bot was removed from a chat, the chat is automatically pruned from the list and you'll see that noted in the DM response.
