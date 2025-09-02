import asyncio
import dataclasses
import errno
import logging
import os
import re
import secrets
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from functools import partial
from tempfile import NamedTemporaryFile
from typing import Any

import anyio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    BaseHandler,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ExtBot,
    MessageHandler,
    PicklePersistence,
    filters,
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WHITELISTED_CHAT_IDS = [int(chat_id) for chat_id in os.getenv("WHITELISTED_CHAT_IDS", "0").split(",")]
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
VERSION = os.getenv("VERSION", "dev")

RESULT_PATTERN = re.compile(r"Key found for UID: [0-9a-f]+, Sector: \d+, Key type: [AB]: ([0-9a-f]+)")


@dataclass
class ChatData:
    logs: defaultdict[str, set[str]] = field(default_factory=partial(defaultdict, set))
    keys: defaultdict[str, set[str]] = field(default_factory=partial(defaultdict, set))
    running: set[str] = field(default_factory=set)


type Context = CallbackContext[ExtBot[None], dict[str, Any], ChatData, dict[str, Any]]


class TokenRemoverFormatter(logging.Formatter):
    """Formatter that removes sensitive information in urls."""

    @staticmethod
    def _filter(s: str) -> str:
        return s.replace(TELEGRAM_TOKEN, "_TOKEN_")

    def format(self, record: logging.LogRecord) -> str:
        original = logging.Formatter.format(self, record)
        return self._filter(original)


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

for handler in logging.root.handlers:
    handler.setFormatter(TokenRemoverFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

_handlers: list[BaseHandler[Update, Context, None]] = []

HandlerCallback = Callable[[Update, Context], Coroutine[Any, Any, None]]


def add_handler(
    handler: type[BaseHandler[Update, Context, None]],
    **kwargs: Any,  # noqa: ANN401
) -> Callable[[HandlerCallback], HandlerCallback]:
    def decorator(func: HandlerCallback) -> HandlerCallback:
        _handlers.append(handler(callback=func, **kwargs))
        return func

    return decorator


def add_cmd_handler(cmd: str) -> Callable[[HandlerCallback], HandlerCallback]:
    return add_handler(CommandHandler, command=cmd)


@add_cmd_handler("start")
async def start(update: Update, context: Context) -> None:
    assert update.effective_chat  # noqa: S101

    if update.effective_chat.id not in WHITELISTED_CHAT_IDS:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"You are not whitelisted\nYour chat id is {update.effective_chat.id}",
        )
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Hello, I am HardnestedBot")


@add_cmd_handler("reset")
async def reset(update: Update, context: Context) -> None:
    assert context.chat_data  # noqa: S101
    assert update.effective_chat  # noqa: S101

    for k in dataclasses.fields(context.chat_data):
        setattr(context.chat_data, k.name, k.default_factory() if callable(k.default_factory) else k.default)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Reset chat data")


@add_handler(MessageHandler, filters=filters.Document.FileExtension("log") & filters.Chat(WHITELISTED_CHAT_IDS))
async def new_file(update: Update, context: Context) -> None:
    assert update.message  # noqa: S101
    assert update.message.document  # noqa: S101
    assert context.chat_data  # noqa: S101

    file = await context.bot.get_file(update.message.document)
    content = await file.download_as_bytearray()

    cuids: dict[str, None] = {}  # use dict instead of set to preserve order
    for line in content.decode("utf-8").splitlines():
        cuid = line.split(" ")[5]
        cuids[cuid] = None
        context.chat_data.logs[cuid].add(line)

    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="Select id to decode:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(i.upper(), callback_data=i)] for i in cuids]),
    )


@add_handler(CallbackQueryHandler, block=True)
async def button(update: Update, context: Context) -> None:
    assert update.callback_query  # noqa: S101
    assert update.callback_query.data  # noqa: S101
    assert context.chat_data  # noqa: S101
    assert update.effective_chat  # noqa: S101

    await update.callback_query.answer()
    cuid = update.callback_query.data
    force = cuid.startswith("!")
    if force:
        cuid = cuid[1:]

    if not force and context.chat_data.keys[cuid]:
        keys = context.chat_data.keys[cuid]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"```\n{'\n'.join(k.upper() for k in keys)}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Recalculate", callback_data=f"!{cuid}")]]),
        )
        return

    if not force and cuid in context.chat_data.running:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Already running; please wait",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Start anyway", callback_data=f"!{cuid}")]]),
        )
        return

    if not context.chat_data.logs[cuid]:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="No logs found for this chat; please resend file",
        )
        return

    context.chat_data.running.add(cuid)
    try:
        await run_hardnested(cuid, update.effective_chat.id, context)
    finally:
        context.chat_data.running.remove(cuid)


async def run_hardnested(cuid: str, chat_id: int, context: Context) -> None:
    assert context.chat_data  # noqa: S101

    bot = context.bot
    msg = await bot.send_message(chat_id=chat_id, text="Decoding logs for cuid " + cuid)
    key_msg = await context.bot.send_message(
        text=f"Found keys so far: ```\n{'\n'.join(k.upper() for k in context.chat_data.keys[cuid])}\n```"
        if context.chat_data.keys[cuid]
        else "No keys found yet",
        chat_id=chat_id,
        parse_mode=ParseMode.MARKDOWN,
    )
    with NamedTemporaryFile(mode="w", delete_on_close=False, encoding="utf-8") as f:
        for line in sorted(context.chat_data.logs[cuid]):
            f.write(line + "\n")
        f.flush()
        f.close()

        log.info("Decoding logs for tag %s in file %s", cuid, f.name)
        cur_out = ""
        out = []
        async for chunk in run_process(["./HardnestedRecovery/hardnested_main", f.name]):
            cur_out += chunk
            new_msg = cur_out.rfind("[=] Hardnested attack starting...")
            max_msg_len = 4000
            if len(cur_out) < max_msg_len and new_msg <= 0:
                await bot.edit_message_text(
                    text=f"```\n{cur_out.strip()}\n...\n```",
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                cutoff = max_msg_len if new_msg <= 0 else new_msg
                final = cur_out[:cutoff].rsplit("\n", 1)[0]
                cur_out = cur_out[len(final) + 1 :]
                out.append(final)

                keys = set(re.findall(RESULT_PATTERN, final))
                log.info("Found keys: %s", keys)
                context.chat_data.keys[cuid] |= keys

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
                await context.bot.send_message(
                    text="Done!",
                    chat_id=chat_id,
                )
                await bot.delete_message(chat_id, key_msg.message_id)
                key_msg = await context.bot.send_message(
                    text=f"```\n{'\n'.join(k.upper() for k in context.chat_data.keys[cuid])}\n```"
                    if context.chat_data.keys[cuid]
                    else "No keys found yet",
                    chat_id=chat_id,
                    parse_mode=ParseMode.MARKDOWN,
                )

        await bot.edit_message_text(
            text=f"```\n{cur_out}\n```",
            chat_id=chat_id,
            message_id=msg.message_id,
            parse_mode=ParseMode.MARKDOWN,
        )
        await bot.delete_message(chat_id, key_msg.message_id)
        await context.bot.send_message(
            text=f"```\n{'\n'.join(k.upper() for k in context.chat_data.keys[cuid])}\n```"
            if context.chat_data.keys[cuid]
            else "No keys found",
            chat_id=chat_id,
            parse_mode=ParseMode.MARKDOWN,
        )


async def run_process(args: str | Sequence[str]) -> AsyncIterator[str]:
    # this section uses really hacky file descriptor stuff to get the live preview working
    # for some reason normal pipes don't work with the hardnested utility
    # only works on unix, errors out on windows (run in docker)
    mo, so = os.openpty()  # type: ignore[attr-defined]
    os.set_blocking(mo, False)
    proc = await anyio.open_process(
        args,
        stdout=so,
        stderr=so,
    )
    os.close(so)

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
        yield chunk.decode("utf-8")
    os.close(mo)
    exit_code = await proc.wait()
    log.info("external process finished with exit code %s", exit_code)


@add_cmd_handler("keys")
async def all_keys(update: Update, context: Context) -> None:
    assert context.chat_data  # noqa: S101
    assert update.effective_chat  # noqa: S101

    keys = {k for ks in context.chat_data.keys.values() for k in ks}
    await context.bot.send_message(
        text=f"```\n{'\n'.join(k.upper() for k in keys)}\n```",
        chat_id=update.effective_chat.id,
        parse_mode=ParseMode.MARKDOWN,
    )


if __name__ == "__main__":
    log.info("HardnestedBot version %s", VERSION)

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .context_types(ContextTypes(chat_data=ChatData))
        .persistence(PicklePersistence("data/data.pickle"))
        .build()
    )

    app.add_handlers(_handlers)

    if WEBHOOK_URL:
        app.run_webhook(
            webhook_url=WEBHOOK_URL,
            port=WEBHOOK_PORT,
            listen="0.0.0.0",  # noqa: S104
            secret_token=secrets.token_urlsafe(),
        )
    else:
        app.run_polling()
