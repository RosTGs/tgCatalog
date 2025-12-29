from __future__ import annotations

import os
import logging

from dotenv import load_dotenv

# сначала грузим .env
load_dotenv()

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from admin_bot import (
    cb,
    on_text,
    on_doc,
    on_photo,
    kb_home,
    kb_cats,
    kb_adm_home,
)

from core_helpers import (
    get_setting,
    is_admin,
    require_staff,
    replace_menu,
    delete_scope,
    clear_all,
    touch_user,
)

BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("CLIENT_BOT_TOKEN")
)

if not BOT_TOKEN:
    raise RuntimeError(
        "Укажи токен бота в .env (BOT_TOKEN / TELEGRAM_BOT_TOKEN / CLIENT_BOT_TOKEN)"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    await clear_all(update, context)
    text = get_setting("welcome_html") or "Каталог. Нажмите «Открыть витрину»."
    await replace_menu(update, context, text, kb_home(), scope="home")


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    await delete_scope(update, context, "home")
    await delete_scope(update, context, "shop")
    await replace_menu(
        update,
        context,
        "Выберите категорию:",
        kb_cats(0),
        scope="shop",
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    if not require_staff(update):
        return
    await replace_menu(
        update,
        context,
        "<b>Панель</b>",
        kb_adm_home(is_admin(update.effective_user.id)),
        scope="admin",
    )


async def error_handler(update: object, context):
    logging.exception("Unhandled error: %s", context.error)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("admin", cmd_admin))

    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.Document.ALL, on_doc))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
