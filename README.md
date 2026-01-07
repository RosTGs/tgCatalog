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

## Запуск на Timeweb

1. Создайте сервер/приложение в Timeweb и подключитесь по SSH.
2. Установите Python (желательно 3.10+), а также git:
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip git
   ```
3. Клонируйте репозиторий и перейдите в директорию проекта:
   ```bash
   sudo mkdir -p /srv/bots
   sudo chown -R "$USER":"$USER" /srv/bots
   git clone git@github.com:RosTGs/tgCatalog.git /srv/bots/tgCatalog
   cd /srv/bots/tgCatalog
   ```
4. Создайте `.env` и заполните переменные (минимум `BOT_TOKEN`, `ADMIN_IDS`, `DB_PATH`):
   ```bash
   nano .env
   ```
   Пример:
   ```dotenv
   BOT_TOKEN=ваш_токен
   ADMIN_IDS=123456,987654
   DB_PATH=/var/lib/tg-catalog/catalog.db
   ```
5. Установите зависимости:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
6. Запустите бота (один из вариантов):
   - **systemd**: создайте сервис, чтобы бот стартовал при перезагрузке.
     ```ini
     [Unit]
     Description=TG Catalog Bot
     After=network.target

     [Service]
     WorkingDirectory=/srv/bots/tgCatalog
     ExecStart=/srv/bots/tgCatalog/venv/bin/python /srv/bots/tgCatalog/main.py
     Restart=always
     User=youruser

     [Install]
     WantedBy=multi-user.target
     ```
     Замените `User` и пути на свои.
     ```bash
     sudo systemctl daemon-reload
     sudo systemctl enable --now tg-catalog.service
     ```
   - **pm2**: можно запустить через `pm2 start "venv/bin/python main.py" --name tg-catalog`.
7. При необходимости откройте нужные порты в панели Timeweb (например, для вебхуков или внешнего HTTP-доступа).

### Хранение базы и резервных копий

- Переменная `DB_PATH` должна указывать на путь к файлу базы (например, `/var/lib/tg-catalog/catalog.db`).
- Рекомендуется хранить базу вне директории репозитория (например, `/var/lib/tg-catalog/`), предварительно создав каталог и выдав права пользователю, под которым запускается бот.
- Каталог `backups` лучше располагать рядом с базой: `/var/lib/tg-catalog/backups/`.
