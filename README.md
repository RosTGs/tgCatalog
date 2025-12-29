# TG Catalog layered (admin + client)

## Установка локально

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка

1. Открой `.env` и пропиши:
   - `BOT_TOKEN` — токен бота из BotFather
   - `ADMIN_IDS` — Telegram user_id админов через запятую (например `123456,987654`).

2. Перезапусти бота после изменения `.env`.

## Запуск

```bash
source venv/bin/activate
python main.py
```

### Режимы

- Клиент:
  - команда `/start`
- Админ:
  - команда `/admin` (доступна только user_id из `ADMIN_IDS`)
