import asyncio
import errno
import logging
import os
import re
import subprocess  # noqa: S404: acknowledg possible security implications
from collections.abc import Iterable
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WHITELISTED_CHAT_IDS = [int(chat_id) for chat_id in os.getenv("WHITELISTED_CHAT_IDS", "0").split(",")]


class TokenRemoverFormatter(logging.Formatter):
    """Formatter that removes sensitive information in urls."""

    @staticmethod
    def _filter(s: str) -> str:
        return s.replace(TELEGRAM_TOKEN, "_TOKEN_")

    def format(self, record: logging.LogRecord) -> str:
        original = logging.Formatter.format(self, record)
        return self._filter(original)


logging.basicConfig(
    level=logging.INFO,
)
log = logging.getLogger(__name__)

for handler in logging.root.handlers:
    handler.setFormatter(TokenRemoverFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat  # noqa: S101

    if update.effective_chat.id not in WHITELISTED_CHAT_IDS:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"You are not whitelisted\nYour chat id is {update.effective_chat.id}",
        )
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Hello, I am HardnestedBot")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert context.chat_data  # noqa: S101
    assert update.effective_chat  # noqa: S101

    for k in list(context.chat_data):
        del context.chat_data[k]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Reset chat data")


async def new_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message  # noqa: S101
    assert update.message.document  # noqa: S101
    assert context.chat_data  # noqa: S101

    file = await context.bot.get_file(update.message.document)
    content = await file.download_as_bytearray()
    content = content.decode("utf-8").splitlines()

    if "logs" not in context.chat_data:
        context.chat_data["logs"] = {}

    for line in content:
        cuid = line.split(" ")[5]
        if cuid not in context.chat_data["logs"]:
            context.chat_data["logs"][cuid] = set()
        context.chat_data["logs"][cuid].add(line)

    cuids = list(dict.fromkeys([line.split(" ")[5] for line in content]).keys())
    keyboard = [[InlineKeyboardButton(i.upper(), callback_data=i)] for i in cuids]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="Select id to decode:",
        reply_markup=reply_markup,
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.callback_query  # noqa: S101
    assert update.callback_query.data  # noqa: S101
    assert context.chat_data  # noqa: S101
    assert update.effective_chat  # noqa: S101

    await update.callback_query.answer()
    cuid = update.callback_query.data
    force = cuid.startswith("!")
    if force:
        cuid = cuid[1:]
    if "keys" not in context.chat_data:
        context.chat_data["keys"] = {}

    if not force and cuid in context.chat_data["keys"]:
        keys = context.chat_data["keys"][cuid]
        keyboard = [[InlineKeyboardButton("Recalculate", callback_data=f"!{cuid}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Keys found for this cuid:\n```\n{"\n".join(keys)}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )
        return

    if "running" not in context.chat_data:
        context.chat_data["running"] = set()
    if not force and cuid in context.chat_data["running"]:
        keyboard = [[InlineKeyboardButton("Start anyway", callback_data=f"!{cuid}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Already running; please wait",
            reply_markup=reply_markup,
        )
        return

    if "logs" not in context.chat_data:
        context.chat_data["logs"] = {}
    if cuid not in context.chat_data["logs"]:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="No logs found for this chat; please resend file",
        )
        return

    context.chat_data["running"].add(cuid)
    keys = await run_hardnested(cuid, context.chat_data["logs"][cuid], update.effective_chat.id, context.bot)
    if keys:
        await context.bot.send_message(
            text=f"Found keys:\n```\n{"\n".join(keys)}\n```",
            chat_id=update.effective_chat.id,
            parse_mode=ParseMode.MARKDOWN,
        )
        context.chat_data["keys"][cuid] = context.chat_data["keys"].get(cuid, set()) | keys
    context.chat_data["running"].remove(cuid)


async def run_hardnested(cuid: str, logs: str, chat_id: int, bot: Bot) -> Iterable[str]:
    msg = await bot.send_message(chat_id=chat_id, text="Decoding logs for cuid " + cuid)
    with NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as f:
        for line in sorted(logs):
            f.write(line + "\n")
        f.flush()
        f.close()

        log.info("Decoding logs for tag %s in file %s", cuid, f.name)
        # this section uses really hacky file descriptor stuff to get the live preview working
        # for some reason normal pipes don't work with the hardnested utility
        # only works on unix, errors out on windows (run in docker)
        mo, so = os.openpty()  # pyright: ignore[reportAttributeAccessIssue]
        os.set_blocking(mo, False)
        subprocess.Popen(
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
            if len(cur_out) < 4000 and new_msg <= 0:  # noqa: PLR2004: over 4000 characters, send a new message
                await bot.edit_message_text(
                    text=f"```\n{cur_out.strip()}\n...\n```",
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                cutoff = 4000 if new_msg <= 0 else new_msg
                final = cur_out[:cutoff].rsplit("\n", 1)[0]
                cur_out = cur_out[len(final) + 1 :]
                out.append(final)
                await bot.edit_message_text(
                    text=f"```\n{final}\n```",
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    parse_mode=ParseMode.MARKDOWN,
                )
                msg = await bot.send_message(
                    text=f"```\n{cur_out.strip()}\n...\n```",
                    chat_id=chat_id,
                    parse_mode=ParseMode.MARKDOWN,
                )
        await bot.edit_message_text(
            text=f"```\n{cur_out}\n```",
            chat_id=chat_id,
            message_id=msg.message_id,
            parse_mode=ParseMode.MARKDOWN,
        )
        os.close(mo)
        out.append(cur_out)
        out = "\n".join(out)
        keys = set(re.findall(r"Key found for UID: [0-9a-f]+, Sector: \d+, Key type: [AB]: ([0-9a-f]+)", out))
        log.info("Found keys: %s", keys)
        return keys


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(PicklePersistence("persistence/data.pickle")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(
        MessageHandler(filters.Document.FileExtension("log") & filters.Chat(WHITELISTED_CHAT_IDS), new_file),
    )
    app.add_handler(CallbackQueryHandler(button, block=False))

    app.run_polling()
