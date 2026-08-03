# ClipPilot

Автопилот: «длинное видео → Shorts → автопостинг на YouTube».

## Что это

CLI-приложение, которое превращает длинные видео из папки `inbox` в вертикальные клипы 9:16, готовые к публикации как YouTube Shorts.

**Пайплайн:** `source → analyze → cut → format → review? → publish`

| Стадия | Команда | Что делает |
|---|---|---|
| ingest | `scan` / `watch` | Забирает видео из `data/inbox`, считает хэш (дедуп), регистрирует источник, переносит файл в `data/staging` |
| analyze | `analyze` | ffprobe: длительность, разрешение, fps |
| cut | `segment` | PySceneDetect находит границы сцен → создаёт клипы |
| format | `format` | ffmpeg: нарезка, кроп до 9:16, re-encode |
| review | `review` | ручное одобрение/отклонение в `data/review` |
| publish | `publish` | загрузка на YouTube через Playwright-сессию |

## Установка

Требования: Python 3.11+ (проверено на 3.14), ffmpeg в PATH.

```powershell
python -m venv .venv
& .venv\Scripts\python.exe -m pip install -e . --no-deps
& .venv\Scripts\python.exe -m pip install -e .[dev]   # ruff + mypy + types-PyYAML
```

Полный набор зависимостей объявлен в `pyproject.toml` (`[project] dependencies`), но ставится лениво по мере реализации задач.

## Быстрый старт

1. Положи видео в `data/inbox`
2. Прогони пайплайн по шагам:

```powershell
& .venv\Scripts\python.exe -m clip_pilot scan          # 1. забрать файлы из inbox
& .venv\Scripts\python.exe -m clip_pilot analyze       # 2. ffprobe-метаданные
& .venv\Scripts\python.exe -m clip_pilot segment       # 3. нарезка по сценам
& .venv\Scripts\python.exe -m clip_pilot format --limit 10   # 4. ffmpeg 9:16
```

Готовые клипы — в `data/clips/<clip_id>.mp4` (1080x1920).

## Публикация (Playwright)

Загрузка идёт через браузер (Playwright) с сохранённой сессией, без Google Cloud / OAuth.

1. Установи Playwright и Chromium:
   ```powershell
   & .venv\Scripts\python.exe -m pip install playwright
   & .venv\Scripts\python.exe -m playwright install chromium
   ```
2. Сохрани сессию (откроется окно браузера — войди вручную, потом Enter):
   ```powershell
   & .venv\Scripts\python.exe -m clip_pilot auth login --name main
   ```
   Сессия сохранится в `auth/main.json`.
3. Загружай одобренные клипы:
   ```powershell
   & .venv\Scripts\python.exe -m clip_pilot publish --account main --limit 1
   ```

Заголовок клипа: `{имя источника} #{номер клипа}` (например `my_interview #3`). После публикации клип получает статус `published`, файл переносится в `data/published`, в БД создаётся запись `post` с id видео на YouTube.

⚠️ Это неофициальный путь (браузерная автоматизация против ToS) — есть риск бана аккаунта. Используй с осторожностью: разумные лимиты в день, реальные задержки, прогрев канала. Селекторы страницы загрузки могут ломаться при обновлениях YouTube — при ошибке дамп HTML/скриншот сохраняются в `logs/`.

## Команды CLI

```
& .venv\Scripts\python.exe -m clip_pilot --version
& .venv\Scripts\python.exe -m clip_pilot <command> [options]
```

| Команда | Аргументы | Описание |
|---|---|---|
| `watch` | — | Непрерывно следит за `inbox`, новые файлы сразу ингестит |
| `scan` | — | Одноразовое сканирование `inbox` |
| `analyze` | — | Анализ всех источников в статусе `new` |
| `segment` | `--min-seconds N` · `--max-seconds N` | Нарезка источников `done` на клипы (границы длины переопределяют конфиг) |
| `format` | `--limit N` · `--source-id N` | Форматирование клипов `cut` (максимум `N`, или только одного источника) |
| `review prepare` | `--limit N` · `--source-id N` | Перенос клипов `ready` в `data/review` (статус `review`) |
| `review list` | — | Список клипов на ревью |
| `review approve` | `clip_id` | Одобрить клип (`approved`) |
| `review reject` | `clip_id` · `--reason "..."` | Отклонить клип, перенос в `data/rejected` |
| `auth login` | `--name NAME` | Открыть браузер, войти на YouTube вручную, сохранить сессию в `auth/NAME.json` |
| `auth status` | — | Список аккаунтов + валидность сессий |
| `auth logout` | `--name NAME` | Удалить сохранённую сессию |
| `publish` | `--account NAME` · `--limit N` | Загрузить одобренные клипы на YouTube (Playwright) |
| `retry` | `source_id` | Сброс источника `failed`/`skipped` обратно в `new` |
| `reset` | `--source-id N` | Удаление клипов источника `segmented` и возврат в `done` (только если клипы ещё `cut`) |

Пример: нарезать все `done`-источники на клипы 1–3 минуты:

```
& .venv\Scripts\python.exe -m clip_pilot segment --min-seconds 60 --max-seconds 180
```

Общий флаг: `--config <path>` (по умолчанию `config.yaml`).

## Конфигурация — `config.yaml`

| Секция | Ключ | По умолчанию | Описание |
|---|---|---|---|
| `paths` | `inbox` | `data/inbox` | Куда кладёшь длинные видео |
| | `staging` | `data/staging` | Рабочая папка после ингеста |
| | `clips` | `data/clips` | Готовые клипы |
| | `review` | `data/review` | Клипы на ручном ревью |
| | `rejected` | `data/rejected` | Отклонённые клипы |
| | `published` | `data/published` | Опубликованные |
| | `auth` | `auth` | Сохранённые сессии аккаунтов (Playwright) |
| | `logs` | `logs` | Логи |
| | `db` | `data/db.sqlite3` | База данных |
| `video` | `min_clip_seconds` | `15` | Минимальная длительность клипа (сцены склеиваются до этого минимума) |
| | `max_clip_seconds` | `60` | Максимальная длительность клипа (верхняя граница) |
| | `ffprobe_path` | `ffprobe` | Путь к ffprobe |
| `video.scene` | `detector` | `content` | Детектор PySceneDetect: `content` / `adaptive` |
| | `threshold` | `27.0` | Чувствительность детектора (меньше = больше склеек) |
| | `min_scene_seconds` | `3.0` | Сцены короче — склеиваются с соседней |
| `video.formatting` | `width` | `1080` | Ширина клипа |
| | `height` | `1920` | Высота клипа |
| | `strategy` | `center_crop` | Форматирование: `center_crop` (MVP) / `blur_background` (M2) |
| `watcher` | `scan_interval_seconds` | `3` | Пауза между сканированиями в `watch` |
| | `settle_seconds` | `2` | Сколько файл должен быть стабильным по размеру |
| | `hash_chunk_mb` | `4` | Сколько МБ хэшировать для дедупа |
| | `extensions` | `.mp4 .mov ...` | Какие расширения считать видео |
| `logging` | `level` | `INFO` | Уровень логов |
| `review` | `default_mode` | `manual` | Режим ревью для нового источника: `manual` / `auto` |
| `playwright` | `headless` | `false` | Браузер без окна в проде; `false` для отладки и первого логина |
| | `channel` | `chrome` | Использовать реальный Chrome (меньше блоков при входе), а не встроенный Chromium |
| | `user_agent` | `""` | Свой User-Agent (например, от обычного Chrome), если нужен |
| | `timeout_seconds` | `300` | Сколько ждать загрузки/публикации (макс) |
| | `slow_mo_ms` | `0` | Замедление действий браузера (для отладки) |
| `upload` | `max_videos_per_day` | `5` | Дневной лимит публикаций на аккаунт |
| | `delay_between_seconds` | `[45, 120]` | Случайная пауза между загрузками |

## Статусы

**Источник** (`sources.status`): `new → done → segmented` · `failed` · `skipped`
**Клип** (`clips.status`): `cut → formatting → ready → review → approved/rejected` · `failed` · `published`

## Структура проекта

```
src/clip_pilot/
  cli.py            # точка входа, подкоманды
  config.py         # загрузка config.yaml
  db.py             # SQLite: WAL, миграции
  repo.py           # весь доступ к БД
  constants.py      # статусы и константы
  ingest.py         # хэширование + регистрация источников
  watcher.py        # поллинг inbox
  analyzer.py       # ffprobe-анализ
  scenes.py         # PySceneDetect + склейка/нарезка сегментов
  segmenter.py      # создание клипов
  ffmpeg_tools.py   # ffmpeg-конвейер (кроп, нарезка)
  formatter.py      # форматирование клипов
  review.py         # ревью: перенос в review/, approve/reject
  uploader.py       # оркестратор публикации (Protocol Uploader)
  playwright_uploader.py  # загрузка через Playwright-сессию
  migrations/       # SQL-миграции (001_initial.sql, ...)
```

## Тесты и качество

```powershell
& .venv\Scripts\ruff.exe check .              # линтер (E, F, I, UP, B)
& .venv\Scripts\ruff.exe format --check .     # форматтер
& .venv\Scripts\mypy.exe src/clip_pilot       # типы
& .venv\Scripts\python.exe -m unittest discover -s tests   # юнит-тесты
```

## Известные ограничения

- `center_crop` из горизонтального видео теряет до ~72% кадра по бокам (для вертикальных/квадратных — ок). Умный кроп — M2.
- fps не пересэмплируется — сохраняется исходный, YouTube конвертирует сам.
- Обрезка тишины (silenceremove) запланирована на M2.
- Публикация через Playwright — неофициальный путь (риск бана), селекторы страницы загрузки хрупкие.
