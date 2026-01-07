from __future__ import annotations

import io
import json
import logging
import re
import shutil
from contextlib import closing
from datetime import datetime
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db_layer import (
    db_query,
    db_exec,
    connect,
    DB_PATH,
    BASE,
    BACKUP_DIR,
    BACKUP_KEEP,
)

from core_helpers import (
    remember,
    replace_menu,
    clear_all,
    delete_scope,
    is_admin,
    has_perm,
    require_staff,
    public_links,
    style_link_text,
    load_links,
    save_links,
    get_setting,
    set_setting,
    reserve_enabled,
    reserve_text,
    reserve_url_for,
    touch_user,
)

log = logging.getLogger(__name__)


# ================== УТИЛИТЫ ДЛЯ КЛАВИАТУР / ТЕКСТА ==================

def shorten(t: str, n: int) -> str:
    t = (t or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def product_variants(pid: int):
    return db_query(
        "SELECT id,name,stock FROM product_variants WHERE product_id=? ORDER BY id",
        (pid,),
    )


def product_variant_info(pid: int) -> tuple[int, bool, list[str]]:
    rows = product_variants(pid)
    if not rows:
        return 0, False, []
    total = sum(r["stock"] for r in rows)
    lines = [f"• {r['name']} — {r['stock']}" for r in rows]
    return total, True, lines


def product_stock_lines(pid: int) -> tuple[list[str], int, bool]:
    total, has_variants, variant_lines = product_variant_info(pid)
    if has_variants:
        lines = ["Варианты:"] + variant_lines + [f"Итого: {total}"]
    else:
        lines = ["Варианты: нет."]
    return lines, total, has_variants


def caption_for(prod, cat_name: str) -> str:
    stock_lines, total_stock, _ = product_stock_lines(prod["id"])
    parts = [
        f"<b>{prod['name']}</b>",
        f"Категория: {cat_name}",
        *stock_lines,
    ]
    if total_stock <= 0:
        parts.append("<b>Нет в наличии</b>")
    return "\n".join(parts)


# ================== КЛАВИАТУРЫ: КЛИЕНТ ==================

def kb_home() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🛍 ОТКРЫТЬ ВИТРИНУ", callback_data="shop:cats:0")]
    ]
    for btn in public_links():
        rows.append(
            [
                InlineKeyboardButton(
                    style_link_text(btn["text"]),
                    url=btn["url"],
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("🧹 ОЧИСТИТЬ ЭКРАН", callback_data="shop:clear")]
    )
    return InlineKeyboardMarkup(rows)


def kb_cats(page: int = 0, per: int = 8) -> InlineKeyboardMarkup:
    cats = db_query("SELECT * FROM categories WHERE is_active=1 ORDER BY id")
    rows: list[list[InlineKeyboardButton]] = []
    start = page * per
    for c in cats[start : start + per]:
        rows.append(
            [
                InlineKeyboardButton(
                    c["name"], callback_data=f"shop:cat:{c['id']}:{page}"
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(
            InlineKeyboardButton("◀", callback_data=f"shop:cats:{max(0, page-1)}")
        )
    if start + per < len(cats):
        nav.append(InlineKeyboardButton("▶", callback_data=f"shop:cats:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 В НАЧАЛО", callback_data="shop:home")])
    return InlineKeyboardMarkup(rows)


# ================== КЛАВИАТУРЫ: АДМИН-ПАНЕЛЬ ==================

def kb_adm_home(is_admin_user: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("Категории", callback_data="adm:cats:0"),
            InlineKeyboardButton("Товары", callback_data="adm:prods:0"),
        ],
        [InlineKeyboardButton("Главные кнопки", callback_data="adm:links")],
        [InlineKeyboardButton("Приветствие", callback_data="adm:welcome")],
        [InlineKeyboardButton("Бронь", callback_data="adm:reserve")],
        [
            InlineKeyboardButton(
                "Данные (импорт/экспорт/бэкап)", callback_data="adm:data"
            )
        ],
    ]
    if is_admin_user:
        rows.insert(
            -1, [InlineKeyboardButton("Редакторы", callback_data="adm:editors")]
        )
    return InlineKeyboardMarkup(rows)


def kb_adm_cats(page: int = 0, per: int = 12) -> InlineKeyboardMarkup:
    cats = db_query("SELECT * FROM categories ORDER BY id")
    rows: list[list[InlineKeyboardButton]] = []
    start = page * per
    for c in cats[start : start + per]:
        label = f"{c['id']}. {shorten(c['name'], 28)}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"adm:cat:{c['id']}")]
        )
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(
            InlineKeyboardButton("◀", callback_data=f"adm:cats:{max(0, page-1)}")
        )
    if start + per < len(cats):
        nav.append(InlineKeyboardButton("▶", callback_data=f"adm:cats:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data="adm:cat:add")])
    rows.append([InlineKeyboardButton("◀ Меню", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)


def kb_adm_cat(cat_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "✏️ Переименовать", callback_data=f"adm:cat:rename:{cat_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🟢/⚫ Активность", callback_data=f"adm:cat:toggle:{cat_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 Удалить (со всеми товарами)",
                callback_data=f"adm:cat:delete:{cat_id}",
            )
        ],
        [InlineKeyboardButton("◀ Назад", callback_data="adm:cats:0")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_adm_prods_cats(page: int = 0, per: int = 12) -> InlineKeyboardMarkup:
    cats = db_query("SELECT * FROM categories ORDER BY id")
    rows: list[list[InlineKeyboardButton]] = []
    start = page * per
    for c in cats[start : start + per]:
        label = f"{c['id']}. {shorten(c['name'], 28)}"
        rows.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"adm:prods:cat:{c['id']}:0"
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(
            InlineKeyboardButton(
                "◀", callback_data=f"adm:prods:{max(0, page-1)}"
            )
        )
    if start + per < len(cats):
        nav.append(
            InlineKeyboardButton("▶", callback_data=f"adm:prods:{page+1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀ Меню", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)


def kb_adm_prods_list(page: int = 0, per: int = 10) -> InlineKeyboardMarkup:
    prods = db_query("SELECT * FROM products ORDER BY id")
    rows: list[list[InlineKeyboardButton]] = []
    start = page * per
    for p in prods[start : start + per]:
        mark = "🟢" if p["is_active"] else "⚫"
        total_stock, _, _ = product_variant_info(p["id"])
        name = (
            f"{mark} {p['id']}. {shorten(p['name'], 26)} [{total_stock}]"
        )
        rows.append(
            [InlineKeyboardButton(name, callback_data=f"adm:prod:{p['id']}")]
        )
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(
            InlineKeyboardButton(
                "◀", callback_data=f"adm:prods:{max(0, page-1)}"
            )
        )
    if start + per < len(prods):
        nav.append(
            InlineKeyboardButton("▶", callback_data=f"adm:prods:{page+1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("➕ Добавить товар", callback_data="adm:prod:add")])
    rows.append([InlineKeyboardButton("◀ Меню", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)


def kb_adm_prods(cat_id: int, page: int = 0, per: int = 10) -> InlineKeyboardMarkup:
    prods = db_query(
        "SELECT p.* FROM products p "
        "JOIN product_categories pc ON pc.product_id=p.id "
        "WHERE pc.category_id=? ORDER BY p.id",
        (cat_id,),
    )
    rows: list[list[InlineKeyboardButton]] = []
    start = page * per
    for p in prods[start : start + per]:
        mark = "🟢" if p["is_active"] else "⚫"
        total_stock, _, _ = product_variant_info(p["id"])
        name = (
            f"{mark} {p['id']}. {shorten(p['name'], 26)} [{total_stock}]"
        )
        rows.append(
            [InlineKeyboardButton(name, callback_data=f"adm:prod:{p['id']}")]
        )
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(
            InlineKeyboardButton(
                "◀",
                callback_data=f"adm:prods:cat:{cat_id}:{max(0, page-1)}",
            )
        )
    if start + per < len(prods):
        nav.append(
            InlineKeyboardButton(
                "▶", callback_data=f"adm:prods:cat:{cat_id}:{page+1}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                "➕ Добавить товар", callback_data=f"adm:prod:add:{cat_id}"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton("◀ Товары", callback_data="adm:prods:0")]
    )
    return InlineKeyboardMarkup(rows)


def kb_adm_prod(pid: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "✏️ Имя", callback_data=f"adm:prod:edit:name:{pid}"
            ),
            InlineKeyboardButton(
                "✏️ Описание", callback_data=f"adm:prod:edit:desc:{pid}"
            ),
        ],
        [
            InlineKeyboardButton(
                "🏷 Категории", callback_data=f"adm:prod:cats:edit:{pid}"
            ),
            InlineKeyboardButton(
                "🧩 Варианты", callback_data=f"adm:prod:variants:{pid}"
            )
        ],
        [
            InlineKeyboardButton(
                "🟢/⚫ Активность", callback_data=f"adm:prod:toggle:{pid}"
            )
        ],
        [
            InlineKeyboardButton(
                "🖼 Добавить фото", callback_data=f"adm:photo:add:{pid}"
            ),
            InlineKeyboardButton(
                "🧹 Удалить фото", callback_data=f"adm:photo:clear:{pid}"
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Удалить товар", callback_data=f"adm:prod:delete:{pid}"
            )
        ],
        [InlineKeyboardButton("◀ К списку", callback_data="adm:prods:0")],
    ]
    return InlineKeyboardMarkup(rows)


def product_categories(pid: int):
    return db_query(
        "SELECT c.id, c.name FROM categories c "
        "JOIN product_categories pc ON pc.category_id=c.id "
        "WHERE pc.product_id=? ORDER BY c.id",
        (pid,),
    )


def product_categories_label(pid: int) -> str:
    cats = product_categories(pid)
    names = ", ".join([c["name"] for c in cats])
    return names or "—"


def kb_adm_prod_variants(pid: int) -> InlineKeyboardMarkup:
    variants = product_variants(pid)
    rows: list[list[InlineKeyboardButton]] = []
    for v in variants:
        label = f"{v['id']}. {shorten(v['name'], 22)} [{v['stock']}]"
        rows.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"adm:variant:{pid}:{v['id']}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "➕ Добавить вариант", callback_data=f"adm:variant:add:{pid}"
            )
        ]
    )
    rows.append([InlineKeyboardButton("◀ Назад", callback_data=f"adm:prod:{pid}")])
    return InlineKeyboardMarkup(rows)


def kb_adm_variant(pid: int, vid: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "✏️ Имя", callback_data=f"adm:variant:edit:name:{pid}:{vid}"
            ),
            InlineKeyboardButton(
                "✏️ Остаток",
                callback_data=f"adm:variant:edit:stock:{pid}:{vid}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Удалить", callback_data=f"adm:variant:delete:{pid}:{vid}"
            )
        ],
        [InlineKeyboardButton("◀ Назад", callback_data=f"adm:prod:variants:{pid}")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_adm_prod_categories(pid: int, selected: set[int]) -> InlineKeyboardMarkup:
    cats = db_query("SELECT * FROM categories ORDER BY id")
    rows: list[list[InlineKeyboardButton]] = []
    for c in cats:
        mark = "✅" if c["id"] in selected else "⬜️"
        label = f"{mark} {c['id']}. {shorten(c['name'], 24)}"
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"adm:prod:cats:toggle:{pid}:{c['id']}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("✅ Готово", callback_data=f"adm:prod:cats:done:{pid}")]
    )
    rows.append(
        [InlineKeyboardButton("◀ Отмена", callback_data=f"adm:prod:{pid}")]
    )
    return InlineKeyboardMarkup(rows)


def product_text(pid: int) -> str:
    r = db_query("SELECT * FROM products WHERE id=?", (pid,))
    if not r:
        return "Товар не найден"
    p = r[0]
    stock_lines, _, _ = product_stock_lines(pid)
    lines = [
        f"<b>{p['name']}</b>",
        f"ID: {p['id']}  Категории: {product_categories_label(pid)}",
        *stock_lines,
        f"Активен: {bool(p['is_active'])}",
        "",
        p["description"] or "—",
    ]
    photo_count = db_query(
        "SELECT COUNT(*) AS cnt FROM photos WHERE product_id=?", (pid,)
    )[0]["cnt"]
    lines.append("")
    lines.append(f"Фото: {photo_count} шт.")
    return "\n".join(lines)


def kb_links_manage() -> InlineKeyboardMarkup:
    arr = load_links()
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("➕ Добавить", callback_data="adm:links:add")]
    ]
    for i, btn in enumerate(arr):
        state = "👁" if btn.get("active", 1) else "🚫"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{state} {shorten(btn['text'], 24)}",
                    callback_data=f"adm:links:edit:{i}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀ Меню", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)


def kb_link_edit(i: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "✏️ Текст", callback_data=f"adm:links:txt:{i}"
            ),
            InlineKeyboardButton(
                "🔗 URL", callback_data=f"adm:links:url:{i}"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔼 Вверх", callback_data=f"adm:links:up:{i}"
            ),
            InlineKeyboardButton(
                "🔽 Вниз", callback_data=f"adm:links:dn:{i}"
            ),
        ],
        [
            InlineKeyboardButton(
                "👁 Вкл/Выкл", callback_data=f"adm:links:toggle:{i}"
            ),
            InlineKeyboardButton(
                "🗑 Удалить", callback_data=f"adm:links:del:{i}"
            ),
        ],
        [InlineKeyboardButton("◀ Назад", callback_data="adm:links")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_data() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⬆ Импорт JSON/.db", callback_data="adm:data:import")],
        [InlineKeyboardButton("⬇ Экспорт JSON", callback_data="adm:data:export")],
        [
            InlineKeyboardButton(
                "💾 Бэкап DB и скачать", callback_data="adm:data:backup"
            )
        ],
        [
            InlineKeyboardButton(
                "📥 Скачать текущую DB",
                callback_data="adm:data:downloaddb",
            )
        ],
        [InlineKeyboardButton("◀ Меню", callback_data="adm:home")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_editor(uid: int, row) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{'ON' if row['is_active'] else 'OFF'} → Переключить",
                callback_data=f"adm:editor:toggle:{uid}",
            )
        ],
        [
            InlineKeyboardButton(
                f"Cats:{row['perm_cats']}",
                callback_data=f"adm:editor:perm:cats:{uid}",
            ),
            InlineKeyboardButton(
                f"Prods:{row['perm_prods']}",
                callback_data=f"adm:editor:perm:prods:{uid}",
            ),
            InlineKeyboardButton(
                f"Photos:{row['perm_photos']}",
                callback_data=f"adm:editor:perm:photos:{uid}",
            ),
        ],
        [
            InlineKeyboardButton(
                f"Links:{row['perm_links']}",
                callback_data=f"adm:editor:perm:links:{uid}",
            ),
            InlineKeyboardButton(
                f"Welcome:{row['perm_welcome']}",
                callback_data=f"adm:editor:perm:welcome:{uid}",
            ),
            InlineKeyboardButton(
                f"Reserve:{row['perm_reserve']}",
                callback_data=f"adm:editor:perm:reserve:{uid}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Удалить редактора",
                callback_data=f"adm:editor:del:{uid}",
            )
        ],
        [InlineKeyboardButton("◀ Назад", callback_data="adm:editors")],
    ]
    return InlineKeyboardMarkup(rows)


# ================== ВИДЫ / ЭКРАНЫ АДМИНА ==================

async def adm_open_cats(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    if not has_perm(update.effective_user.id, "cats"):
        return
    await replace_menu(
        update, context, "<b>Категории</b>", kb_adm_cats(page), scope="admin"
    )


async def adm_open_cat(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int):
    if not has_perm(update.effective_user.id, "cats"):
        return
    r = db_query("SELECT * FROM categories WHERE id=?", (cat_id,))
    if not r:
        await adm_open_cats(update, context, 0)
        return
    c = r[0]
    text = (
        f"<b>Категория {c['id']}</b>\n"
        f"{c['name']}\n"
        f"Активна: {bool(c['is_active'])}"
    )
    await replace_menu(update, context, text, kb_adm_cat(c["id"]), scope="admin")


async def adm_open_prods_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
):
    if not has_perm(update.effective_user.id, "prods"):
        return
    await replace_menu(
        update,
        context,
        "<b>Товары</b>",
        kb_adm_prods_list(page),
        scope="admin",
    )


async def adm_open_prods(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cat_id: int,
    page: int = 0,
):
    if not has_perm(update.effective_user.id, "prods"):
        return
    await replace_menu(
        update,
        context,
        f"<b>Товары категории {cat_id}</b>",
        kb_adm_prods(cat_id, page),
        scope="admin",
    )


async def adm_open_prod(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int
):
    if not has_perm(update.effective_user.id, "prods"):
        return
    if context.user_data.get("prod_cats_pid") == pid:
        context.user_data.pop("prod_cats_pid", None)
        context.user_data.pop("prod_cats_selected", None)

    await delete_scope(update, context, "admin")

    chat_id = update.effective_chat.id

    # грузим товар
    r = db_query("SELECT * FROM products WHERE id=?", (pid,))
    if not r:
        m = await context.bot.send_message(chat_id, "Товар не найден")
        await remember(update, m.message_id, "admin")
        return

    prod = r[0]
    cats_label = product_categories_label(pid)
    stock_lines, total_stock, _ = product_stock_lines(pid)

    # 1) сначала все фото медиагруппой
    photos = db_query(
        "SELECT file_id FROM photos WHERE product_id=? ORDER BY id", (pid,)
    )
    if photos:
        media = [InputMediaPhoto(ph["file_id"]) for ph in photos[:10]]
        try:
            msgs = await context.bot.send_media_group(chat_id, media)
            for m in msgs:
                await remember(update, m.message_id, "admin")
        except Exception as e:
            log.warning("admin photos preview failed: %s", e)

    # 2) затем текст + кнопки
    lines = [
        f"<b>{prod['name']}</b>",
        f"Категории: {cats_label}",
        *stock_lines,
    ]
    if total_stock <= 0:
        lines.append("<b>Нет в наличии</b>")
    # ВАЖНО: без .get, только через индексацию
    if prod["description"]:
        lines.append("")
        lines.append(prod["description"])

    text = "\n".join(lines)

    kb = kb_adm_prod(pid)

    msg = await context.bot.send_message(
        chat_id,
        text,
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    await remember(update, msg.message_id, "admin")


async def adm_open_prod_variants(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int
):
    if not has_perm(update.effective_user.id, "prods"):
        return
    total_stock, has_variants, variant_lines = product_variant_info(pid)
    lines = [f"<b>Варианты товара {pid}</b>"]
    if has_variants:
        lines.extend(variant_lines)
        lines.append(f"Итого: {total_stock}")
    else:
        lines.append("Список пуст.")
    await replace_menu(
        update,
        context,
        "\n".join(lines),
        kb_adm_prod_variants(pid),
        scope="admin",
    )


async def adm_open_variant(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int, vid: int
):
    if not has_perm(update.effective_user.id, "prods"):
        return
    rows = db_query(
        "SELECT * FROM product_variants WHERE id=? AND product_id=?",
        (vid, pid),
    )
    if not rows:
        await adm_open_prod_variants(update, context, pid)
        return
    v = rows[0]
    text = (
        f"<b>Вариант {v['id']}</b>\n"
        f"Название: {v['name']}\n"
        f"Остаток: {v['stock']}"
    )
    await replace_menu(
        update, context, text, kb_adm_variant(pid, vid), scope="admin"
    )

async def adm_open_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_perm(update.effective_user.id, "links"):
        return
    await replace_menu(
        update,
        context,
        "<b>Главные кнопки</b>",
        kb_links_manage(),
        scope="admin",
    )


async def adm_open_reserve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_perm(update.effective_user.id, "reserve"):
        return
    prod = db_query(
        "SELECT * FROM products WHERE is_active=1 ORDER BY id LIMIT 1"
    )
    sample = "—"
    if prod:
        try:
            sample = reserve_url_for(prod[0]) or "—"
        except Exception:
            sample = "—"
    text = (
        "<b>Бронь</b>\n"
        "Режим: Telegram\n"
        f"Текст кнопки: {reserve_text()}\n"
        f"Username/ссылка: {get_setting('reserve_tg_username') or ''}\n"
        f"Шаблон: {get_setting('reserve_msg_tpl') or ''}\n"
        "Шаблоны: {id} {name} {price}\n"
        f"Пример: {sample}"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                f"Состояние: {'ON' if reserve_enabled() else 'OFF'} → Переключить",
                callback_data="adm:reserve:toggle",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Текст кнопки", callback_data="adm:reserve:text"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Telegram username/ссылка",
                callback_data="adm:reserve:username",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Шаблон сообщения", callback_data="adm:reserve:tpl"
            )
        ],
    ]
    rows.append([InlineKeyboardButton("◀ Меню", callback_data="adm:home")])
    await replace_menu(
        update, context, text, InlineKeyboardMarkup(rows), scope="admin"
    )


async def adm_open_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await replace_menu(
        update,
        context,
        "<b>Данные</b>\nИмпорт, экспорт, бэкап.",
        kb_data(),
        scope="admin",
    )


async def adm_open_editors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = db_query("SELECT * FROM editors ORDER BY user_id")
    if rows:
        lines = ["<b>Редакторы</b>"]
        for r in rows:
            uname = r["username"] or ""
            line = (
                f"{r['user_id']} ({uname}) — "
                f"{'ON' if r['is_active'] else 'OFF'} "
                f"[C:{r['perm_cats']} P:{r['perm_prods']} "
                f"F:{r['perm_photos']} L:{r['perm_links']} "
                f"W:{r['perm_welcome']} R:{r['perm_reserve']}]"
            )
            lines.append(line)
    else:
        lines = ["<b>Редакторы</b>", "Список пуст"]
    kb_rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("➕ Добавить", callback_data="adm:editor:add")]
    ]
    for r in rows:
        uname = r["username"] or ""
        kb_rows.append(
            [
                InlineKeyboardButton(
                    f"⚙ {r['user_id']} ({uname})",
                    callback_data=f"adm:editor:edit:{r['user_id']}",
                )
            ]
        )
    kb_rows.append([InlineKeyboardButton("◀ Меню", callback_data="adm:home")])
    await replace_menu(
        update,
        context,
        "\n".join(lines),
        InlineKeyboardMarkup(kb_rows),
        scope="admin",
    )


async def adm_open_editor(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    if not is_admin(update.effective_user.id):
        return
    r = db_query("SELECT * FROM editors WHERE user_id=?", (uid,))
    if not r:
        await adm_open_editors(update, context)
        return
    row = r[0]
    text = (
        f"<b>Редактор {uid}</b>\n"
        f"Статус: {'ON' if row['is_active'] else 'OFF'}\n"
        f"Права: C:{row['perm_cats']} P:{row['perm_prods']} "
        f"F:{row['perm_photos']} L:{row['perm_links']} "
        f"W:{row['perm_welcome']} R:{row['perm_reserve']}"
    )
    await replace_menu(update, context, text, kb_editor(uid, row), scope="admin")


# ================== ИМПОРТ / ЭКСПОРТ / БЭКАП ==================

def export_json() -> dict:
    cats = db_query("SELECT * FROM categories ORDER BY id")
    prods = db_query("SELECT * FROM products ORDER BY id")
    photos = db_query("SELECT product_id,file_id FROM photos ORDER BY id")
    prod_cats = db_query(
        "SELECT product_id,category_id FROM product_categories ORDER BY product_id"
    )
    variants = db_query(
        "SELECT * FROM product_variants ORDER BY product_id,id"
    )
    return {
        "categories": [dict(r) for r in cats],
        "products": [dict(r) for r in prods],
        "photos": [dict(r) for r in photos],
        "product_categories": [dict(r) for r in prod_cats],
        "product_variants": [dict(r) for r in variants],
    }


def import_json(data: dict):
    cats = data.get("categories", [])
    prods = data.get("products", [])
    photos = data.get("photos", [])
    prod_cats = data.get("product_categories", [])
    variants = data.get("product_variants", [])
    with closing(connect()) as c:
        cur = c.cursor()
        cur.execute("BEGIN")
        try:
            for cat in cats:
                cur.execute(
                    """INSERT INTO categories(id,name,is_active)
                       VALUES(?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                           name=excluded.name,
                           is_active=excluded.is_active""",
                    (cat.get("id"), cat["name"], int(cat.get("is_active", 1))),
                )
            for p in prods:
                cur.execute(
                    """INSERT INTO products(
                           id,category_id,name,description,
                           price,stock,is_active,created_at
                       )
                       VALUES(?,?,?,?,?,?,?,COALESCE(?, datetime('now')))
                       ON CONFLICT(id) DO UPDATE SET
                           category_id=excluded.category_id,
                           name=excluded.name,
                           description=excluded.description,
                           price=excluded.price,
                           stock=excluded.stock,
                           is_active=excluded.is_active""",
                    (
                        p.get("id"),
                        p.get("category_id"),
                        p["name"],
                        p.get("description", ""),
                        int(p.get("price", 0)),
                        int(p.get("stock", 0)),
                        int(p.get("is_active", 1)),
                        p.get("created_at"),
                    ),
                )
            if not prod_cats:
                for p in prods:
                    cat_id = p.get("category_id")
                    if cat_id is None:
                        continue
                    prod_cats.append(
                        {"product_id": p.get("id"), "category_id": cat_id}
                    )
            for rel in prod_cats:
                cur.execute(
                    """INSERT INTO product_categories(product_id,category_id)
                       VALUES(?,?)
                       ON CONFLICT(product_id,category_id) DO NOTHING""",
                    (rel["product_id"], rel["category_id"]),
                )
            for var in variants:
                cur.execute(
                    """INSERT INTO product_variants(
                           id,product_id,name,stock
                       ) VALUES(?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                           product_id=excluded.product_id,
                           name=excluded.name,
                           stock=excluded.stock""",
                    (
                        var.get("id"),
                        var["product_id"],
                        var["name"],
                        int(var.get("stock", 0)),
                    ),
                )
            for ph in photos:
                cur.execute(
                    "INSERT INTO photos(product_id,file_id) VALUES(?,?)",
                    (ph["product_id"], ph["file_id"]),
                )
            c.commit()
        except Exception:
            c.rollback()
            raise


# ================== ВИТРИНА ДЛЯ КЛИЕНТА (GRID + ТОВАР) ==================

async def show_shop_grid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cat_id: int,
    page: int = 0,
):
    await delete_scope(update, context, "shop")
    prods = db_query(
        "SELECT p.* FROM products p "
        "JOIN product_categories pc ON pc.product_id=p.id "
        "WHERE p.is_active=1 AND pc.category_id=? ORDER BY p.id",
        (cat_id,),
    )
    chat_id = update.effective_chat.id
    per = 4
    total = len(prods)
    if total == 0:
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("◀ Категории", callback_data="shop:cats:0")],
                [InlineKeyboardButton("🏠 В НАЧАЛО", callback_data="shop:home")],
            ]
        )
        m = await context.bot.send_message(
            chat_id,
            "В этой категории пока нет активных товаров.",
            reply_markup=kb,
        )
        await remember(update, m.message_id, "shop")
        return
    max_page = (total - 1) // per
    if page < 0:
        page = 0
    if page > max_page:
        page = max_page
    start = page * per
    items = prods[start : start + per]
    for p in items:
        pid = p["id"]
        stock_lines, total_stock, _ = product_stock_lines(pid)
        ph = db_query(
            "SELECT file_id FROM photos WHERE product_id=? ORDER BY id LIMIT 1",
            (pid,),
        )
        lines = [
            f"<b>{p['name']}</b>",
            *stock_lines,
        ]
        if total_stock <= 0:
            lines.append("<b>Нет в наличии</b>")
        text = "\n".join(lines)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Подробнее", callback_data=f"shop:prod:{cat_id}:{pid}"
                    )
                ]
            ]
        )
        if ph:
            msg = await context.bot.send_photo(
                chat_id,
                photo=ph[0]["file_id"],
                caption=text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
        else:
            msg = await context.bot.send_message(
                chat_id,
                text=text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
        await remember(update, msg.message_id, "shop")
    nav_buttons: list[list[InlineKeyboardButton]] = []
    row_nav: list[InlineKeyboardButton] = []
    if page > 0:
        row_nav.append(
            InlineKeyboardButton("◀", callback_data=f"shop:cat:{cat_id}:{page-1}")
        )
    if page < max_page:
        row_nav.append(
            InlineKeyboardButton("▶", callback_data=f"shop:cat:{cat_id}:{page+1}")
        )
    if row_nav:
        nav_buttons.append(row_nav)
    nav_buttons.append(
        [InlineKeyboardButton("◀ Категории", callback_data="shop:cats:0")]
    )
    nav_buttons.append(
        [InlineKeyboardButton("🏠 В НАЧАЛО", callback_data="shop:home")]
    )
    nav = await context.bot.send_message(
        chat_id,
        "Навигация по товарам:",
        reply_markup=InlineKeyboardMarkup(nav_buttons),
    )
    await remember(update, nav.message_id, "shop")


async def show_shop_product(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int, pid: int
):
    await delete_scope(update, context, "shop")

    r = db_query("SELECT * FROM products WHERE id=?", (pid,))
    chat_id = update.effective_chat.id
    if not r:
        m = await context.bot.send_message(chat_id, "Товар не найден")
        await remember(update, m.message_id, "shop")
        return

    prod = r[0]
    cats_label = product_categories_label(pid)
    stock_lines, total_stock, _ = product_stock_lines(pid)

    # --- текст карточки ---
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
    details = "\n".join(lines)

    # --- кнопки ---
    rows: list[list[InlineKeyboardButton]] = []
    url = reserve_url_for(prod)
    if url:
        rows.append([InlineKeyboardButton(reserve_text(), url=url)])
    rows.append(
        [
            InlineKeyboardButton(
                "◀ Назад", callback_data=f"shop:cat:{cat_id}:0"
            )
        ]
    )
    rows.append([InlineKeyboardButton("🏠 В НАЧАЛО", callback_data="shop:home")])
    kb = InlineKeyboardMarkup(rows)

    # --- первое фото товара, как в show_shop_grid ---
    ph = db_query(
        "SELECT file_id FROM photos WHERE product_id=? ORDER BY id LIMIT 1",
        (pid,),
    )

    if ph:
        msg = await context.bot.send_photo(
            chat_id,
            photo=ph[0]["file_id"],
            caption=details,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    else:
        msg = await context.bot.send_message(
            chat_id,
            text=details,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )

    await remember(update, msg.message_id, "shop")

# ================== CALLBACK-РОУТЕР (shop + adm) ==================

async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    await q.answer()

    # ---------- Клиентская часть ----------

    if data == "shop:home":
        await delete_scope(update, context, "shop")
        await delete_scope(update, context, "home")
        text = get_setting("welcome_html") or "Каталог. Нажмите «Открыть витрину»."
        await replace_menu(update, context, text, kb_home(), scope="home")
        return

    if data == "shop:clear":
        await clear_all(update, context)
        return

    if data.startswith("shop:cats:"):
        page = int(data.split(":")[2])
        await delete_scope(update, context, "shop")
        await replace_menu(
            update, context, "Выберите категорию:", kb_cats(page), scope="shop"
        )
        return

    if data.startswith("shop:cat:"):
        parts = data.split(":")
        cat_id = int(parts[2])
        page = int(parts[3])
        await show_shop_grid(update, context, cat_id, page)
        return

    if data.startswith("shop:prod:"):
        parts = data.split(":")
        cat_id = int(parts[2])
        pid = int(parts[3])
        await show_shop_product(update, context, cat_id, pid)
        return

    # ---------- всё, что ниже, только для админа/редактора ----------

    if data.startswith("adm:") and not require_staff(update):
        return

    if data == "adm:home":
        await replace_menu(
            update,
            context,
            "<b>Панель</b>",
            kb_adm_home(is_admin(update.effective_user.id)),
            scope="admin",
        )
        return

    # --- Категории ---

    if data.startswith("adm:cats:"):
        if not has_perm(update.effective_user.id, "cats"):
            return
        page = int(data.split(":")[2])
        await adm_open_cats(update, context, page)
        return

    if data == "adm:cat:add":
        if not has_perm(update.effective_user.id, "cats"):
            return
        context.user_data["await_cat_add"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:cats:0")]]
        )
        await replace_menu(update, context, "Название категории:", kb, scope="admin")
        return

    if data.startswith("adm:cat:rename:"):
        if not has_perm(update.effective_user.id, "cats"):
            return
        cid = int(data.split(":")[3])
        context.user_data["await_cat_rename"] = cid
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:cats:0")]]
        )
        await replace_menu(
            update, context, "Новое имя категории:", kb, scope="admin"
        )
        return

    if data.startswith("adm:cat:toggle:"):
        if not has_perm(update.effective_user.id, "cats"):
            return
        cid = int(data.split(":")[3])
        r = db_query("SELECT * FROM categories WHERE id=?", (cid,))
        if r:
            db_exec(
                "UPDATE categories SET is_active=? WHERE id=?",
                (0 if r[0]["is_active"] else 1, cid),
            )
        await adm_open_cat(update, context, cid)
        return

    if data.startswith("adm:cat:delete:ok:"):
        if not has_perm(update.effective_user.id, "cats"):
            return
        cid = int(data.split(":")[4])
        db_exec("DELETE FROM product_categories WHERE category_id=?", (cid,))
        db_exec("DELETE FROM categories WHERE id=?", (cid,))
        await adm_open_cats(update, context, 0)
        return

    if data.startswith("adm:cat:delete:"):
        if not has_perm(update.effective_user.id, "cats"):
            return
        cid = int(data.split(":")[3])
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❗ Да, удалить с товарами",
                        callback_data=f"adm:cat:delete:ok:{cid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Отмена", callback_data=f"adm:cat:{cid}"
                    )
                ],
            ]
        )
        await replace_menu(
            update,
            context,
            "Удалить категорию и связи с товарами?",
            kb,
            scope="admin",
        )
        return

    if data.startswith("adm:cat:"):
        if not has_perm(update.effective_user.id, "cats"):
            return
        cid = int(data.split(":")[2])
        await adm_open_cat(update, context, cid)
        return

    # --- Товары ---

    if data.startswith("adm:prods:cat:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        parts = data.split(":")
        cat_id = int(parts[3])
        page = int(parts[4])
        await adm_open_prods(update, context, cat_id, page)
        return

    if data.startswith("adm:prods:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        page = int(data.split(":")[2])
        await adm_open_prods_list(update, context, page)
        return

    if data.startswith("adm:prod:add"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        parts = data.split(":")
        cat_id = int(parts[3]) if len(parts) > 3 else None
        back_cb = (
            f"adm:prods:cat:{cat_id}:0" if cat_id is not None else "adm:prods:0"
        )
        context.user_data["await_prod_name"] = True
        context.user_data["prod_add_back"] = back_cb
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data=back_cb)]]
        )
        await replace_menu(update, context, "Имя товара:", kb, scope="admin")
        return

    if data.startswith("adm:prod:edit:name:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        context.user_data["await_prod_name_edit"] = pid
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data=f"adm:prod:{pid}")]]
        )
        await replace_menu(update, context, "Новое имя:", kb, scope="admin")
        return

    if data.startswith("adm:prod:edit:desc:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        context.user_data["await_prod_desc_edit"] = pid
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data=f"adm:prod:{pid}")]]
        )
        await replace_menu(
            update, context, "Новое описание:", kb, scope="admin"
        )
        return

    if data.startswith("adm:prod:variants:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[3])
        await adm_open_prod_variants(update, context, pid)
        return

    if data.startswith("adm:prod:toggle:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[3])
        r = db_query("SELECT * FROM products WHERE id=?", (pid,))
        if r:
            db_exec(
                "UPDATE products SET is_active=? WHERE id=?",
                (0 if r[0]["is_active"] else 1, pid),
            )
        await adm_open_prod(update, context, pid)
        return

    if data.startswith("adm:prod:delete:ok:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        db_exec("DELETE FROM products WHERE id=?", (pid,))
        await adm_open_prods_list(update, context, 0)
        return

    if data.startswith("adm:prod:cats:toggle:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        parts = data.split(":")
        pid = int(parts[4])
        cat_id = int(parts[5])
        if context.user_data.get("prod_cats_pid") != pid:
            context.user_data["prod_cats_pid"] = pid
            context.user_data["prod_cats_selected"] = {
                row["id"] for row in product_categories(pid)
            }
        selected = context.user_data.get("prod_cats_selected", set())
        if cat_id in selected:
            selected.remove(cat_id)
        else:
            selected.add(cat_id)
        context.user_data["prod_cats_selected"] = selected
        await replace_menu(
            update,
            context,
            "Выберите категории для товара (можно несколько):",
            kb_adm_prod_categories(pid, selected),
            scope="admin",
        )
        return

    if data.startswith("adm:prod:cats:edit:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        selected = {row["id"] for row in product_categories(pid)}
        context.user_data["prod_cats_pid"] = pid
        context.user_data["prod_cats_selected"] = selected
        await replace_menu(
            update,
            context,
            "Выберите категории для товара (можно несколько):",
            kb_adm_prod_categories(pid, selected),
            scope="admin",
        )
        return

    if data.startswith("adm:prod:cats:done:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        if context.user_data.get("prod_cats_pid") != pid:
            context.user_data["prod_cats_selected"] = {
                row["id"] for row in product_categories(pid)
            }
        selected = context.user_data.get("prod_cats_selected", set())
        db_exec("DELETE FROM product_categories WHERE product_id=?", (pid,))
        for cat_id in sorted(selected):
            db_exec(
                "INSERT INTO product_categories(product_id,category_id) VALUES(?,?)",
                (pid, cat_id),
            )
        context.user_data.pop("prod_cats_pid", None)
        context.user_data.pop("prod_cats_selected", None)
        await adm_open_prod(update, context, pid)
        return

    if data.startswith("adm:prod:delete:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[3])
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❗ Да, удалить товар",
                        callback_data=f"adm:prod:delete:ok:{pid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Отмена", callback_data=f"adm:prod:{pid}"
                    )
                ],
            ]
        )
        await replace_menu(
            update, context, "Удалить товар?", kb, scope="admin"
        )
        return

    if data.startswith("adm:variant:add:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[3])
        context.user_data["await_variant_name"] = pid
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data=f"adm:prod:variants:{pid}")]]
        )
        await replace_menu(
            update, context, "Название варианта:", kb, scope="admin"
        )
        return

    if data.startswith("adm:variant:edit:name:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        vid = int(data.split(":")[5])
        context.user_data["await_variant_name_edit"] = (pid, vid)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data=f"adm:variant:{pid}:{vid}")]]
        )
        await replace_menu(
            update, context, "Новое название варианта:", kb, scope="admin"
        )
        return

    if data.startswith("adm:variant:edit:stock:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        vid = int(data.split(":")[5])
        context.user_data["await_variant_stock_edit"] = (pid, vid)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data=f"adm:variant:{pid}:{vid}")]]
        )
        await replace_menu(
            update, context, "Новый остаток (число):", kb, scope="admin"
        )
        return

    if data.startswith("adm:variant:delete:ok:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[4])
        vid = int(data.split(":")[5])
        db_exec(
            "DELETE FROM product_variants WHERE id=? AND product_id=?",
            (vid, pid),
        )
        await adm_open_prod_variants(update, context, pid)
        return

    if data.startswith("adm:variant:delete:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[3])
        vid = int(data.split(":")[4])
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❗ Да, удалить вариант",
                        callback_data=f"adm:variant:delete:ok:{pid}:{vid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Отмена", callback_data=f"adm:variant:{pid}:{vid}"
                    )
                ],
            ]
        )
        await replace_menu(
            update, context, "Удалить вариант?", kb, scope="admin"
        )
        return

    if data.startswith("adm:variant:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[2])
        vid = int(data.split(":")[3])
        await adm_open_variant(update, context, pid, vid)
        return

    if data.startswith("adm:prod:"):
        if not has_perm(update.effective_user.id, "prods"):
            return
        pid = int(data.split(":")[2])
        await adm_open_prod(update, context, pid)
        return

    # --- Фото ---

    if data.startswith("adm:photo:add:"):
        if not has_perm(update.effective_user.id, "photos"):
            return
        pid = int(data.split(":")[3])
        context.user_data["await_addphoto_pid"] = pid
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Готово", callback_data=f"adm:photo:done:{pid}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "◀ Назад", callback_data=f"adm:prod:{pid}"
                    )
                ],
            ]
        )
        await replace_menu(
            update,
            context,
            "Пришлите 1–10 фото, затем нажмите «Готово».",
            kb,
            scope="admin",
        )
        return

    if data.startswith("adm:photo:done:"):
        if not has_perm(update.effective_user.id, "photos"):
            return
        pid = int(data.split(":")[3])
        context.user_data.pop("await_addphoto_pid", None)
        await adm_open_prod(update, context, pid)
        return

    if data.startswith("adm:photo:clear:"):
        if not has_perm(update.effective_user.id, "photos"):
            return
        pid = int(data.split(":")[3])
        db_exec("DELETE FROM photos WHERE product_id=?", (pid,))
        await adm_open_prod(update, context, pid)
        return

    # --- Главные кнопки ---

    if data == "adm:links":
        if not has_perm(update.effective_user.id, "links"):
            return
        await adm_open_links(update, context)
        return

    if data == "adm:links:add":
        if not has_perm(update.effective_user.id, "links"):
            return
        context.user_data["await_link_text"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:links")]]
        )
        await replace_menu(
            update, context, "Текст кнопки:", kb, scope="admin"
        )
        return

    if data.startswith("adm:links:edit:"):
        if not has_perm(update.effective_user.id, "links"):
            return
        idx = int(data.split(":")[3])
        arr = load_links()
        if not (0 <= idx < len(arr)):
            await adm_open_links(update, context)
            return
        btn = arr[idx]
        text = (
            f"<b>Кнопка {idx+1}</b>\n"
            f"Текст: {btn['text']}\n"
            f"URL: {btn['url']}\n"
            f"Активна: {bool(btn.get('active', 1))}"
        )
        await replace_menu(
            update,
            context,
            text,
            kb_link_edit(idx),
            scope="admin",
        )
        return

    if data.startswith("adm:links:txt:"):
        if not has_perm(update.effective_user.id, "links"):
            return
        i = int(data.split(":")[3])
        context.user_data["await_link_txt_i"] = i
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "◀ Назад", callback_data=f"adm:links:edit:{i}"
                    )
                ]
            ]
        )
        await replace_menu(
            update, context, "Новый текст:", kb, scope="admin"
        )
        return

    if data.startswith("adm:links:url:"):
        if not has_perm(update.effective_user.id, "links"):
            return
        i = int(data.split(":")[3])
        context.user_data["await_link_url_i"] = i
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "◀ Назад", callback_data=f"adm:links:edit:{i}"
                    )
                ]
            ]
        )
        await replace_menu(
            update, context, "Новый URL (http/https):", kb, scope="admin"
        )
        return

    if data.startswith("adm:links:up:"):
        if not has_perm(update.effective_user.id, "links"):
            return
        i = int(data.split(":")[3])
        arr = load_links()
        if 0 < i < len(arr):
            arr[i - 1], arr[i] = arr[i], arr[i - 1]
            save_links(arr)
        await adm_open_links(update, context)
        return

    if data.startswith("adm:links:dn:"):
        if not has_perm(update.effective_user.id, "links"):
            return
        i = int(data.split(":")[3])
        arr = load_links()
        if 0 <= i < len(arr) - 1:
            arr[i + 1], arr[i] = arr[i], arr[i + 1]
            save_links(arr)
        await adm_open_links(update, context)
        return

    if data.startswith("adm:links:toggle:"):
        if not has_perm(update.effective_user.id, "links"):
            return
        i = int(data.split(":")[3])
        arr = load_links()
        if 0 <= i < len(arr):
            arr[i]["active"] = 0 if arr[i].get("active", 1) else 1
            save_links(arr)
        await adm_open_links(update, context)
        return

    if data.startswith("adm:links:del:"):
        if not has_perm(update.effective_user.id, "links"):
            return
        i = int(data.split(":")[3])
        arr = load_links()
        if 0 <= i < len(arr):
            arr.pop(i)
            save_links(arr)
        await adm_open_links(update, context)
        return

    # --- Приветствие ---

    if data == "adm:welcome":
        if not has_perm(update.effective_user.id, "welcome"):
            return
        context.user_data["await_welcome"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Меню", callback_data="adm:home")]]
        )
        await replace_menu(
            update,
            context,
            "Введите приветственное сообщение (HTML):",
            kb,
            scope="admin",
        )
        return

    # --- Бронь ---

    if data == "adm:reserve":
        if not has_perm(update.effective_user.id, "reserve"):
            return
        await adm_open_reserve(update, context)
        return

    if data == "adm:reserve:toggle":
        if not has_perm(update.effective_user.id, "reserve"):
            return
        set_setting("reserve_enabled", "0" if reserve_enabled() else "1")
        await adm_open_reserve(update, context)
        return

    if data == "adm:reserve:text":
        if not has_perm(update.effective_user.id, "reserve"):
            return
        context.user_data["await_reserve_text"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:reserve")]]
        )
        await replace_menu(
            update,
            context,
            "Новый текст кнопки брони:",
            kb,
            scope="admin",
        )
        return

    if data == "adm:reserve:username":
        if not has_perm(update.effective_user.id, "reserve"):
            return
        context.user_data["await_reserve_username"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:reserve")]]
        )
        await replace_menu(
            update,
            context,
            "Введите Telegram username или ссылку t.me:",
            kb,
            scope="admin",
        )
        return

    if data == "adm:reserve:tpl":
        if not has_perm(update.effective_user.id, "reserve"):
            return
        context.user_data["await_reserve_tpl"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:reserve")]]
        )
        await replace_menu(
            update,
            context,
            "Шаблон сообщения. Доступны {id},{name},{price}:",
            kb,
            scope="admin",
        )
        return

    # --- Данные (импорт/экспорт/бэкап) ---

    if data == "adm:data":
        if not is_admin(update.effective_user.id):
            return
        await adm_open_data(update, context)
        return

    if data == "adm:data:import":
        if not is_admin(update.effective_user.id):
            return
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Меню", callback_data="adm:data")]]
        )
        await replace_menu(
            update,
            context,
            "Отправьте .json или .db файлом в этот чат.",
            kb,
            scope="admin",
        )
        return

    if data == "adm:data:export":
        if not is_admin(update.effective_user.id):
            return
        data_json = json.dumps(export_json(), ensure_ascii=False, indent=2)
        bio = io.BytesIO(data_json.encode("utf-8"))
        bio.name = "catalog-export.json"
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=bio,
            caption="Экспорт JSON",
        )
        return

    if data == "adm:data:backup":
        if not is_admin(update.effective_user.id):
            return
        bdir = BASE / BACKUP_DIR
        bdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = bdir / f"catalog-{ts}.db"
        shutil.copy2(DB_PATH, dst)
        files = sorted(
            [p for p in bdir.glob("catalog-*.db")],
            key=lambda p: p.name,
            reverse=True,
        )
        for p in files[BACKUP_KEEP:]:
            try:
                p.unlink()
            except Exception:
                pass
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open(dst, "rb"),
            caption=f"Бэкап {dst.name}",
        )
        return

    if data == "adm:data:downloaddb":
        if not is_admin(update.effective_user.id):
            return
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open(DB_PATH, "rb"),
            caption="Текущая база данных",
        )
        return

    # --- Редакторы ---

    if data == "adm:editors":
        if not is_admin(update.effective_user.id):
            return
        await adm_open_editors(update, context)
        return

    if data == "adm:editor:add":
        if not is_admin(update.effective_user.id):
            return
        context.user_data["await_editor_add"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:editors")]]
        )
        await replace_menu(
            update,
            context,
            "Введите @username или числовой Telegram ID редактора:",
            kb,
            scope="admin",
        )
        return

    if data.startswith("adm:editor:edit:"):
        if not is_admin(update.effective_user.id):
            return
        uid = int(data.split(":")[3])
        await adm_open_editor(update, context, uid)
        return

    if data.startswith("adm:editor:toggle:"):
        if not is_admin(update.effective_user.id):
            return
        uid = int(data.split(":")[3])
        r = db_query("SELECT * FROM editors WHERE user_id=?", (uid,))
        if r:
            db_exec(
                "UPDATE editors SET is_active=? WHERE user_id=?",
                (0 if r[0]["is_active"] else 1, uid),
            )
        await adm_open_editor(update, context, uid)
        return

    if data.startswith("adm:editor:perm:"):
        if not is_admin(update.effective_user.id):
            return
        _, _, _, perm, uid_str = data.split(":")
        uid = int(uid_str)
        col_map = {
            "cats": "perm_cats",
            "prods": "perm_prods",
            "photos": "perm_photos",
            "links": "perm_links",
            "welcome": "perm_welcome",
            "reserve": "perm_reserve",
        }
        col = col_map[perm]
        r = db_query(f"SELECT {col} FROM editors WHERE user_id=?", (uid,))
        if r:
            new_val = 0 if r[0][col] else 1
            db_exec(
                f"UPDATE editors SET {col}=? WHERE user_id=?",
                (new_val, uid),
            )
        await adm_open_editor(update, context, uid)
        return

    if data.startswith("adm:editor:del:"):
        if not is_admin(update.effective_user.id):
            return
        uid = int(data.split(":")[3])
        db_exec("DELETE FROM editors WHERE user_id=?", (uid,))
        await adm_open_editors(update, context)
        return


# ================== HANDLER ТЕКСТА ДЛЯ АДМИНА ==================

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    if (get_setting("auto_delete_user") or "0") == "1" and getattr(
        update, "message", None
    ):
        try:
            await update.message.delete()
        except Exception:
            pass

    if not require_staff(update):
        return

    uid = update.effective_user.id
    text = (update.message.text or "").strip()

    # прекращение режима добавления фото словом "готово" и т.п.
    if context.user_data.get("await_addphoto_pid") and text.lower() in ("готово", "готово!", "done"):
        pid = context.user_data.pop("await_addphoto_pid")
        await adm_open_prod(update, context, pid)
        return

    # --- приветствие ---

    if context.user_data.pop("await_welcome", False):
        if not has_perm(uid, "welcome"):
            return
        set_setting("welcome_html", text or "Каталог.")
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Меню", callback_data="adm:home")]]
        )
        await replace_menu(update, context, "Сохранено.", kb, scope="admin")
        return

    # --- добавление главной кнопки (текст -> URL) ---

    if context.user_data.pop("await_link_text", False):
        if not has_perm(uid, "links"):
            return
        context.user_data["link_text"] = text
        context.user_data["await_link_url"] = True
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="adm:links")]]
        )
        await replace_menu(
            update,
            context,
            "URL ссылки (http/https):",
            kb,
            scope="admin",
        )
        return

    if context.user_data.pop("await_link_url", False):
        if not has_perm(uid, "links"):
            return
        if not re.match(r"^https?://", text):
            context.user_data["await_link_url"] = True
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ Назад", callback_data="adm:links")]]
            )
            await replace_menu(
                update,
                context,
                "Нужен http/https URL:",
                kb,
                scope="admin",
            )
            return
        arr = load_links()
        arr.append(
            {
                "text": context.user_data.pop("link_text", "Ссылка"),
                "url": text,
                "active": 1,
            }
        )
        save_links(arr)
        await adm_open_links(update, context)
        return

    # --- редактирование существующих кнопок ---

    i = context.user_data.pop("await_link_txt_i", None)
    if i is not None:
        if not has_perm(uid, "links"):
            return
        arr = load_links()
        if 0 <= i < len(arr):
            arr[i]["text"] = text
            save_links(arr)
        await adm_open_links(update, context)
        return

    i = context.user_data.pop("await_link_url_i", None)
    if i is not None:
        if not has_perm(uid, "links"):
            return
        if not re.match(r"^https?://", text):
            context.user_data["await_link_url_i"] = i
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "◀ Назад",
                            callback_data=f"adm:links:edit:{i}",
                        )
                    ]
                ]
            )
            await replace_menu(
                update,
                context,
                "Нужен http/https URL:",
                kb,
                scope="admin",
            )
            return
        arr = load_links()
        if 0 <= i < len(arr):
            arr[i]["url"] = text
            save_links(arr)
        await adm_open_links(update, context)
        return

    # --- категории ---

    if context.user_data.pop("await_cat_add", False):
        if not has_perm(uid, "cats"):
            return
        db_exec(
            "INSERT INTO categories(name,is_active) VALUES(?,1)",
            (text,),
        )
        await adm_open_cats(update, context, 0)
        return

    cid = context.user_data.pop("await_cat_rename", None)
    if cid:
        if not has_perm(uid, "cats"):
            return
        db_exec("UPDATE categories SET name=? WHERE id=?", (text, cid))
        await adm_open_cat(update, context, cid)
        return

    # --- мастер добавления товара ---

    if context.user_data.pop("await_prod_name", False):
        if not has_perm(uid, "prods"):
            return
        context.user_data["new_prod_name"] = text
        context.user_data["await_prod_desc"] = True
        back_cb = context.user_data.get("prod_add_back", "adm:prods:0")
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "◀ Назад", callback_data=back_cb,
                    )
                ]
            ]
        )
        await replace_menu(update, context, "Описание:", kb, scope="admin")
        return

    if context.user_data.pop("await_prod_desc", False):
        if not has_perm(uid, "prods"):
            return
        context.user_data.pop("prod_add_back", None)
        name = context.user_data.pop("new_prod_name", "Товар")
        desc = text
        pid = db_exec(
            """INSERT INTO products(
                   name,description,price,stock,is_active
               ) VALUES(?,?,0,0,1)""",
            (name, desc),
        )
        await adm_open_prod(update, context, pid)
        return

    # --- редактирование товара ---

    pid = context.user_data.pop("await_prod_name_edit", None)
    if pid:
        if not has_perm(uid, "prods"):
            return
        db_exec("UPDATE products SET name=? WHERE id=?", (text, pid))
        await adm_open_prod(update, context, pid)
        return

    pid = context.user_data.pop("await_prod_desc_edit", None)
    if pid:
        if not has_perm(uid, "prods"):
            return
        db_exec(
            "UPDATE products SET description=? WHERE id=?",
            (text, pid),
        )
        await adm_open_prod(update, context, pid)
        return

    # --- варианты товара ---

    pid = context.user_data.pop("await_variant_name", None)
    if pid is not None:
        if not has_perm(uid, "prods"):
            return
        context.user_data["new_variant_name"] = text
        context.user_data["await_variant_stock"] = pid
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data=f"adm:prod:variants:{pid}")]]
        )
        await replace_menu(
            update, context, "Остаток варианта (число):", kb, scope="admin"
        )
        return

    pid = context.user_data.pop("await_variant_stock", None)
    if pid is not None:
        if not has_perm(uid, "prods"):
            return
        try:
            stock = int(text)
        except Exception:
            context.user_data["await_variant_stock"] = pid
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "◀ Назад",
                            callback_data=f"adm:prod:variants:{pid}",
                        )
                    ]
                ]
            )
            await replace_menu(
                update, context, "Введите число.", kb, scope="admin"
            )
            return
        name = context.user_data.pop("new_variant_name", "Вариант")
        db_exec(
            "INSERT INTO product_variants(product_id,name,stock) VALUES(?,?,?)",
            (pid, name, stock),
        )
        await adm_open_prod_variants(update, context, pid)
        return

    variant_ctx = context.user_data.pop("await_variant_name_edit", None)
    if variant_ctx:
        if not has_perm(uid, "prods"):
            return
        pid, vid = variant_ctx
        db_exec(
            "UPDATE product_variants SET name=? WHERE id=? AND product_id=?",
            (text, vid, pid),
        )
        await adm_open_variant(update, context, pid, vid)
        return

    variant_ctx = context.user_data.pop("await_variant_stock_edit", None)
    if variant_ctx:
        if not has_perm(uid, "prods"):
            return
        pid, vid = variant_ctx
        try:
            stock = int(text)
        except Exception:
            context.user_data["await_variant_stock_edit"] = (pid, vid)
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "◀ Назад",
                            callback_data=f"adm:variant:{pid}:{vid}",
                        )
                    ]
                ]
            )
            await replace_menu(
                update, context, "Введите число.", kb, scope="admin"
            )
            return
        db_exec(
            "UPDATE product_variants SET stock=? WHERE id=? AND product_id=?",
            (stock, vid, pid),
        )
        await adm_open_variant(update, context, pid, vid)
        return

    # --- бронь ---

    if context.user_data.pop("await_reserve_text", False):
        if not has_perm(uid, "reserve"):
            return
        set_setting("reserve_text", text or "Забронировать")
        await adm_open_reserve(update, context)
        return

    if context.user_data.pop("await_reserve_username", False):
        if not has_perm(uid, "reserve"):
            return
        set_setting("reserve_tg_username", text.strip())
        await adm_open_reserve(update, context)
        return

    if context.user_data.pop("await_reserve_tpl", False):
        if not has_perm(uid, "reserve"):
            return
        set_setting("reserve_msg_tpl", text or "")
        await adm_open_reserve(update, context)
        return

    # --- редакторы ---

    if context.user_data.pop("await_editor_add", False):
        if not is_admin(uid):
            return
        kb_back = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ К списку", callback_data="adm:editors")]]
        )
        try:
            if text.lstrip().startswith("@") or re.search(r"[A-Za-z_]", text):
                uname = text.strip().lstrip("@").lower()
                rows = db_query(
                    "SELECT * FROM users WHERE lower(username)=? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (uname,),
                )
                if not rows:
                    msg = (
                        "Пользователь с таким username не найден.\n"
                        "Попросите его сначала нажать /start у бота."
                    )
                    await replace_menu(
                        update, context, msg, kb_back, scope="admin"
                    )
                    return
                eid = rows[0]["user_id"]
                uname_real = rows[0]["username"] or uname
            else:
                eid = int(re.sub(r"[^\d]", "", text))
                rows = db_query(
                    "SELECT username FROM users WHERE user_id=?",
                    (eid,),
                )
                uname_real = rows[0]["username"] if rows else ""
            db_exec(
                """INSERT INTO editors(user_id,is_active,username)
                   VALUES(?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       is_active=1,
                       username=excluded.username""",
                (eid, 1, uname_real or ""),
            )
            msg = f"Редактор {eid} добавлен."
            await replace_menu(update, context, msg, kb_back, scope="admin")
        except Exception as e:
            await replace_menu(
                update, context, f"Ошибка: {e}", kb_back, scope="admin"
            )
        return


# ================== HANDLER ДОКУМЕНТОВ (ИМПОРТ) ==================

async def on_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    if not require_staff(update):
        return
    if not is_admin(update.effective_user.id):
        return
    doc = update.message.document if update.message else None
    if not doc:
        return
    try:
        f = await context.bot.get_file(doc.file_id)
        updir = BASE / "uploads"
        updir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\-]+", "_", doc.file_name or "file.bin")
        dst = updir / safe
        await f.download_to_drive(custom_path=str(dst))
        if safe.lower().endswith(".json"):
            data = json.loads(dst.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "categories" not in data:
                await update.message.reply_text("Неверный JSON.")
                return
            import_json(data)
            await update.message.reply_text("Импорт JSON завершён.")
        elif safe.lower().endswith(".db"):
            bdir = BASE / BACKUP_DIR
            bdir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            if Path(DB_PATH).exists():
                shutil.copy2(DB_PATH, bdir / f"catalog-{ts}.db")
            shutil.copy2(dst, DB_PATH)
            await update.message.reply_text("База .db заменена.")
        else:
            await update.message.reply_text(
                "Поддерживаются файлы .json и .db."
            )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


# ================== HANDLER ФОТО ДЛЯ АДМИНА ==================

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    if not require_staff(update):
        return
    if not has_perm(update.effective_user.id, "photos"):
        return
    pid = context.user_data.get("await_addphoto_pid")
    if not pid:
        return
    ph_list = update.message.photo
    if not ph_list:
        return
    fid = ph_list[-1].file_id
    db_exec(
        "INSERT INTO photos(product_id,file_id) VALUES(?,?)", (pid, fid)
    )
