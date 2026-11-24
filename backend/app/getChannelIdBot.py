from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Chat ID:", update.effective_chat.id)

app = Application.builder().token("9206366584:AAHiZPdwBJkkOJRJVV6yv_rwZsxH6IBvymY").build()
app.add_handler(MessageHandler(filters.ALL, get_chat_id))
app.run_polling()