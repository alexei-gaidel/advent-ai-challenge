# API проекта: MCP-серверы и веб-порты

Сгенерировано `31-dev-assistant/gen_docs.py`. Список инструментов каждого сервера получен вживую: клиент запускает сервер по stdio, делает `initialize` и `tools/list`. `?` после имени параметра — необязательный.

## `16-mcp-client/mcp_server.py` — advent-demo-server 0.1.0

Запуск: `python3 16-mcp-client/mcp_server.py` (stdio, JSON-RPC 2.0). Инструментов: 3.

| Инструмент | Параметры | Описание |
|---|---|---|
| `ping` | — | Проверка связи: возвращает pong и версию сервера. |
| `echo` | `text: string` | Возвращает переданный текст без изменений. |
| `now` | `format?: string` | Текущее время сервера в выбранном формате. |

## `17-mcp-tool/git_server.py` — advent-git-server 1.0.0

Запуск: `python3 17-mcp-tool/git_server.py` (stdio, JSON-RPC 2.0). Инструментов: 4.

| Инструмент | Параметры | Описание |
|---|---|---|
| `git_log` | `limit?: integer`, `grep?: string`, `path?: string` | История коммитов репозитория Advent AI Challenge. Полезно, чтобы узнать, что и когда делалось в проекте. |
| `git_show` | `ref: string` | Подробности одного коммита: автор, дата, сообщение и список изменённых файлов. |
| `git_search` | `query: string`, `path?: string`, `limit?: integer` | Поиск строки в содержимом файлов репозитория. Отвечает, где в коде встречается нужное понятие. |
| `git_summary` | — | Сводка по репозиторию: число коммитов, папки заданий, дата последнего коммита. |

## `18-scheduled-mcp/market_server.py` — advent-market-server 1.0.0

Запуск: `python3 18-scheduled-mcp/market_server.py` (stdio, JSON-RPC 2.0). Инструментов: 6.

| Инструмент | Параметры | Описание |
|---|---|---|
| `market_digest` | `period?: string` | Сводка по курсам криптовалют: текущая цена и изменение за сутки по каждой монете. |
| `market_history` | `symbol: string`, `limit?: integer` | Ряд наблюдений по одному инструменту: время и цена. |
| `collect_now` | — | Снять наблюдение с биржи немедленно, не дожидаясь расписания. |
| `list_jobs` | — | Задания планировщика: вид, периодичность, время следующего запуска. |
| `list_runs` | `limit?: integer` | Журнал запусков планировщика: когда сработало задание, с каким результатом. По нему видно, соблюдалось ли расписание. |
| `schedule_reminder` | `text: string`, `after_minutes: number` | Отложенное напоминание: сработает один раз через указанное число минут и выключится. |

## `19-mcp-pipeline/pipeline_server.py` — advent-pipeline-server 1.0.0

Запуск: `python3 19-mcp-pipeline/pipeline_server.py` (stdio, JSON-RPC 2.0). Инструментов: 4.

| Инструмент | Параметры | Описание |
|---|---|---|
| `tickets_fetch` | `url?: string` | ШАГ 1 из 3. Снимает страницу события на Яндекс Афише и сохраняет снимок предложения: категории билетов, цены, статус продаж. Возвращает snapshot_id, который нужен шагу 2. |
| `changes_summarize` | `snapshot_id: string` | ШАГ 2 из 3. Сравнивает снимок с предыдущим и описывает изменения: подешевел ли минимальный билет, добавились ли категории, изменился ли статус продаж. Возвращает report_id для шага 3. |
| `report_save` | `report_id: string`, `notify?: boolean` | ШАГ 3 из 3. Сохраняет отчёт в файл и, если включено уведомление и есть изменения, отправляет его в Telegram. Принимает report_id, полученный на шаге 2. |
| `pipeline_runs` | `limit?: integer` | Журнал прогонов пайплайна: когда выполнялся и что нашёл. |

## `20-mcp-orchestration/mcp_server.py`

Не запустился: сервер не ответил

## `20-mcp-orchestration/money_server.py` — advent-money-server 1.0.0

Запуск: `python3 20-mcp-orchestration/money_server.py` (stdio, JSON-RPC 2.0). Инструментов: 2.

| Инструмент | Параметры | Описание |
|---|---|---|
| `rates` | `currency: string` | Официальный курс ЦБ РФ для валюты (EUR, USD, CNY, KZT …) к рублю. Возвращает rate_id, который нужен инструменту budget. |
| `budget` | `items: array`, `rate_id: string` | Складывает статьи расходов в рублях и пересчитывает итог в валюту по rate_id из rates этого сервера. Возвращает budget_id. |

## `20-mcp-orchestration/notes_server.py` — advent-notes-server 1.0.0

Запуск: `python3 20-mcp-orchestration/notes_server.py` (stdio, JSON-RPC 2.0). Инструментов: 4.

| Инструмент | Параметры | Описание |
|---|---|---|
| `create` | `title: string`, `text: string`, `sources?: array` | Создаёт заметку. sources — идентификаторы данных, на которых она основана (forecast_id, budget_id и т. п.). Возвращает note_id. |
| `checklist_add` | `note_id: string`, `items: array` | Добавляет пункты чек-листа в заметку по note_id. |
| `read` | `note_id: string` | Возвращает заметку целиком: текст, источники, чек-лист. |
| `search` | `query: string` | Ищет среди сохранённых заметок по словам из заголовка и текста. |

## `20-mcp-orchestration/weather_server.py` — advent-weather-server 1.0.0

Запуск: `python3 20-mcp-orchestration/weather_server.py` (stdio, JSON-RPC 2.0). Инструментов: 2.

| Инструмент | Параметры | Описание |
|---|---|---|
| `search` | `query: string` | Ищет город по названию и возвращает кандидатов с place_id, регионом и страной. place_id нужен инструменту forecast. |
| `forecast` | `place_id: string`, `date: string` | Прогноз погоды на один день: температура, осадки, ветер. Принимает place_id из search этого сервера и дату YYYY-MM-DD (не дальше 15 дней вперёд). Возвращает forecast_id. |

## `31-dev-assistant/project_server.py` — advent-project-server 1.0.0

Запуск: `python3 31-dev-assistant/project_server.py` (stdio, JSON-RPC 2.0). Инструментов: 6.

| Инструмент | Параметры | Описание |
|---|---|---|
| `git_branch` | — | Текущая git-ветка репозитория, её последний коммит и отставание от origin. |
| `git_status` | — | Состояние рабочей копии: изменённые, добавленные и неотслеживаемые файлы. Отвечает на «что сейчас не закоммичено». |
| `git_files` | `path?: string`, `limit?: integer` | Файлы проекта под git (без игнорируемых баз и ключей). Без path — папки верхнего уровня с числом файлов; с path — файлы этой папки, например 17-mcp-tool. |
| `git_diff` | `path?: string`, `staged?: boolean` | Незакоммиченные изменения в отслеживаемых файлах. По умолчанию — сводка по файлам; с path — сам diff файла или папки. |
| `git_log` | `limit?: integer`, `path?: string` | Последние коммиты: хеш, дата, сообщение. Можно ограничить папкой. |
| `read_file` | `path: string`, `start?: integer`, `lines?: integer` | Содержимое файла проекта с номерами строк. Для вопросов о коде, на которые документация не отвечает. |

## Порты веб-интерфейсов

Правило проекта: порт = 8000 + номер дня. Значения прочитаны из констант `PORT`.

| Папка | Файл | Порт |
|---|---|---|
| `01-llm-api-request` | `web.py` | 8000 |
| `03-prompting-strategies` | `web.py` | 8003 |
| `04-temperature` | `web.py` | 8004 |
| `05-model-versions` | `web.py` | 8005 |
| `06-agent` | `web.py` | 8006 |
| `07-persistent-context` | `web.py` | 8007 |
| `09-context-compression` | `web.py` | 8009 |
| `10-context-strategies` | `web.py` | 8010 |
| `11-memory-model` | `web.py` | 8011 |
| `12-personalization` | `web.py` | 8012 |
| `13-task-state` | `web.py` | 8013 |
| `14-invariants` | `web.py` | 8014 |
| `15-lifecycle` | `web.py` | 8015 |
| `16-mcp-client` | `web.py` | 8016 |
| `17-mcp-tool` | `web.py` | 8017 |
| `18-scheduled-mcp` | `web.py` | 8018 |
| `19-mcp-pipeline` | `web.py` | 8019 |
| `20-mcp-orchestration` | `web.py` | 8020 |
| `21-document-index` | `web.py` | 8021 |
| `22-first-rag` | `web.py` | 8022 |
| `23-rerank-filter` | `web.py` | 8023 |
| `24-citations` | `web.py` | 8024 |
| `25-rag-chat` | `web.py` | 8025 |
| `26-local-llm` | `web.py` | 8026 |
| `28-local-rag` | `web.py` | 8028 |
| `29-local-optimization` | `web.py` | 8029 |
| `30-private-llm-service` | `server.py` | 8030 |
| `31-dev-assistant` | `web.py` | 8031 |
