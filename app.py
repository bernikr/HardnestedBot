import asyncio
import errno
import logging
import os
import re
import subprocess
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WHITELISTED_CHAT_IDS = [int(chat_id) for chat_id in os.getenv("WHITELISTED_CHAT_IDS", "0").split(",")]

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
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Decoding logs for cuid " + cuid)
    with NamedTemporaryFile(mode="w", delete=False) as f:
        for line in sorted(context.chat_data[cuid]):
            f.write(line + "\n")
        f.flush()
        f.close()

        logging.info(f"Decoding logs for tag {cuid} in file {f.name}")
        # this section uses really hacky file descriptor stuff to get the live preview working
        # for some reason normal pipes don't work with the hardnested utility
        mo, so = os.openpty()
        os.set_blocking(mo, False)
        process = subprocess.Popen(
            # socat tricks the program into thinking it's in a tty
            f"./HardnestedRecovery/hardnested_main {f.name}",
            stdout=so,
            stderr=so,
            shell=True,
        )
        os.close(so)
        cur_out = ""
        out = []
        while True:
            await asyncio.sleep(0)
            try:
                chunk = os.read(mo, 256)
            except BlockingIOError:
                await asyncio.sleep(1)
                continue
            except OSError as e:
                if e.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break

            cur_out += chunk.decode("utf-8")
            new_msg = cur_out.rfind("[=] Hardnested attack starting...")
            if len(cur_out) < 4000 and new_msg <= 0:  # over 4000 characters, send a new message
                await context.bot.edit_message_text(text=f"```\n{cur_out.strip()}\n...\n```", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode=ParseMode.MARKDOWN)
            else:
                final = cur_out[:min(4000, new_msg)].rsplit("\n", 1)[0]
                cur_out = cur_out[len(final) + 1:]
                out.append(final)
                await context.bot.edit_message_text(text=f"```\n{final}\n```", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode=ParseMode.MARKDOWN)
                msg = await context.bot.send_message(text=f"```\n{cur_out.strip()}\n...\n```", chat_id=update.effective_chat.id)
        await context.bot.edit_message_text(text=f"```\n{cur_out}\n```", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode=ParseMode.MARKDOWN)
        os.close(mo)
        out.append(cur_out)
        out = "\n".join(out)
        keys = set(re.findall(r"Key found for UID: [0-9a-f]+, Sector: \d+, Key type: [AB]: ([0-9a-f]+)", out))
        logging.info(f"Found keys: {keys}")
        if keys:
            await context.bot.send_message(text=f"Found keys:\n```\n{"\n".join(k.upper() for k in keys)}\n```", chat_id=update.effective_chat.id, parse_mode=ParseMode.MARKDOWN)



if __name__ == "__main__":
    application = (ApplicationBuilder()
                   .token(TELEGRAM_TOKEN)
                   .persistence(PicklePersistence("persistence/data.pickle"))
                   .build())

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.FileExtension("log") & filters.Chat(WHITELISTED_CHAT_IDS), new_file))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling()
