# ClipPilot

Автопилот: «длинное видео → Shorts → автопостинг на YouTube».

## Установка

1. Python 3.11+ (проверено на 3.14)
2. `python -m venv .venv`
3. `& .venv\Scripts\python.exe -m pip install -e . --no-deps` (ссылка на пакет; зависимости ставятся лениво по задачам)
4. `& .venv\Scripts\python.exe -m pip install PyYAML python-dotenv` (минимальный набор для каркаса)
5. ffmpeg в PATH — нужен начиная с T5 (нарезка) и T9 (форматирование)

## Запуск

```
& .venv\Scripts\python.exe -m clip_pilot --version
& .venv\Scripts\python.exe -m clip_pilot
```

При первом запуске создаются рабочие папки (`data/inbox`, `data/clips`, `data/review`, `data/published`, `logs`) и база `data/db.sqlite3`.

## Структура

- `src/clip_pilot/` — пакет: `cli.py`, `config.py`, `db.py` (SQLite + миграции), `repo.py` (работа с БД), `constants.py` (статусы)
- `src/clip_pilot/migrations/` — SQL-миграции (`001_initial.sql`, ...)
- `data/inbox` — клади сюда длинные видео
- `data/review` — клипы, ожидающие ручного одобрения
- `tests/` — юнит-тесты (`python -m unittest discover -s tests`)
