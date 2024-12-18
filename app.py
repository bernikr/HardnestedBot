import logging
import os
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, \
    filters, PicklePersistence

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WHITELISTED_CHAT_IDS = [int(chat_id) for chat_id in os.getenv("WHITELISTED_CHAT_IDS").split(",")]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in WHITELISTED_CHAT_IDS:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"You are not whitelisted\nYour chat id is {update.effective_chat.id}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Hello, I am HardnestedBot")


async def new_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await context.bot.get_file(update.message.document)
    content = await file.download_as_bytearray()
    content = content.decode("utf-8").splitlines()

    for line in content:
        cuid = line.split(" ")[5]
        if cuid not in context.chat_data:
            context.chat_data[cuid] = set()
        context.chat_data[cuid].add(line)

    cuids = list(dict.fromkeys([line.split(" ")[5] for line in content]).keys())
    keyboard = [
        [InlineKeyboardButton(i.upper(), callback_data=i)] for i in cuids
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Select id to decode:", reply_markup=reply_markup)


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    cuid = query.data
    if cuid not in context.chat_data:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="No logs found for this chat; please resend file")
        return
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Decoding logs for chat id " + cuid)
    with NamedTemporaryFile(mode="w", delete=False) as f:
        for line in sorted(context.chat_data[cuid]):
            f.write(line + "\n")
        f.close()
        await context.bot.editMessageText(text="Logs decoded for chat id " + cuid, chat_id=update.effective_chat.id, message_id=msg.message_id)


if __name__ == "__main__":
    application = (ApplicationBuilder()
                   .token(TELEGRAM_TOKEN)
                   .persistence(PicklePersistence("data.pickle"))
                   .build())

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.FileExtension("log") & filters.Chat(WHITELISTED_CHAT_IDS), new_file))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling()
