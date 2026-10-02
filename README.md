# Telegram SLA Monitoring Bot

Бот следит за временем ответа в рабочих Telegram-группах. Когда внешний участник пишет в
отслеживаемый чат, бот заводит «ожидание ответа» (тикет) с дедлайном `created_at + SLA`.
Если до дедлайна не ответил ни один из назначенных отвечающих (responders), бот отправляет
уведомление о нарушении SLA в служебный чат/топик с упоминанием ответственного и кнопкой
«Ответ не требуется».

PostgreSQL — единственный источник истины (настройки, тикеты, факты ответа и нарушения,
уведомления, аудит). Redis используется только как брокер Celery.

## Содержание

- [Архитектура](#архитектура)
- [Требования](#требования)
- [Быстрый старт (Docker Compose)](#быстрый-старт-docker-compose)
- [Локальная разработка](#локальная-разработка)
- [Настройка бота в Telegram](#настройка-бота-в-telegram)
- [Команды администратора](#команды-администратора)
- [Режимы сопоставления ответа](#режимы-сопоставления-ответа)
- [Надёжность](#надёжность)
- [Конфигурация](#конфигурация)
- [Тесты, линтеры, типы](#тесты-линтеры-типы)
- [Принятые допущения](#принятые-допущения)
- [Ограничения и дальнейшие улучшения](#ограничения-и-дальнейшие-улучшения)

## Архитектура

```text
Telegram ──updates──► bot (aiogram 3, asyncpg) ──► PostgreSQL ◄── celery-worker (psycopg)
                                                       ▲                 │
                         celery-beat ─► scan_due_replies ┘                 └─► Telegram sendMessage
```

| Сервис          | Что делает                                                                      |
|-----------------|---------------------------------------------------------------------------------|
| `bot`           | принимает updates (polling или webhook), создаёт/закрывает тикеты, админ-команды, кнопка; `/health/*` |
| `celery-beat`   | раз в 30 с запускает сканер просрочек и «подметальщик» outbox                   |
| `celery-worker` | помечает просроченные тикеты, пишет outbox, доставляет уведомления в Telegram   |
| `migrations`    | одноразовый `alembic upgrade head`                                              |
| `postgres`, `redis` | хранилище и брокер                                                          |

Структура кода:

```text
app/
  core/        config.py (pydantic-settings), enums.py, types.py, logging.py (JSON-логи)
  schemas/     Pydantic DTO: чаты, тикеты, уведомления, статистика, аргументы команд, callback data
  db/          base.py (naming convention), session.py, models/, repositories/, uow.py
  services/    бизнес-логика: MessageProcessing, PendingReply, Sla, Notification, ChatSettings,
               Stats, TelegramUser, MessageLink, Audit + чистые правила (reply_matching, formatting)
  bot/         dispatcher, routers/ (common, messages, callbacks, admin_chats, stats), filters/,
               keyboards/, middlewares/, sender.py (адаптер aiogram для воркера), main.py
  workers/     celery_app.py, context.py, tasks/ (sla, notifications, maintenance)
  web/         health.py (/health/live, /health/ready)
migrations/    Alembic
tests/         unit/ и integration/ (реальный PostgreSQL)
```

Handlers и Celery-задачи тонкие: извлекают данные и вызывают сервис. Сервисы работают через
Unit of Work: открыть транзакцию → изменить данные → commit, и только потом обращаться к
Telegram. Бот использует async SQLAlchemy (asyncpg), воркер — sync (psycopg); пути исполнения
не смешиваются.

### Жизненный цикл тикета

```text
             ответ responder                    ответ responder (время фиксируется)
waiting ───────────────────────► answered ◄──────────────────────────┐
   │                                                                  │
   │ дедлайн наступил (scan_due_replies)                              │
   └──────────────────────────────────► overdue ──────────────────────┘
   │                                       │
   └──── «Ответ не требуется» ─────────────┴──► not_required
```

`cancelled` — мониторинг чата выключен (`/chat_disable`), пока тикет был открыт; такие тикеты
не эскалируются и не входят в расчёт соблюдения SLA.

### SLA-таймеры и уведомления (transactional outbox)

1. Дедлайн хранится в `pending_replies.deadline_at`. Никаких долгих `countdown/eta` в Celery.
2. `scan_due_replies` (Celery Beat, каждые `APP_CELERY__SCAN_INTERVAL_SECONDS`) в одной транзакции:
   `SELECT … WHERE status='waiting' AND deadline_at <= now() … FOR UPDATE SKIP LOCKED LIMIT batch`,
   переводит тикеты в `overdue`, пишет `reply_events(overdue)` и событие в `outbox_events`
   (уникальность `(event_type, aggregate_type, aggregate_id)` — не больше одной эскалации на тикет).
   После commit ставит `notify_overdue_reply(outbox_id)` в очередь `notifications`.
3. `notify_overdue_reply`: блокирует outbox-событие (`SKIP LOCKED`), проверяет, что оно
   `pending` и тикет всё ещё `overdue`, переводит в `processing`, **commit**, затем отправляет
   сообщение в Telegram, затем отдельной транзакцией фиксирует `sent`, `notification_message_id`,
   `notification_sent_at`.
4. Временные ошибки (сеть, 5xx, flood control) → событие снова `pending` с
   `available_at = now + backoff` (экспонента, учёт `retry_after`), Celery `retry`. Число попыток
   ограничено `APP_CELERY__MAX_DELIVERY_ATTEMPTS`.
5. Постоянные ошибки (`chat not found`, бот удалён/нет прав, не задан чат уведомлений) →
   `failed`, текст ошибки в `last_error`, лог `event=notification_failed` уровня ERROR
   (технический алерт).
6. `deliver_pending_notifications` (Beat) — страховка: подбирает `pending`-события, если сообщение
   Celery потерялось. `recover_stale_notifications` обрабатывает события, «зависшие» в `processing`.

## Требования

- Docker 24+ и Docker Compose v2 — для запуска;
- для разработки: Python 3.12+, PostgreSQL 16+, Redis 7+.

## Быстрый старт (Docker Compose)

```bash
cp .env.example .env
# заполните APP_TELEGRAM__BOT_TOKEN, APP_TELEGRAM__ADMIN_TELEGRAM_IDS, APP_DATABASE__PASSWORD
docker compose up -d --build
docker compose ps
docker compose logs -f bot celery-worker celery-beat
```

Порядок запуска обеспечивается `depends_on`: `postgres`/`redis` (healthcheck) →
`migrations` (`alembic upgrade head`, должен завершиться успешно) → `bot`, `celery-worker`,
`celery-beat`.

Применить миграции вручную:

```bash
docker compose run --rm migrations            # = alembic upgrade head
docker compose run --rm migrations alembic current
```

Flower для отладки (не запускается по умолчанию):

```bash
docker compose --profile debug up -d flower   # http://127.0.0.1:5555
```

Health-эндпоинты бота (порт `APP_APP__HTTP_PORT`, по умолчанию 8080, опубликован на
`127.0.0.1:8080`):

- `GET /health/live` — процесс жив;
- `GET /health/ready` — PostgreSQL и Redis отвечают (таймаут 2 с), иначе `503`.

## Локальная разработка

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # APP_DATABASE__HOST=127.0.0.1, APP_REDIS__HOST=127.0.0.1
docker compose up -d postgres redis
alembic upgrade head

python -m app.bot                                                    # бот (polling)
celery -A app.workers.celery_app:celery_app worker -Q default,notifications,maintenance -l INFO
celery -A app.workers.celery_app:celery_app beat -l INFO
```

Запускайте ровно **один** процесс `celery beat`, иначе периодические задачи будут ставиться
дважды (это безопасно благодаря блокировкам, но бесполезно). Воркеров может быть сколько угодно.

### Polling и webhook

- **Polling** (по умолчанию, `APP_TELEGRAM__WEBHOOK_ENABLED=false`): бот сам удаляет webhook
  и опрашивает Telegram. HTTP-сервер поднимается только для `/health/*`.
- **Webhook**: задайте

  ```dotenv
  APP_TELEGRAM__WEBHOOK_ENABLED=true
  APP_TELEGRAM__WEBHOOK_BASE_URL=https://bot.example.com
  APP_TELEGRAM__WEBHOOK_PATH=/telegram/webhook
  APP_TELEGRAM__WEBHOOK_SECRET_TOKEN=<случайная строка, 1-256 символов A-Za-z0-9_->
  ```

  Бот регистрирует webhook при старте и слушает тот же порт `APP_APP__HTTP_PORT`
  (webhook + health). Запросы без корректного заголовка `X-Telegram-Bot-Api-Secret-Token`
  отклоняются. TLS терминируйте на reverse proxy (nginx/Traefik), проксируя
  `WEBHOOK_PATH` на `bot:8080`; для этого измените публикацию порта
  (`BOT_HTTP_PUBLISH=0.0.0.0:8080`) или подключите прокси к сети compose.

### Прокси для Telegram (SOCKS / HTTP)

Все запросы к Telegram Bot API — и бота (polling, ответы, кнопки), и Celery-воркера
(отправка уведомлений) — можно пустить через прокси:

```dotenv
APP_TELEGRAM__PROXY_URL=socks5://10.0.0.5:1080        # или socks4://…, http://proxy:3128
# логин/пароль — прямо в URL (percent-encoded): socks5://user:p%40ss@10.0.0.5:1080
# или отдельными переменными (спецсимволы экранируются автоматически):
APP_TELEGRAM__PROXY_USERNAME=user
APP_TELEGRAM__PROXY_PASSWORD=p@ss
```

- Схемы: `http` (через `CONNECT`), `socks4`, `socks5`; `socks5h` и `socks4a` принимаются как
  синонимы. DNS-имя `api.telegram.org` всегда разрешается на стороне прокси.
  HTTPS-прокси (`https://`) не поддерживается.
- Неверный URL (нет порта, неизвестная схема, путь) — ошибка при старте с понятным сообщением.
- Пароль не попадает в логи: при старте пишется только `telegram api proxy: socks5://host:port`.
- Недоступный прокси при отправке уведомления — временная ошибка: событие уходит на повтор с
  backoff, как при обычной сетевой ошибке.
- Через прокси идут только запросы к Telegram; PostgreSQL и Redis подключаются напрямую.
  Входящий webhook прокси не касается.
- В Docker адрес прокси должен быть доступен из контейнера: не `127.0.0.1` хоста, а, например,
  `host.docker.internal` (с `extra_hosts: ["host.docker.internal:host-gateway"]`) или имя
  сервиса в сети compose.

## Настройка бота в Telegram

1. Создайте бота у [@BotFather](https://t.me/BotFather), получите токен.
2. **Privacy mode**: `@BotFather → /mybots → Bot Settings → Group Privacy → Turn off`.
   Иначе бот в группах видит только команды и ответы на свои сообщения, и тикеты создаваться
   не будут. После изменения настройки **удалите и заново добавьте бота** в группы.
   Альтернатива — сделать бота администратором группы (админы видят все сообщения).
3. Добавьте бота в отслеживаемые группы и в служебный чат уведомлений. В служебном чате бот
   должен иметь право писать; если это форум — право писать в нужный топик.
4. Узнайте свой Telegram ID (например, через @userinfobot) и укажите его в
   `APP_TELEGRAM__ADMIN_TELEGRAM_IDS`.
5. ID чата уведомлений: перешлите сообщение из него боту-«определителю» или временно
   добавьте туда бота и посмотрите `telegram_chat_id` в логах. ID топика — число из ссылки
   на сообщение в топике: `https://t.me/c/<chat>/<thread_id>/<message_id>`.

## Команды администратора

Команды настройки выполняются **в самой отслеживаемой группе** и действуют на неё.
Доступны только пользователям из `APP_TELEGRAM__ADMIN_TELEGRAM_IDS`; остальные получают отказ.
В личном чате доступны `/start`, `/help`, `/stats`.

| Команда | Описание |
|---|---|
| `/start`, `/help` | справка |
| `/chat_add` | добавить текущую группу в мониторинг (настройки по умолчанию из `.env`) |
| `/chat_enable`, `/chat_disable` | включить/выключить мониторинг; история сохраняется, открытые ожидания при выключении переводятся в `cancelled` |
| `/chat_settings` | текущие настройки |
| `/chat_sla <seconds>` | SLA, 1 с … 30 суток |
| `/chat_priority <p1\|p2\|p3\|p4>` | приоритет |
| `/chat_mode <any_responder_message\|reply_only\|thread_or_reply>` | режим сопоставления ответа |
| `/chat_timezone <IANA>` | часовой пояс для отображения, например `Asia/Yekaterinburg` |
| `/chat_responsible` | Reply на сообщение пользователя → назначить ответственного |
| `/chat_add_responder` | Reply → добавить отвечающего |
| `/chat_remove_responder` | Reply → удалить отвечающего |
| `/chat_responders` | список отвечающих |
| `/chat_notification <chat_id> [thread_id]` | куда отправлять уведомления о нарушении SLA |
| `/pending` | открытые ожидания в текущем чате |
| `/stats [days]` | статистика за `days` дней (1–365, по умолчанию 30) |

Пример первичной настройки группы:

```text
/chat_add
/chat_sla 900
/chat_priority p1
/chat_mode any_responder_message
(Reply на сообщение сотрудника) /chat_responsible
(Reply на сообщение другого сотрудника) /chat_add_responder
/chat_notification -1001234567890 42
/chat_settings
```

Каждое изменение конфигурации пишется в `chat_configuration_audit` (кто, когда, старое и новое
значение).

Кнопку «Ответ не требуется» могут нажимать глобальные администраторы и ответственный за чат
(`APP_APP__RESPONSIBLE_CAN_MARK_NOT_REQUIRED=true`). После нажатия тикет переходит в
`not_required`, клавиатура убирается, к тексту уведомления добавляется строка
`✅ Ответ не требуется. Отметил: <пользователь>`, в `reply_events` пишется аудит.

### Смена ответственного из уведомления

Под каждым SLA-уведомлением, кроме «Ответ не требуется», есть две кнопки. Нажатие показывает
список отвечающих (responders) чата, кроме текущего ответственного, и кнопку «« Назад».

| Кнопка | Что меняет | Кто может |
|---|---|---|
| 👤 Сменить ответственного по сообщению | ответственного только этого тикета (snapshot): строка «Ответственный:» в уведомлении обновляется, добавляется `🔁 Ответственный по сообщению: A → B. Изменил: …`; в `reply_events` пишется `reassigned`; статистика по тикету относится к новому ответственному | глобальные админы, текущий ответственный тикета, ответственный за чат; только для открытых тикетов |
| 👥 Сменить ответственного чата | ответственного чата для **новых** сообщений (как `/chat_responsible`); уже созданные тикеты не меняются; добавляется `👥 Ответственный чата: A → B (для новых сообщений)`; аудит в `chat_configuration_audit` с `source=notification` | только глобальные админы |

Выбрать можно только пользователя из списка отвечающих чата — это проверяется на сервере,
поэтому подделанный callback с чужим ID будет отклонён. Если отвечающих, кроме текущего
ответственного, нет, бот предложит добавить их через `/chat_add_responder`.

### Статистика

Группировка — по чату и ответственному (snapshot на момент создания тикета). Метрики:
создано, ответов, среднее/медиана/P90 времени ответа (`responded_at - created_at`), просрочки,
соблюдение SLA, `not_required` отдельно, открыто сейчас.

- **Просрочка** = ответ получен после дедлайна **или** тикет всё ещё открыт после дедлайна.
- **Соблюдение SLA** = `ответов в срок / (ответов в срок + просрочек)`; `not_required` и
  `cancelled` не учитываются. При отсутствии данных показывается `—`.
- Длинные отчёты разбиваются на несколько сообщений (лимит 4096 символов).

## Режимы сопоставления ответа

Ответом считается только сообщение пользователя из списка responders этого чата, отправленное
позже исходного сообщения (сравниваются message ID, они монотонны в пределах чата). Одно
сообщение закрывает не более одного тикета; закрывать можно тикеты в статусах `waiting` и
`overdue`.

| Режим | Правило |
|---|---|
| `any_responder_message` (по умолчанию) | любое сообщение responder закрывает **самый старый** открытый тикет чата |
| `reply_only` | только прямой Reply на исходное сообщение закрывает этот тикет |
| `thread_or_reply` | прямой Reply → точное совпадение; иначе закрывается самый старый тикет в том же топике (`message_thread_id`; сообщения вне топиков считаются одним «общим» потоком) |

Особенность форумов: Telegram проставляет `reply_to_message` = корневое сообщение топика для
каждого обычного сообщения в топике. Такой «ответ» не считается прямым Reply.

Конкурентность: подбор тикета выполняется `SELECT … FOR UPDATE SKIP LOCKED`. Повторная доставка
того же сообщения responder не закрывает второй тикет (проверка + частичный уникальный индекс
`(chat_id, response_message_id)`).

## Надёжность

| Сценарий | Как обработан |
|---|---|
| Повторный update исходного сообщения (webhook redelivery) | `INSERT … ON CONFLICT DO NOTHING` по `(chat_id, source_message_id)` |
| Повторный update ответа responder | проверка `response_message_id` + уникальный индекс |
| Ответ одновременно с запуском сканера | сканер пропускает заблокированные строки (`SKIP LOCKED`) и пересматривает их на следующем запуске; кто первым закоммитил, тот и прав |
| Два worker-а / повторная Celery-задача | блокировка outbox-события + проверка статуса `pending`; уникальный outbox на тикет |
| Двойное нажатие кнопки | блокирующий `FOR UPDATE` на тикет: второе нажатие видит финальный статус |
| Ответ между эскалацией и доставкой | событие `cancelled`, уведомление не отправляется; тикет считается просроченным в статистике |
| Telegram принял сообщение, а worker упал до записи ID | событие остаётся `processing`; по таймауту `APP_CELERY__PROCESSING_TIMEOUT_SECONDS` помечается `failed` с алертом `notification_state_unknown` (без дубля). Можно выбрать «лучше дубль, чем потеря»: `APP_CELERY__RESEND_STALE_NOTIFICATIONS=true` |
| Смена настроек после создания тикета | SLA, приоритет и ответственный хранятся snapshot-ом в тикете; адрес уведомления берётся актуальный на момент эскалации |
| Рестарты | всё состояние в PostgreSQL; `task_acks_late`, `task_reject_on_worker_lost`, `prefetch_multiplier=1` |
| Группа стала супергруппой | сервисное сообщение `migrate_to_chat_id` обновляет `telegram_chat_id` |

Транзакции к БД никогда не открыты во время HTTP-запросов к Telegram.

## Конфигурация

Все переменные имеют префикс `APP_` и разделитель вложенности `__`; см. `.env.example`.
Секреты (`BOT_TOKEN`, пароли, webhook secret) хранятся как `SecretStr` и не попадают в логи.

| Переменная | По умолчанию | Описание |
|---|---|---|
| `APP_TELEGRAM__BOT_TOKEN` | — (обязательно) | токен бота |
| `APP_TELEGRAM__ADMIN_TELEGRAM_IDS` | пусто | ID глобальных админов через запятую |
| `APP_TELEGRAM__WEBHOOK_*` | выкл. | см. «Polling и webhook» |
| `APP_TELEGRAM__PROXY_URL` | нет | прокси для Telegram API: `http://`, `socks4://`, `socks5://` |
| `APP_TELEGRAM__PROXY_USERNAME` / `_PASSWORD` | нет | учётные данные прокси (альтернатива логину в URL) |
| `APP_DATABASE__HOST/PORT/NAME/USER/PASSWORD` | `postgres/5432/telegram_sla_bot/telegram_sla_bot/—` | PostgreSQL |
| `APP_REDIS__HOST/PORT/PASSWORD` | `redis/6379/—` | Redis (брокер) |
| `APP_APP__DEFAULT_SLA_SECONDS` | `900` | SLA для новых чатов |
| `APP_APP__DEFAULT_PRIORITY` | `p3` | приоритет новых чатов |
| `APP_APP__DEFAULT_REPLY_MATCH_MODE` | `any_responder_message` | режим новых чатов |
| `APP_APP__DEFAULT_TIMEZONE` | `Europe/Moscow` | часовой пояс новых чатов |
| `APP_APP__RESPONSIBLE_CAN_MARK_NOT_REQUIRED` | `true` | ответственный может нажимать кнопку |
| `APP_APP__AUTO_ADD_RESPONSIBLE_AS_RESPONDER` | `true` | назначение ответственного добавляет его в responders |
| `APP_APP__HTTP_PORT` | `8080` | порт health/webhook |
| `APP_CELERY__SCAN_INTERVAL_SECONDS` | `30` | период сканера |
| `APP_CELERY__SCAN_BATCH_SIZE` | `100` | тикетов за один проход |
| `APP_CELERY__MAX_DELIVERY_ATTEMPTS` | `8` | попыток доставки |
| `APP_CELERY__RETRY_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | `5` / `600` | экспоненциальный backoff |
| `APP_CELERY__PROCESSING_TIMEOUT_SECONDS` | `300` | когда `processing` считается зависшим |
| `APP_CELERY__RESEND_STALE_NOTIFICATIONS` | `false` | см. таблицу надёжности |
| `APP_LOGGING__LEVEL` / `APP_LOGGING__JSON_FORMAT` | `INFO` / `true` | логирование |

Логи — JSON в stdout с полями `event`, `update_id`, `telegram_chat_id`, `telegram_message_id`,
`pending_reply_id`, `outbox_event_id`, `telegram_user_id`, `celery_task_id`, `error_type`,
`error_message`, где применимо.

## Тесты, линтеры, типы

```bash
pip install -e ".[dev]"
ruff format --check . && ruff check .
mypy                       # strict
pytest tests/unit          # без внешних зависимостей
```

Интеграционные тесты используют реальный PostgreSQL: создают временную БД, применяют
Alembic-миграции и удаляют БД после прогона. Пользователь должен иметь право `CREATEDB`:

```bash
docker compose up -d postgres
export APP_TEST_DATABASE_URL=postgresql+psycopg://telegram_sla_bot:<password>@127.0.0.1:5432/postgres
pytest                     # unit + integration; без доступной БД integration-тесты пропускаются
```

(Для этого опубликуйте порт postgres, например `docker compose run` с `-p 5432:5432`, или
используйте локальный PostgreSQL.) CI (`.github/workflows/ci.yml`) запускает ruff, mypy,
все тесты с сервисом PostgreSQL 16 и проверку `alembic upgrade/downgrade`.

## Принятые допущения

1. Ответы учитываются **только в исходном чате**; личные сообщения боту не считаются ответом.
2. «Ответ не требуется» нажимают глобальные админы и назначенный ответственный (отключаемо).
3. Сообщение ответственного считается ответом, если он входит в `chat_responders`; по умолчанию
   при назначении ответственный автоматически добавляется в responders.
4. SLA, приоритет и ответственный фиксируются snapshot-ом в тикете: изменения настроек не
   меняют уже созданные обязательства и историческую статистику.
5. SLA считается 24/7 в календарных секундах; рабочие часы и праздники не учитываются.
6. Сообщения, отправленные до добавления бота (и до `/chat_add`), игнорируются.
7. Режим по умолчанию — `any_responder_message`.
8. Время начала SLA (`created_at`) — время отправки сообщения в Telegram (не позже момента
   обработки). Если бот был недоступен, пропущенные updates получат честный, уже частично
   истёкший SLA.
9. Игнорируются: сообщения ботов, сервисные сообщения, сообщения без `from_user`, сообщения
   от имени чата/канала (`sender_chat`), команды боту (`/...`), пустые тексты, отредактированные
   сообщения. Медиа (с подписью или без) — полезные сообщения.
10. Уведомление отправляется в чат/топик, заданный в настройках на момент нарушения SLA.

## Ограничения и дальнейшие улучшения

Ограничения Bot API:

- бот не может прочитать историю чата: учитываются только сообщения, полученные через updates
  после добавления бота, при выключенном privacy mode;
- ссылки на сообщения существуют только для супергрупп (`t.me/c/...` открывается только
  участникам чата); для обычных групп ссылка не формируется;
- у `sendMessage` нет ключа идемпотентности, поэтому «exactly once» при падении процесса
  ровно между ответом Telegram и записью в БД невозможен — выбирается политика
  (без дублей по умолчанию);
- удаление сообщения в группе бот не видит, поэтому тикет на удалённое сообщение остаётся
  открытым (закройте кнопкой).

Идеи развития: рабочие часы/календари, многоуровневая эскалация, повторные напоминания,
закрытие тикета при удалении сообщения (через MTProto/userbot), web-панель, метрики
Prometheus, автоочистка старых outbox-событий, RBAC.
