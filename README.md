# NewSqadBot

NewSqadBot — Discord-бот для сообщества NewSquad (один сервер). Модули: заявки, роли, модерация, тикеты, голосовые комнаты, мероприятия, объявления и прогресс участников.

**Версия:** 1.4.0. Стек: Python 3.12, discord.py, SQLite/SQLAlchemy, Alembic, Docker Compose.

## Запуск

1. Установите Docker Desktop (Linux containers).
2. Создайте .env из .env.example и установите DISCORD_TOKEN. Не публикуйте секреты.
3. В Discord Developer Portal включите Server Members Intent; Message Content Intent нужен только при использовании XP за текст.
4. Проверьте роль бота и разрешения на целевом сервере.
5. Выполните: docker compose up -d --build

Диагностика: docker compose ps; docker compose logs --tail=150 bot

Smoke без Discord: docker compose run --rm --no-deps --entrypoint python bot -m app.ops.smoke

## Обновление без потери базы

Перед обновлением: docker compose exec bot python -m app.ops.backup create

Затем: git pull origin main; docker compose up -d --build --force-recreate

Не используйте docker compose down -v: база хранится в Docker volume. Выгружайте резервные копии за пределы volume.

## Разработка

Установите requirements-dev.txt и запустите:
- ruff check .
- python -m compileall -q app main.py
- python -m pytest -q

GitHub Actions проверяет Python, тесты, Docker Build и smoke-проверку пустой SQLite.

## Надёжность v1.4.0

- При старте выполняются Alembic и проверка SQLite quick_check/foreign_key_check.
- Healthcheck фиксирует Discord, SQLite, Scheduler и EventBus в /app/data/health.json.
- SIGTERM инициирует корректную остановку фоновых задач.
- Scheduler использует SQLite leases и повторные попытки; завершённые ключи сохраняются для защиты от повторного исполнения.
- EventBus использует durable outbox, сквозное постраничное сканирование, квитанции и осторожную очистку старых завершённых событий.
- /botsettings → Диагностика показывает состояние очередей.

Операции Discord API не гарантируют exactly-once после разрыва соединения: внешние побочные эффекты требуют идемпотентности.

Подробные инструкции: docs/OPERATIONS.md
