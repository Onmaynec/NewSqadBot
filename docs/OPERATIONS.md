# NewSqadBot — эксплуатация v1.0.0

Документ для развёртывания и восстановления бота. README и Issues не используются как техническое задание.

## Docker Desktop / Windows 11

1. Выберите Linux containers в Docker Desktop и проверьте docker info.
2. Создайте .env по образцу .env.example, укажите DISCORD_TOKEN. Никогда не коммитьте .env.
3. Сборка: docker compose build --pull
4. Запуск: docker compose up -d
5. Проверка: docker compose ps
6. Логи: docker compose logs --tail=100 bot

Команда smoke без подключения Discord: docker compose run --rm --no-deps --entrypoint python bot -m app.ops.smoke

## Docker Hub 401 Unauthorized

Отказ на стадии load metadata для python:3.12-slim-bookworm — ошибка загрузки образа из реестра, ещё до выполнения Dockerfile.

Проверка авторизации:
1. docker logout
2. docker login
3. docker pull python:3.12-slim-bookworm

По умолчанию v1.0.0 использует зеркало Docker Official Images на Amazon ECR:
public.ecr.aws/docker/library/python:3.12-slim-bookworm

Для ECR: docker pull public.ecr.aws/docker/library/python:3.12-slim-bookworm
Если ECR недоступен, добавьте в .env PYTHON_BASE_IMAGE=python:3.12-slim-bookworm, пройдите docker login, затем снова выполните docker compose build --pull.

Если оба реестра недоступны, проверить сеть, корпоративный прокси, Docker Desktop, VPN и сетевые блокировки. Не изменять токен Discord: он здесь не участвует.

## Обновление версии

1. Снять резервную копию SQLite.
2. git pull origin main
3. docker compose build --pull
4. docker compose up -d --force-recreate
5. docker compose ps
6. docker compose logs --tail=100 bot

НЕЛЬЗЯ использовать docker compose down -v: volume newsqad_data хранит базу.

## SQLite: резервные копии и восстановление

Бот использует SQLite с WAL, поэтому копирование только newsqad.db из работающего контейнера может привести к неполному бэкапу. Используется штатный SQLite Online Backup API.

Создание копии во время работы:
docker compose exec bot python -m app.ops.backup create

Проверка:
docker compose exec bot python -m app.ops.backup verify /app/data/backups/ИМЯ.db

Копии лежат в volume по пути /app/data/backups. Регулярно экспортируйте их за пределы Docker volume: например, через docker cp с идентификатором контейнера из docker compose ps.

Восстановление — только когда основной бот остановлен:
1. docker compose stop bot
2. docker compose run --rm --no-deps --entrypoint python bot -m app.ops.backup restore /app/data/backups/ИМЯ.db
3. docker compose up -d bot
4. Проверить health, логи, права доступа, роли и отложенные операции в Discord.

Restore проверяет целостность backup-файла, сохраняет текущую базу как before-restore-*.db и не выполняется при свежем признаке работы контейнера. Старые изменения, уже применённые к Discord, не откатываются восстановлением SQLite; необходимо сверить состояния.

## Discord и безопасность

Бот ограничен одним guild ID из ТЗ. Токен, персональные сведения и credential store Docker не публиковать.

В Discord Developer Portal включить Server Members Intent; для голосовых модулей нужен Voice States Intent. Message Content — только когда функции реально требуют чтения текста.

Роли бота должны стоять выше управляемых. Для временных комнат нужны Manage Channels и Move Members, для тикетов — Manage Channels и Embed Links, для Membership — Manage Roles, а для изменения ника — Manage Nicknames. Проверяйте channel overwrites.

После запуска:
- /botsettings — серверные настройки и права (администрация);
- /voice diagnose — проверка разрешений и настроек генератора;
- /application status — жизненный цикл заявки;
- /stats settings — начало сбора статистики;
- /suggest settings — панели предложений.

Docker smoke подтверждает загрузку Python-модулей и чистые миграции. Поведение на реальном Discord-сервере необходимо проверять отдельно на разрешённых тестовых аккаунтах, не применяя наказания к случайным пользователям.
