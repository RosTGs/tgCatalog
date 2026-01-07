from __future__ import annotations

import os
import json
import logging
import re
from urllib.parse import quote

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db_layer import (
    db_query,
    db_exec,
    get_setting as _get_setting,
    set_setting as _set_setting,
)

log = logging.getLogger(__name__)

# --------- админы из .env ---------

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: set[int] = set()

for part in ADMIN_IDS_RAW.replace(";", ",").split(","):
    part = part.strip()
    if not part:
        continue
    try:
        ADMIN_IDS.add(int(part))
    except ValueError:
        pass

log.info("ADMIN_IDS parsed: %s", ADMIN_IDS)


# просто прокидываем наружу, как ожидал admin_bot

def get_setting(key: str) -> str | None:
    return _get_setting(key)


def set_setting(key: str, value: str) -> None:
    _set_setting(key, value)


# --------- трекинг сообщений ---------

async def remember(update: Update, message_id: int, scope: str) -> None:
    chat_id = update.effective_chat.id
    db_exec(
        "INSERT INTO messages(chat_id,message_id,scope) VALUES(?,?,?)",
        (chat_id, message_id, scope),
    )


async def delete_scope(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    scope: str,
) -> None:
    chat_id = update.effective_chat.id
    rows = db_query(
        "SELECT message_id FROM messages WHERE chat_id=? AND scope=?",
        (chat_id, scope),
    )
    for r in rows:
        mid = r["message_id"]
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass
    db_exec(
        "DELETE FROM messages WHERE chat_id=? AND scope=?",
        (chat_id, scope),
    )


async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    rows = db_query(
        "SELECT message_id FROM messages WHERE chat_id=?",
        (chat_id,),
    )
    for r in rows:
        mid = r["message_id"]
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass
    db_exec("DELETE FROM messages WHERE chat_id=?", (chat_id,))


async def replace_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup,
    scope: str,
    parse_mode: str | None = ParseMode.HTML,
):
    await delete_scope(update, context, scope)
    chat_id = update.effective_chat.id
    m = await context.bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    await remember(update, m.message_id, scope)
    return m


# --------- пользователи / роли ---------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _editor_row(user_id: int):
    rows = db_query("SELECT * FROM editors WHERE user_id=?", (user_id,))
    return rows[0] if rows else None


def has_perm(user_id: int, perm: str) -> bool:
    if is_admin(user_id):
        return True
    row = _editor_row(user_id)
    if not row or not row["is_active"]:
        return False
    col_map = {
        "cats": "perm_cats",
        "prods": "perm_prods",
        "photos": "perm_photos",
        "links": "perm_links",
        "welcome": "perm_welcome",
        "reserve": "perm_reserve",
    }
    col = col_map.get(perm)
    if not col:
        return False
    return bool(row[col])


def require_staff(update: Update) -> bool:
    """Просто проверка прав, без сообщений для клиентов."""
    user = update.effective_user
    if not user:
        return False
    uid = user.id
    if is_admin(uid):
        return True
    row = _editor_row(uid)
    return bool(row and row["is_active"])


def touch_user(update: Update) -> None:
    user = update.effective_user
    if not user:
        return
    uid = user.id
    username = (user.username or "").strip()
    first = user.first_name or ""
    last = user.last_name or ""
    db_exec(
        """INSERT INTO users(user_id,username,first_name,last_name,updated_at)
           VALUES(?,?,?,?,datetime('now'))
           ON CONFLICT(user_id) DO UPDATE SET
               username=excluded.username,
               first_name=excluded.first_name,
               last_name=excluded.last_name,
               updated_at=datetime('now')""",
        (uid, username, first, last),
    )


# --------- главные кнопки (links_json) ---------

def load_links() -> list[dict]:
    raw = get_setting("links_json") or "[]"
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    norm: list[dict] = []
    for x in data:
        if not isinstance(x, dict):
            continue
        text = str(x.get("text", "")).strip()
        url = str(x.get("url", "")).strip()
        if not text or not url:
            continue
        active = 1 if int(x.get("active", 1) or 0) else 0
        norm.append({"text": text, "url": url, "active": active})
    return norm


def save_links(arr: list[dict]) -> None:
    set_setting("links_json", json.dumps(arr, ensure_ascii=False))


def public_links() -> list[dict]:
    return [b for b in load_links() if b.get("active", 1)]


def style_link_text(t: str) -> str:
    t = t.strip()
    if not t:
        return "Ссылка"
    return f"🔗 {t}"


# --------- бронь ---------

def reserve_enabled() -> bool:
    return (get_setting("reserve_enabled") or "0") == "1"


def reserve_text() -> str:
    return get_setting("reserve_text") or "Забронировать"


def _normalize_tg_username(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        return raw[1:]
    match = re.search(r"t\.me/([^/?#]+)", raw)
    if match:
        return match.group(1)
    return raw


def reserve_url_for(prod, size: str | None = None) -> str | None:
    if not reserve_enabled():
        return None

    username = _normalize_tg_username(
        get_setting("reserve_tg_username") or ""
    )
    pid = prod["id"]
    name = str(prod["name"])
    size_label = (size or "").strip() or "—"

    if not username:
        return None

    tpl = (
        get_setting("reserve_msg_tpl")
        or "Здравствуйте, хочу забронировать украшение {name} (ID: {id}, Размер: {size})"
    )
    txt = (
        tpl.replace("{id}", str(pid))
        .replace("{name}", name)
        .replace("{size}", size_label)
    )
    return f"https://t.me/{username}?text={quote(txt)}"
