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
| review | *(следующая задача T12)* | ручное одобрение/отклонение |
| publish | *(следующая задача T16)* | загрузка на YouTube |

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
| `segment` | — | Нарезка всех источников `done` на клипы |
| `format` | `--limit N` · `--source-id N` | Форматирование клипов `cut` (максимум `N`, или только одного источника) |
| `retry` | `source_id` | Сброс источника `failed`/`skipped` обратно в `new` |

Общий флаг: `--config <path>` (по умолчанию `config.yaml`).

## Конфигурация — `config.yaml`

| Секция | Ключ | По умолчанию | Описание |
|---|---|---|---|
| `paths` | `inbox` | `data/inbox` | Куда кладёшь длинные видео |
| | `staging` | `data/staging` | Рабочая папка после ингеста |
| | `clips` | `data/clips` | Готовые клипы |
| | `review` | `data/review` | Клипы на ручном ревью |
| | `published` | `data/published` | Опубликованные |
| | `logs` | `logs` | Логи |
| | `db` | `data/db.sqlite3` | База данных |
| `video` | `min_clip_seconds` | `15` | Минимальная длительность клипа (нижняя граница нарезки) |
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

## Статусы

**Источник** (`sources.status`): `new → done → segmented` · `failed` · `skipped`
**Клип** (`clips.status`): `cut → formatting → ready` · `failed` · `review → approved/rejected` · `published`

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
