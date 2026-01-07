
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from dotenv import load_dotenv
import os

# важно: используем те же функции доступа к БД и резерву, что и админ
from admin_bot import db_query, reserve_url_for, reserve_text

# Загрузка конфигурации из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

app = Application.builder().token(BOT_TOKEN).build()

# Главное меню
def client_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📂 Каталог", callback_data="client:categories")]
        ]
    )

# Клавиатура с категориями
def client_categories_keyboard(categories) -> InlineKeyboardMarkup:
    rows = []
    for cat in categories:
        rows.append([InlineKeyboardButton(cat["name"], callback_data=f"client:cat:{cat['id']}")])
    rows.append([InlineKeyboardButton("🔙 В главное меню", callback_data="client:back_main")])
    return InlineKeyboardMarkup(rows)

# Клавиатура с товарами
def client_products_keyboard(category_id: int, products) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        rows.append([InlineKeyboardButton(p["name"], callback_data=f"client:product:{category_id}:{p['id']}")])
    rows.append([InlineKeyboardButton("🔙 К категориям", callback_data="client:back_categories")])
    return InlineKeyboardMarkup(rows)

# Кнопка бронирования
def reserve_button(prod) -> InlineKeyboardButton:
    url = f"https://wa.me/{prod['reserve_phone']}?text=Забронировать товар: {prod['name']}"
    return InlineKeyboardButton(f"📞 Забронировать {prod['name']}", url=url)


def product_variant_lines(pid: int):
    rows = db_query(
        "SELECT name,stock FROM product_variants WHERE product_id=? ORDER BY id",
        (pid,),
    )
    if not rows:
        return ["Варианты: нет."], 0
    total = sum(row["stock"] for row in rows)
    lines = ["Варианты:"]
    lines.extend([f"• {row['name']} — {row['stock']}" for row in rows])
    lines.append(f"Итого: {total}")
    return lines, total

# Обработчик старта
async def client_start_handler(update: Update, context):
    if update.message:
        await update.message.reply_text("Добро пожаловать в каталог.", reply_markup=client_main_menu_keyboard())
    elif update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text("Добро пожаловать в каталог.", reply_markup=client_main_menu_keyboard())

# Обработчик меню
async def client_menu_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "client:categories":
        categories = db_query("SELECT * FROM categories ORDER BY id", ())
        categories = [dict(row) for row in categories]
        text = "Выберите категорию:"
        if not categories:
            text = "Категорий пока нет."
        await query.edit_message_text(text, reply_markup=client_categories_keyboard(categories))
        return

    if data == "client:back_main":
        await query.edit_message_text("Главное меню клиента:", reply_markup=client_main_menu_keyboard())
        return

# Обработчик категорий
async def client_categories_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("client:cat:"):
        cat_id = int(data.split(":")[2])
        products = db_query(
            "SELECT p.* FROM products p "
            "JOIN product_categories pc ON pc.product_id=p.id "
            "WHERE p.is_active=1 AND pc.category_id=? ORDER BY p.id",
            (cat_id,),
        )
        prods = [dict(row) for row in products]
        text = "Выберите товар:"
        if not prods:
            text = "В этой категории пока нет товаров."
        await query.edit_message_text(
            text, reply_markup=client_products_keyboard(cat_id, prods)
        )
        return

# Обработчик товаров
# Обработчик товаров – только текст, без картинок и без дублей



async def client_products_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "client:back_categories":
        # назад к списку категорий
        categories = db_query("SELECT * FROM categories ORDER BY id", ())
        cats = [dict(row) for row in categories]
        text = "Выберите категорию:"
        if not cats:
            text = "Категорий пока нет."
        await query.edit_message_text(
            text, reply_markup=client_categories_keyboard(cats)
        )
        return

    if data.startswith("client:product:"):
        _, _, cat_id_str, prod_id_str = data.split(":")
        cat_id = int(cat_id_str)
        pid = int(prod_id_str)

        # грузим товар + категорию
        rows = db_query("SELECT * FROM products WHERE id=?", (pid,))
        if not rows:
            # если товара нет, просто вернёмся к категориям
            categories = db_query("SELECT * FROM categories ORDER BY id", ())
            cats = [dict(row) for row in categories]
            await query.edit_message_text(
                "Товар не найден.", reply_markup=client_categories_keyboard(cats)
            )
            return

        prod = rows[0]
        cat_rows = db_query(
            "SELECT c.name FROM categories c "
            "JOIN product_categories pc ON pc.category_id=c.id "
            "WHERE pc.product_id=? ORDER BY c.id",
            (pid,),
        )
        cats_label = ", ".join([row["name"] for row in cat_rows]) or "—"

        # текст карточки товара
        stock_lines, total_stock = product_variant_lines(pid)
        lines = [
            f"<b>{prod['name']}</b>",
            f"Категории: {cats_label}",
            *stock_lines,
        ]
        if total_stock <= 0:
            lines.append("<b>Нет в наличии</b>")
        if prod["description"]:
            lines.append("")
            lines.append(prod["description"])

        caption = "\n".join(lines)

        # кнопки
        buttons: list[list[InlineKeyboardButton]] = []
        url = reserve_url_for(prod)
        if url:
            buttons.append([InlineKeyboardButton(reserve_text(), url=url)])
        buttons.append(
            [
                InlineKeyboardButton(
                    "◀ Назад к товарам",
                    callback_data=f"client:cat:{cat_id}",
                )
            ]
        )
        buttons.append(
            [InlineKeyboardButton("🏠 В НАЧАЛО", callback_data="client:back_main")]
        )
        kb = InlineKeyboardMarkup(buttons)

        chat_id = query.message.chat_id

        # по желанию: удаляем сообщение со списком, чтобы не висело
        try:
            await query.message.delete()
        except Exception:
            pass

        # берём первое фото товара
        ph = db_query(
            "SELECT file_id FROM photos WHERE product_id=? ORDER BY id LIMIT 1",
            (pid,),
        )

        if ph:
            await context.bot.send_photo(
                chat_id,
                photo=ph[0]["file_id"],
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
        else:
            await context.bot.send_message(
                chat_id,
                text=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )

        return



# Регистрация хендлеров
def register_client_handlers(app):
    app.add_handler(CallbackQueryHandler(client_menu_callback, pattern=r"^client:(categories|back_main)$"))
    app.add_handler(CallbackQueryHandler(client_categories_callback, pattern=r"^client:(back_main|cat:\d+)$"))
    app.add_handler(CallbackQueryHandler(client_products_callback, pattern=r"^client:(back_categories|product:\d+:\d+)$"))
    app.add_handler(CommandHandler("start", client_start_handler))

# Запуск бота
if __name__ == "__main__":
    register_client_handlers(app)
    app.run_polling()
