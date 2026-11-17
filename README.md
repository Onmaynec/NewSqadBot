# NewSqadBot

NewSqadBot — Discord-бот для сообщества NewSquad (один сервер). Модули: заявки, роли, модерация, тикеты, голосовые комнаты, мероприятия, объявления и прогресс участников.

**Версия:** 1.6.0. Стек: Python 3.12, discord.py, SQLite/SQLAlchemy, Alembic, Docker Compose.

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


## Обновление v1.5.0 — управление сообществом

Новое в версии:
- `/m panel`, `/m case`, `/m cases`, `/m revoke`, `/m reason`, `/m duration`: интерактивные дела, поиск, история изменений, причина отмены и изменение сроков. Для отдельного журнала настройте `moderation_audit_channel_id` в `/botsettings`.
- `/membership request-info`, `/membership transfer`, `/membership timeline`, `/membership nickname` и `/application answer`: уточнения, передача дел, история и формат серверного ника.
- `/roles panel add` принимает категорию games/notifications/interests/decorative, `/roles panel exclude` позволяет ограничить одновременную выдачу несовместимых ролей.
- `/tickets mine`, `/tickets unanswered`, `/tickets reply-alert` показывают личные обращения и предупреждения об отсутствии первого ответа. Переписки не сохраняются при закрытии.
- `/party list with_places:true` показывает группы со свободными местами, `/party invite` отправляет приглашения, владельцы получают ЛС при вступлении.
- Еженедельные задания и достижения за подтверждённое посещение мероприятий.
- Предложения получили категории и фильтры по автору; об административном решении автору отправляется личное сообщение, если ЛС доступны.

Миграция `0014_community_categories` добавляет категории вариантам панелей ролей и предложениям. Перед обновлением сохраните проверенную копию SQLite; не запускайте `docker compose down -v`.

**Ограничения:** повторяющиеся мероприятия, расширенные аналитические отчёты, полноценный конструктор объявлений и новые механики магазина ещё не реализованы в этой версии. Большинство голосовых функций, наград, розыгрышей и управления тикетами уже присутствуют в предыдущих релизах. Проверка на живом Discord-сервере должна проводиться отдельно.


## Production 1.6.0: локальный сайт администратора

Вместе с ботом работает **локальная чёрно-белая веб-панель**. Выбирайте Discord-роли и каналы из списков, настраивайте параметры участников, тикетов, XP, NS Coins, мероприятий и 16 модулей. Используется та же SQLite — второй сервер базы не требуется.

**Быстрый запуск:**

1. Создайте .env из .env.example и заполните DISCORD_TOKEN.
2. Выполните docker compose build.
3. Сгенерируйте хэш пароля: docker compose run --rm --no-deps --entrypoint python bot -m app.web.auth hash
4. Скопируйте полученную строку WEB_ADMIN_PASSWORD_HASH=... в .env.
5. Запустите: docker compose up -d --build
6. Откройте **http://127.0.0.1:8787** на том же компьютере и войдите.

Пароль должен быть не короче 14 символов. Веб-панель отключена, пока не задан хэш. Порт Docker опубликован только для localhost; **не открывайте панель в интернет**. После изменения включённых модулей перезапустите контейнер. Все прочие настройки сохраняются в SQLite сразу.

В этой версии добавлены WAL, контроль foreign_keys на каждом соединении, безопасная блокировка restore во время degraded-состояния и автоматические тесты HTTP/CSRF/сохранений.

**Подробные шаги, безопасность и чек-лист ручного тестирования:** [docs/RELEASE_1.6.0.md](docs/RELEASE_1.6.0.md).

Веб-сайт редактирует параметры из белого списка; сложные конструкторы Discord (товары магазина, объявления, панели ролей и категории тикетов) пока остаются в slash-командах.
