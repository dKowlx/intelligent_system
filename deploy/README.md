# Один VPS: Docker Compose и Caddy

Исходники находятся в `/opt/studynavigator/app`, серверные настройки — отдельно
в `/opt/studynavigator/server.env` с правами `0600`. Сервер: `root@136.234.80.52`.
Используйте `.env.example` как шаблон; ключ остаётся только на сервере.
Все команды ниже выполняются на VPS из каталога приложения. Compose получает
подстановки из `--env-file ../server.env`; приложение получает настройки
через `env_file` из того же серверного файла. Укажите в нём
`DOMAIN=navigator.136-234-80-52.sslip.io`.

## Запуск и проверка

Нужны Docker Engine с Compose V2, свободные TCP 80/443, UDP 443 для HTTP/3,
исходящий HTTPS и DNS, указывающий на VPS. Настроенный адрес (его доступность и HTTPS проверяются отдельно):
`https://navigator.136-234-80-52.sslip.io/`.

```sh
cd /opt/studynavigator/app
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml config --quiet
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml build --pull app
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml up -d --wait
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml ps
curl --fail https://navigator.136-234-80-52.sslip.io/health
```

Для другого файла настроек задайте `STUDYNAV_ENV_FILE`, для другого домена —
`DOMAIN` в выбранном env-файле. Не выводите `docker compose config`
без `--quiet`: развёрнутые настройки могут включать API-ключ.

Backend работает от `10001:10001`, слушает `0.0.0.0:8000` внутри контейнера и
не публикует порт на хост. Единственный worker задан в Python независимо от
`WEB_CONCURRENCY`. Caddy на `172.30.43.2` — единственный доверенный источник
forwarded-заголовков. Отдельная bridge-сеть `172.30.43.0/28` не должна пересекаться
с сетью VPS/VPN; при смене IP прокси обновите `FORWARDED_ALLOW_IPS` вместе с IPAM.
Сеть допускает исходящие подключения: они нужны для API модели.

Именованный том `studynavigator_app_data` сохраняет `/app/var`: SQLite, загруженные
PDF и журнал расходов. При первом создании Docker переносит права `10001:10001`
из образа. Тома `studynavigator_caddy_data` и `studynavigator_caddy_config`
сохраняют сертификаты и состояние Caddy. Оба сервиса имеют `unless-stopped`
и ротацию stdout/stderr до трёх файлов по 10 MiB. Docker должен быть включён
в автозапуск на VPS. Не используйте `down -v` для обычного обновления.

Совокупный предел известных расходов API — **500 ₽**. Ранний локальный пилот
израсходовал **23,0777 ₽**; эта сумма вычтена из серверного потолка:
`STUDYNAV_LIVE_BUDGET_RUB=476.9223`. Чат, анализ и embeddings используют
один серверный ledger, включая индексацию корпуса и query embeddings.
Это накопительный предел, а не ежедневная квота и не лимит всего аккаунта Billing.

Известный снимок `var/budget500-after.json` содержит 32 попытки и **36,7536 ₽**.
Это исторический снимок, а не текущий остаток нового сервера. Последнее состояние
старого сервера `136.234.76.207` не проверено: подключение завершилось connection reset.
Поэтому нельзя утверждать, что все прежние серверные попытки учтены текущим ledger.
Если обнаружены расходы вне него, их нужно отдельно вычесть из доступного бюджета.

Локальный диагностический runner ограничен 25 ₽. При изменении существующего
предела сначала остановите `app`, сохраните ledger, затем согласованно обновите
серверную настройку и `budget_rub` журнала с разрешением в `budget_extensions`.
Сохраняйте попытки, расходы, неизвестные резервы и тарифы. Не удаляйте ledger
и не очищайте `usage_exceeded_reservation`: увеличение лимита не исправляет
причину этой блокировки.

`/health` проверяет приложение и MCP при старте, но не вызывает платную модель.
Успешный healthcheck не заменяет проверку пользовательского сценария с LLM.
При обновлении кода выполните `build --pull app`, затем `up -d --wait`.
Для перезагрузки Caddy после изменения файла:

```sh
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml exec caddy caddy reload --config /etc/caddy/Caddyfile
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml logs --tail 100 app caddy
```

## Общие исследования

Корпус хранится только на сервере в `/opt/project-navigator/knowledge`.
`compose.knowledge.yaml` подключает его в приложение как `/app/knowledge:ro`.
Индекс находится в постоянном томе: `/app/var/knowledge.sqlite3`
(`var/knowledge.sqlite3` относительно каталога приложения в контейнере).
Переменные `STUDYNAV_KNOWLEDGE_ROOT=/app/knowledge` и
`STUDYNAV_KNOWLEDGE_DB_PATH=/app/var/knowledge.sqlite3` задаёт overlay.
Исследования служат источниками рекомендаций; они не становятся требованиями
пользовательского проекта. Их PDF, извлечённые тексты и индекс не добавляются
в Git и не требуются для обычных offline-тестов.

Индексация запускается явно на сервере. Предпросмотр не пишет индекс и не
вызывает embeddings; `--apply --embed` разрешает запись и платные запросы
в пределах общего ledger. Для текущего корпуса используйте явные ограничения:

```sh
cd /opt/studynavigator/app
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml exec app \
  python tools/index_knowledge.py --root /app/knowledge --db /app/var/knowledge.sqlite3 \
  --max-chunks 157 --max-input-bound 550000
docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml exec app \
  python tools/index_knowledge.py --root /app/knowledge --db /app/var/knowledge.sqlite3 \
  --max-chunks 157 --max-input-bound 550000 --apply --embed
```

Вторую команду выполняют только после проверки предпросмотра. Векторы кешируются
по хешу текста, модели и размерности; повторный запуск с теми же параметрами
использует сохранённые embeddings. Новые фрагменты или другая модель требуют
новых платных запросов. Не запускайте индексацию одновременно с резервированием.

## Копия данных и восстановление

На сервере установлен `deploy/run-backup.sh`; он вызывает `deploy/backup.py`
в отдельном контейнере без сети и без серверного env-файла. Скрипт защищён
`flock`, останавливает работающий `app` и через trap возвращает его в исходное
состояние. Если приложение уже было остановлено, резервирование его не запускает.
При ошибке перезапуска нужно проверить приложение вручную. На время снимка
не запускайте другие процессы, меняющие индекс, корпус или ledger.

```sh
sudo /opt/studynavigator/app/deploy/run-backup.sh
systemctl status project-navigator-backup.timer
systemctl list-timers project-navigator-backup.timer
journalctl -u project-navigator-backup.service --no-pager -n 30
```

`project-navigator-backup.timer` включён: ежедневный запуск в 03:15–03:20 МСК
(00:15 UTC плюс случайная задержка до пяти минут). Unit-файлы находятся в `deploy/`.
Архивы сохраняются в `/opt/studynavigator/backups` с правами `0600`, каталог —
`0700`. Сначала создаётся временный файл; после проверки tar он получает итоговое
имя. Старые копии автоматически не удаляются. Для защиты от потери VPS перенесите
копии в отдельное защищённое хранилище.

Первое резервирование и восстановление во временный каталог на сервере
проверены: `quick_check` обеих SQLite вернул `ok`, восстановлены 5 источников,
157 фрагментов, 157 векторов и 5 PDF. Ledger совпал побайтно; архив имел права
`0600`. Это подтверждает проверенную копию, а не все будущие запуски таймера.

Формат архива создаёт helper, а не сырой tar тома:

| Путь в архиве | Назначение при восстановлении |
|---|---|
| `study.db` | `/app/var/study.db` |
| `uploads/` | `/app/var/uploads/` |
| `live-budget.json` | `/app/var/live-budget.json` |
| `live-budget.json.lock` | соседний lock-файл ledger |
| `knowledge.db` | `/app/var/knowledge.sqlite3` |
| `knowledge/` | новый каталог корпуса, подключённый как `/app/knowledge:ro` |
| `manifest.json` | метаданные снимка, отдельно от рабочей БД |

SQLite копируется через backup API, ledger — под его файловой блокировкой.
Серверный env-файл и API-ключи в архив не входят; храните их отдельно с защитой.

Восстанавливайте копию сначала в **новый отдельный Docker volume и новый каталог
корпуса**, сохраняя действующий том и текущий ledger. Распакуйте доверенный архив
во временный каталог и проверьте состав по таблице. Перенесите файлы в новый том,
переименовав `knowledge.db` в `knowledge.sqlite3`; владельцем данных приложения
должен быть `10001:10001`, корпус должен читаться этим пользователем. Выполните
`PRAGMA quick_check` для обеих восстановленных БД и проверьте документы и счётчики.

Только после проверки подготовьте отдельный Compose override: замените том
`app_data` на новый volume, а `STUDYNAV_KNOWLEDGE_HOST_PATH` — на новый каталог
корпуса. Сначала запускайте восстановленное приложение в offline. Не распаковывайте
архив поверх работающего тома или существующего корпуса. Не используйте `down -v`.

Перед возвратом live перенесите **актуальный** ledger и сохраните все неизвестные
резервы: восстановление старой версии журнала откатывает счётчик и может повторно
открыть потраченный лимит. Если актуального журнала нет, оставайтесь в offline
до явного решения о бюджете. После переключения проверьте `/health`, задания,
пользовательские документы, план и исследовательские источники.

Образы закреплены по digest, Python-зависимости — по версиям и хешам в
`requirements.lock`. Обновление digest выполняется отдельно вместе с проверкой.
Конфигурация опирается на [Docker Compose](https://docs.docker.com/reference/compose-file/services/),
[официальный образ Caddy](https://caddyserver.com/docs/running#docker-compose),
[защиту forwarded-заголовков Caddy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#defaults)
и [настройки доверенного прокси Uvicorn](https://uvicorn.dev/settings/#http).
