"""Helper: prints the chat ID of any message the bot receives.

Usage: set TELEGRAM_BOT_TOKEN in .env, run this script, then send a message
to the bot (or to the group/channel it was added to).
"""
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN is not set (add it to .env)")


async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Chat ID:", update.effective_chat.id)


app = Application.builder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.ALL, get_chat_id))
app.run_polling()
