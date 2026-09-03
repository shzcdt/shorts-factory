"""Telegram bot: a remote control for the clip-pilot pipeline.

The bot never touches video files itself: long-running stages are executed
as ``python -m clip_pilot <stage>`` subprocesses, while quick queries read
SQLite directly (WAL allows concurrent readers). Access is restricted to
the Telegram user ids listed in ``config.yaml`` (``telegram.allowed_users``)
plus ids added at runtime via /adduser (stored in the ``settings`` table).
The first configured id is the owner — only the owner can manage access.

Session files (``auth/<name>.json``) and small videos (up to
``telegram.max_inbox_mb``) can be sent to the bot: sessions are stored in
the auth directory, videos are dropped into the inbox for ingestion.
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from clip_pilot import db, repo
from clip_pilot.config import Config
from clip_pilot.constants import CLIP_STATUS_APPROVED, CLIP_STATUS_REVIEW

logger = logging.getLogger("clip_pilot.telegram_bot")

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
ALLOWED_USERS_SETTING = "telegram_allowed_users"
MESSAGE_LIMIT = 4000
MAX_LIST_ITEMS = 15

STAGES = {
    "scan": "Сканирование inbox",
    "analyze": "Анализ (ffprobe)",
    "segment": "Нарезка на клипы",
    "format": "Формат 9:16",
}
PIPELINE_ORDER = ["scan", "analyze", "segment", "format"]

STAGE_ARGS = {
    "scan": ["scan"],
    "analyze": ["analyze"],
    "segment": ["segment"],
    "format": ["format"],
}

BTN_STATUS = "📊 Статус"
BTN_SOURCES = "🎬 Источники"
BTN_REVIEW = "👀 Ревью"
BTN_PUBLISH = "🚀 Публиковать"
BTN_STAGES = "⚙️ Стадии"
BTN_ACCOUNTS = "👤 Аккаунты"
BTN_HELP = "❓ Помощь"
BTN_UPLOAD = "📥 Загрузить видео"

MENU_KEYBOARD = [
    [BTN_STATUS, BTN_SOURCES],
    [BTN_REVIEW, BTN_PUBLISH],
    [BTN_STAGES, BTN_ACCOUNTS],
    [BTN_UPLOAD, BTN_HELP],
]


def _owner_id(config: Config) -> int:
    """Return the owner id: the first entry of telegram.allowed_users."""
    users = config.telegram.get("allowed_users", [])
    return int(users[0]) if users else 0


def _setting_allowed_ids(conn: sqlite3.Connection) -> set[int]:
    """Return user ids added at runtime via /adduser."""
    raw = repo.get_setting(conn, ALLOWED_USERS_SETTING)
    if not raw:
        return set()
    try:
        return {int(u) for u in json.loads(raw)}
    except (ValueError, TypeError):
        return set()


def _allowed_ids(conn: sqlite3.Connection, config: Config) -> set[int]:
    """Union of config-file ids and runtime-added ids."""
    configured = {int(u) for u in config.telegram.get("allowed_users", [])}
    return configured | _setting_allowed_ids(conn)


def _add_allowed_id(conn: sqlite3.Connection, user_id: int) -> None:
    """Persist a runtime-added user id in the settings table."""
    ids = _setting_allowed_ids(conn)
    ids.add(user_id)
    repo.set_setting(conn, ALLOWED_USERS_SETTING, json.dumps(sorted(ids)))


def _remove_allowed_id(conn: sqlite3.Connection, user_id: int) -> None:
    ids = _setting_allowed_ids(conn)
    ids.discard(user_id)
    repo.set_setting(conn, ALLOWED_USERS_SETTING, json.dumps(sorted(ids)))


def format_help() -> str:
    """Build the detailed /help message with a usage roadmap."""
    return (
        "<b> ClipPilot — автопилот YouTube Shorts</b>\n\n"
        "Этот бот управляет конвейером: длинное видео → нарезка на клипы "
        "9:16 → ревью → публикация на YouTube.\n\n"
        "<b>🚦 Быстрый старт (роадмап)</b>\n"
        "1. Пришли боту видеофайл (до 20 МБ) — он попадёт в папку inbox. "
        "Файлы побольше скопируй на сервер в <code>data/inbox</code> вручную.\n"
        "2. <b>⚙️ Стадии → ▶️ Весь пайплайн</b> — бот сам прогонит все стадии: "
        "сканирование → анализ → нарезка → формат 9:16.\n"
        "3. <b>👀 Ревью</b> — бот пришлёт первый готовый клип видео. Смотри и жми "
        "✅ (одобрить) или ❌ (отклонить). Так по очереди все клипы.\n"
        "4. <b>🚀 Публиковать</b> — одобренные клипы уедут на YouTube, бот пришлёт ссылки.\n\n"
        "<b>📋 Кнопки меню</b>\n"
        f"• {BTN_STATUS} — счётчики источников/клипов, лимиты публикаций, место на диске\n"
        f"• {BTN_SOURCES} — последние источники и сколько клипов из них нарезано\n"
        f"• {BTN_REVIEW} — ревью клипов по одному\n"
        f"• {BTN_PUBLISH} — публикация одобренных клипов (выбор аккаунта)\n"
        f"• {BTN_STAGES} — запуск отдельных стадий конвейера\n"
        f"• {BTN_ACCOUNTS} — аккаунты YouTube и их сессии\n"
        f"• {BTN_UPLOAD} — как загрузить видео\n\n"
        "<b>👤 Аккаунты YouTube</b>\n"
        "Один раз на компьютере с браузером выполни "
        "<code>python -m clip_pilot auth login --name &lt;имя&gt;</code> и войди в Google. "
        "Затем пришли боту файл <code>auth/&lt;имя&gt;.json</code> — сессия сохранится на сервере, "
        "и аккаунт появится в 🚀 Публиковать.\n\n"
        "<b>🔐 Доступ (только для владельца)</b>\n"
        "<code>/adduser &lt;id&gt;</code> — разрешить доступ ещё одному человеку\n"
        "<code>/deluser &lt;id&gt;</code> — отозвать доступ\n"
        "<code>/users</code> — кто имеет доступ сейчас\n\n"
        "<b>⚠️ Лимиты</b>\n"
        "Публикаций в день: по лимиту из конфига на каждый аккаунт. "
        "Не увеличивай резко — YouTube банит за подозрительную активность."
    )


def format_upload_help() -> str:
    """Build the video-upload instructions message."""
    return (
        "<b>📥 Как загрузить видео</b>\n\n"
        "1. Просто пришли видеофайл <b>как файл</b> (не как видео-сообщение!) в этот чат.\n"
        "2. Лимит Telegram для ботов — 20 МБ. Больше — скопируй на сервер напрямую в "
        "<code>data/inbox</code>.\n"
        "3. После загрузки запусти <b>⚙️ Стадии → ▶️ Весь пайплайн</b> или кнопкой "
        "<b>⚙️ Стадии</b> по шагам.\n\n"
        "Поддерживаются: .mp4 .mov .mkv .avi .webm .flv .m4v"
    )


def _clip_caption(conn: sqlite3.Connection, clip: dict, position: int, total: int) -> str:
    """Build a short caption for a clip awaiting review."""
    source = repo.get_source(conn, clip["source_id"])
    source_title = source["title"] if source else f"source {clip['source_id']}"
    minutes = int(clip["start_time"] // 60)
    seconds = int(clip["start_time"] % 60)
    return (
        f"<b>👀 Клип #{clip['id']}</b> ({position} из {total})\n"
        f"Источник: {source_title}\n"
        f"Начало: {minutes}:{seconds:02d} · "
        f"длительность {clip['end_time'] - clip['start_time']:.0f}с\n\n"
        "Годен? ✅ — опубликовать позже, ❌ — в отклонённые."
    )


def _status_text(conn: sqlite3.Connection, config: Config) -> str:
    """Build the status message: entity counts, quota and disk usage."""
    source_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM sources GROUP BY status"
    ).fetchall()
    clip_rows = conn.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status").fetchall()
    accounts = repo.get_accounts(conn)

    lines = ["📊 <b>Статус пайплайна</b>\n\n<b>Источники</b>"]
    lines += [f"  • {row['status']}: {row['n']}" for row in source_rows] or ["  • нет"]
    lines.append("\n<b>Клипы</b>")
    lines += [f"  • {row['status']}: {row['n']}" for row in clip_rows] or ["  • нет"]

    lines.append("\n<b>Аккаунты</b> (публикаций сегодня)")
    if accounts:
        for acc in accounts:
            lines.append(f"  • {acc['name']}: {repo.count_posts_today(conn, acc['id'])}")
    else:
        lines.append("  • нет")

    quota = int(config.upload.get("max_videos_per_day", 5) or 5)
    lines.append(f"\nЛимит: {quota} публикаций/день на аккаунт")
    lines.append(
        f"Диск: inbox {_disk_usage_mb(config.get_path('inbox'))} МБ · "
        f"published {_disk_usage_mb(config.get_path('published'))} МБ"
    )
    return "\n".join(lines)


def _disk_usage_mb(path: Path) -> int:
    """Return the total size of files under a directory in megabytes."""
    total = 0
    if path.is_file():
        return path.stat().st_size // (1024 * 1024)
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total // (1024 * 1024)


def _sources_text(conn: sqlite3.Connection) -> str:
    """Build the sources list message."""
    rows = conn.execute(
        "SELECT s.*, COUNT(c.id) AS clips FROM sources s "
        "LEFT JOIN clips c ON c.source_id = s.id "
        "GROUP BY s.id ORDER BY s.created_at DESC LIMIT ?",
        (MAX_LIST_ITEMS,),
    ).fetchall()
    if not rows:
        return "Пока нет источников. Пришли видео через 📥 Загрузить видео."
    lines = ["🎬 <b>Источники (последние)</b>\n"]
    status_icons = {"new": "🆕", "done": "🔍", "segmented": "✂️", "failed": "💥", "skipped": "⏭"}
    for row in rows:
        icon = status_icons.get(row["status"], "•")
        lines.append(f"{icon} #{row['id']} <b>{row['title']}</b> — {row['clips']} клип(ов)")
    lines.append("\nДетали клипов: /clips &lt;id источника&gt;")
    return "\n".join(lines)


def _clips_text(conn: sqlite3.Connection, source_id: int) -> str:
    """Build the clip list for one source."""
    clips = repo.get_clips_by_source(conn, source_id)
    if not clips:
        return f"У источника #{source_id} нет клипов."
    icons = {"published": "🌍", "approved": "✅", "review": "👀", "rejected": "❌", "ready": "🎞"}
    lines = [f"<b>Клипы источника #{source_id}</b>\n"]
    for clip in clips[:MAX_LIST_ITEMS]:
        icon = icons.get(clip["status"], "•")
        span = f"{clip['start_time']:.0f}-{clip['end_time']:.0f}с"
        lines.append(f"{icon} #{clip['id']} [{clip['status']}] {span}")
    if len(clips) > MAX_LIST_ITEMS:
        lines.append(f"… и ещё {len(clips) - MAX_LIST_ITEMS}")
    return "\n".join(lines)


def _accounts_text(conn: sqlite3.Connection, config: Config) -> str:
    """Build the accounts message with session file presence."""
    accounts = repo.get_accounts(conn)
    if not accounts:
        return (
            "<b>👤 Аккаунтов пока нет</b>\n\n"
            "Как добавить:\n"
            "1. На компьютере: <code>python -m clip_pilot auth login --name main</code>\n"
            "2. Войди в Google в открывшемся окне\n"
            "3. Пришли сюда файл <code>auth/main.json</code>"
        )
    auth_dir = config.get_path("auth")
    lines = ["👤 <b>Аккаунты</b>\n"]
    for acc in accounts:
        session = auth_dir / f"{acc['name']}.json"
        state = "✅ сессия на месте" if session.exists() else "❌ нет сессии"
        lines.append(
            f"• <b>{acc['name']}</b> — {state}\n"
            f"  публикций сегодня: {repo.count_posts_today(conn, acc['id'])}"
        )
    return "\n".join(lines)


def _safe_filename(name: str) -> str:
    """Strip path components from a client-provided filename."""
    return Path(name).name


def _save_session_file(config: Config, filename: str, data: bytes) -> Path:
    """Save an uploaded Playwright session JSON into the auth directory."""
    clean = _safe_filename(filename)
    if not clean.endswith(".json"):
        raise ValueError("Session file must be a .json storage state")
    auth_dir = config.get_path("auth")
    auth_dir.mkdir(parents=True, exist_ok=True)
    target = auth_dir / clean
    target.write_bytes(data)
    logger.info("Session file saved: %s (%s bytes)", target, len(data))
    return target


def _save_incoming_video(config: Config, filename: str, data: bytes) -> Path | None:
    """Save an incoming video into the inbox; None if it is not a video."""
    clean = _safe_filename(filename)
    video_exts = tuple(config.watcher.get("extensions", [".mp4"]))
    if not clean.lower().endswith(video_exts):
        return None
    inbox = config.get_path("inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / clean
    target.write_bytes(data)
    logger.info("Incoming video saved to inbox: %s (%s bytes)", target, len(data))
    return target


async def _run_stage(config_path: str, args: list[str]) -> tuple[int, str]:
    """Run a pipeline stage as a CLI subprocess and capture its output."""
    cmd = [sys.executable, "-m", "clip_pilot", "--config", config_path, *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(Path(config_path).resolve().parent),
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")[-MESSAGE_LIMIT:]
    return proc.returncode or 0, output


def run_bot(config_path: str) -> None:
    """Start the Telegram bot (blocking call).

    Args:
        config_path: Path to config.yaml; subprocess stages reuse it.

    Raises:
        RuntimeError: If TELEGRAM_BOT_TOKEN is not configured or the
            allowed-users whitelist is empty.
    """
    from dotenv import load_dotenv

    load_dotenv()
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} is not set (.env)")

    from aiogram import Bot, Dispatcher, F
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.filters import Command, CommandObject, CommandStart
    from aiogram.types import (
        CallbackQuery,
        FSInputFile,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        KeyboardButton,
        Message,
        ReplyKeyboardMarkup,
    )

    config = Config.load(config_path)
    if not config.telegram.get("allowed_users"):
        raise RuntimeError(
            "telegram.allowed_users is empty in config.yaml — add your Telegram user id"
        )

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    reply_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=b) for b in row] for row in MENU_KEYBOARD],
        resize_keyboard=True,
    )

    def open_conn() -> sqlite3.Connection:
        return db.init_db(config.get_path("db"))

    def is_allowed(conn: sqlite3.Connection, user_id: int) -> bool:
        return user_id in _allowed_ids(conn, config)

    async def deny(message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        logger.warning("Denied access for user %s", uid)
        await message.answer(f"⛔ Нет доступа. Твой id: {uid}")

    def main_menu() -> ReplyKeyboardMarkup:
        return reply_kb

    async def send_status(message: Message) -> None:
        conn = open_conn()
        try:
            text = _status_text(conn, config)
        finally:
            conn.close()
        await message.answer(text, reply_markup=main_menu())

    async def send_sources(message: Message) -> None:
        conn = open_conn()
        try:
            text = _sources_text(conn)
        finally:
            conn.close()
        await message.answer(text)

    async def send_accounts(message: Message) -> None:
        conn = open_conn()
        try:
            text = _accounts_text(conn, config)
        finally:
            conn.close()
        await message.answer(text)

    review_state: dict[int, int] = {}

    async def send_next_review(chat_id: int) -> None:
        conn = open_conn()
        try:
            clips = repo.get_clips_by_status(conn, CLIP_STATUS_REVIEW)
            if not clips:
                review_state.pop(chat_id, None)
                await bot.send_message(
                    chat_id,
                    "🎉 Очередь ревью пуста!\n\nЗапусти ⚙️ Стадии или 🚀 Публиковать.",
                    reply_markup=main_menu(),
                )
                return
            clip = clips[0]
            total = len(clips)
            path = Path(clip["path"])
            if not path.exists():
                await bot.send_message(chat_id, f"💥 Файл клипа #{clip['id']} не найден: {path}")
                return
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Одобрить", callback_data=f"appr:{clip['id']}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить", callback_data=f"rej:{clip['id']}"
                        ),
                    ],
                    [InlineKeyboardButton(text="⏭ Показать заново", callback_data="next")],
                ]
            )
            review_state[chat_id] = clip["id"]
            await bot.send_video(
                chat_id,
                video=FSInputFile(path),
                caption=_clip_caption(conn, clip, 1, total),
                reply_markup=keyboard,
            )
        finally:
            conn.close()

    async def run_stages_with_progress(message: Message, stages: list[str]) -> None:
        """Run stages sequentially, editing one progress message."""
        progress = await message.answer(f"⚙️ Запускаю <b>{STAGES[stages[0]]}</b>…")
        for index, stage in enumerate(stages):
            try:
                await progress.edit_text(
                    f"⚙️ <b>{STAGES[stage]}</b> ({index + 1}/{len(stages)})… ⏳"
                )
            except Exception:
                pass
            code, output = await _run_stage(config_path, STAGE_ARGS[stage])
            if code != 0:
                tail = output[-1500:]
                await progress.edit_text(
                    f"💥 <b>{STAGES[stage]}</b> упал (код {code}):\n<code>{tail}</code>"
                )
                return
            done = f"✅ <b>{STAGES[stage]}</b> — готово ({index + 1}/{len(stages)})"
            await progress.edit_text(done)
            if index < len(stages) - 1:
                await asyncio.sleep(0.5)
                await progress.edit_text(
                    f"✅ {STAGES[stage]} — готово\n⚙️ <b>{STAGES[stages[index + 1]]}</b>… ⏳"
                )
        await progress.edit_text(
            "🏁 <b>Готово!</b> Пайплайн завершён.\n"
            "Дальше: 👀 Ревью — посмотри клипы, потом 🚀 Публиковать."
        )

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        conn = open_conn()
        try:
            allowed = is_allowed(conn, message.from_user.id if message.from_user else 0)
        finally:
            conn.close()
        if not allowed:
            return await deny(message)
        await message.answer(
            "👋 Привет! Это <b>ClipPilot</b> — автопилот YouTube Shorts.\n\n"
            "Положи видео (📥), прогони пайплайн (⚙️), посмотри клипы (👀) "
            "и опубликуй (🚀).\n\nПодробности — в ❓ Помощь.",
            reply_markup=main_menu(),
        )

    @dp.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(format_help())

    @dp.message(Command("status"))
    async def status_cmd(message: Message) -> None:
        conn = open_conn()
        try:
            allowed = is_allowed(conn, message.from_user.id if message.from_user else 0)
        finally:
            conn.close()
        if not allowed:
            return await deny(message)
        await send_status(message)

    @dp.message(Command("sources"))
    async def sources_cmd(message: Message) -> None:
        await send_sources(message)

    @dp.message(Command("clips"))
    async def clips_cmd(message: Message, command: CommandObject) -> None:
        if not command.args or not command.args.strip().isdigit():
            await message.answer("Использование: /clips &lt;source_id&gt;")
            return
        conn = open_conn()
        try:
            text = _clips_text(conn, int(command.args.strip()))
        finally:
            conn.close()
        await message.answer(text)

    @dp.message(Command("review"))
    async def review_cmd(message: Message) -> None:
        await send_next_review(message.chat.id)

    @dp.message(Command("approve_all"))
    async def approve_all_cmd(message: Message) -> None:
        conn = open_conn()
        try:
            clips = repo.get_clips_by_status(conn, CLIP_STATUS_REVIEW)
            for clip in clips:
                repo.update_clip_status(
                    conn, clip["id"], CLIP_STATUS_APPROVED, review_decision="approved"
                )
                repo.add_event(
                    conn, entity_type="clip", entity_id=clip["id"], event_type="clip_approved"
                )
            conn.commit()
        finally:
            conn.close()
        await message.answer(f"✅ Одобрено клипов: {len(clips)}")

    @dp.message(Command("publish"))
    async def publish_cmd(message: Message, command: CommandObject) -> None:
        parts = (command.args or "").split()
        account = parts[0] if parts else "main"
        args = ["publish", "--account", account]
        if len(parts) > 1 and parts[1].isdigit():
            args += ["--limit", parts[1]]
        conn = open_conn()
        try:
            known = repo.get_account_by_name(conn, account) is not None
        finally:
            conn.close()
        if not known:
            await message.answer(f"Аккаунт {account!r} не найден. Смотри 👤 Аккаунты.")
            return
        await message.answer(f"🚀 Публикую через <b>{account}</b>…")
        code, output = await _run_stage(config_path, args)
        if code == 0:
            await message.answer(f"✅ Публикация завершена:\n<pre>{output}</pre>")
        else:
            await message.answer(f"💥 Публикация упала (код {code}):\n<pre>{output}</pre>")

    @dp.message(Command("adduser"))
    async def adduser_cmd(message: Message, command: CommandObject) -> None:
        if message.from_user is None or message.from_user.id != _owner_id(config):
            await message.answer("⛔ Только владелец может управлять доступом")
            return
        if not command.args or not command.args.strip().lstrip("-").isdigit():
            await message.answer("Использование: /adduser &lt;telegram_id&gt;")
            return
        user_id = int(command.args.strip())
        conn = open_conn()
        try:
            _add_allowed_id(conn, user_id)
            conn.commit()
        finally:
            conn.close()
        await message.answer(f"✅ Доступ выдан: {user_id}")

    @dp.message(Command("deluser"))
    async def deluser_cmd(message: Message, command: CommandObject) -> None:
        if message.from_user is None or message.from_user.id != _owner_id(config):
            await message.answer("⛔ Только владелец может управлять доступом")
            return
        if not command.args or not command.args.strip().isdigit():
            await message.answer("Использование: /deluser &lt;telegram_id&gt;")
            return
        user_id = int(command.args.strip())
        conn = open_conn()
        try:
            _remove_allowed_id(conn, user_id)
            conn.commit()
        finally:
            conn.close()
        await message.answer(f"🚫 Доступ отозван: {user_id}")

    @dp.message(Command("users"))
    async def users_cmd(message: Message) -> None:
        conn = open_conn()
        try:
            configured = {int(u) for u in config.telegram.get("allowed_users", [])}
            extra = _setting_allowed_ids(conn)
        finally:
            conn.close()
        lines = ["🔐 <b>Доступ</b>\n"]
        for uid in sorted(configured):
            lines.append(f"• {uid} 👑 владелец" if uid == _owner_id(config) else f"• {uid}")
        for uid in sorted(extra - configured):
            lines.append(f"• {uid}")
        await message.answer("\n".join(lines))

    async def stage_menu(message: Message) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="▶️ Весь пайплайн", callback_data="pipe:all")],
                [InlineKeyboardButton(text="1️⃣ Сканировать inbox", callback_data="pipe:scan")],
                [InlineKeyboardButton(text="2️⃣ Анализ", callback_data="pipe:analyze")],
                [InlineKeyboardButton(text="3️⃣ Нарезка", callback_data="pipe:segment")],
                [InlineKeyboardButton(text="4️⃣ Формат 9:16", callback_data="pipe:format")],
            ]
        )
        await message.answer(
            "⚙️ <b>Стадии конвейера</b>\n\n"
            "▶️ Весь пайплайн — прогнать всё по очереди.\n"
            "Или по шагам 1→2→3→4:",
            reply_markup=keyboard,
        )

    async def publish_menu(message: Message) -> None:
        conn = open_conn()
        try:
            accounts = repo.get_accounts(conn)
            approved = repo.get_clips_by_status(conn, CLIP_STATUS_APPROVED)
        finally:
            conn.close()
        if not accounts:
            await message.answer("Нет аккаунтов. Как добавить — в 👤 Аккаунтах.")
            return
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🚀 {acc['name']}",
                    callback_data=f"pub:{acc['name']}",
                )
            ]
            for acc in accounts
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        await message.answer(
            f"🚀 <b>Публикация</b>\n\nОдобрено клипов: {len(approved)}\nВыбери аккаунт:",
            reply_markup=keyboard,
        )

    @dp.message(F.text)
    async def text_router(message: Message) -> None:
        if message.text is None:
            return
        conn = open_conn()
        try:
            allowed = is_allowed(conn, message.from_user.id if message.from_user else 0)
        finally:
            conn.close()
        if not allowed:
            return await deny(message)

        text = message.text
        handlers = {
            BTN_STATUS: send_status,
            BTN_SOURCES: send_sources,
            BTN_ACCOUNTS: send_accounts,
            BTN_REVIEW: lambda m: send_next_review(m.chat.id),
            BTN_STAGES: stage_menu,
            BTN_PUBLISH: publish_menu,
            BTN_UPLOAD: lambda m: m.answer(format_upload_help()),
            BTN_HELP: lambda m: m.answer(format_help()),
        }
        handler = handlers.get(text)
        if handler is not None:
            await handler(message)
            return

        if text.startswith("/"):
            await message.answer("Неизвестная команда. Смотри ❓ Помощь")
        else:
            await message.answer("Не понял 🤔 Пришли видеофайл или воспользуйся кнопками меню.")

    @dp.callback_query(F.data)
    async def callbacks(query: CallbackQuery) -> None:
        if query.from_user is None:
            return
        conn = open_conn()
        try:
            allowed = is_allowed(conn, query.from_user.id)
        finally:
            conn.close()
        if not allowed:
            await query.answer("Нет доступа", show_alert=True)
            return
        if query.message is None or not isinstance(query.message, Message):
            return
        chat_id = query.message.chat.id
        data = query.data or ""

        if data.startswith("pipe:"):
            stage = data.partition(":")[2]
            stages = PIPELINE_ORDER if stage == "all" else [stage]
            await query.answer()
            await run_stages_with_progress(query.message, stages)
            return

        if data.startswith("pub:"):
            account = data.partition(":")[2]
            await query.answer()
            await query.message.answer(f"🚀 Публикую через <b>{account}</b>…")
            code, output = await _run_stage(config_path, ["publish", "--account", account])
            if code == 0:
                await query.message.answer(f"✅ Публикация завершена:\n<pre>{output}</pre>")
            else:
                await query.message.answer(
                    f"💥 Публикация упала (код {code}):\n<pre>{output}</pre>"
                )
            return

        if data == "next":
            await query.answer()
            await send_next_review(chat_id)
            return

        action, _, clip_id_raw = data.partition(":")
        if not clip_id_raw.isdigit():
            await query.answer("Непонятная команда")
            return
        clip_id = int(clip_id_raw)
        conn = open_conn()
        try:
            if action == "appr":
                repo.update_clip_status(
                    conn, clip_id, CLIP_STATUS_APPROVED, review_decision="approved"
                )
                repo.add_event(
                    conn, entity_type="clip", entity_id=clip_id, event_type="clip_approved"
                )
                conn.commit()
                await query.answer("Одобрен ✅")
            elif action == "rej":
                repo.update_clip_status(
                    conn,
                    clip_id,
                    "rejected",
                    rejected_reason="rejected via telegram",
                    review_decision="rejected",
                )
                repo.add_event(
                    conn, entity_type="clip", entity_id=clip_id, event_type="clip_rejected"
                )
                conn.commit()
                await query.answer("Отклонен ❌")
            else:
                await query.answer()
                return
        finally:
            conn.close()
        await send_next_review(chat_id)

    @dp.message(F.video | F.document)
    async def incoming_file(message: Message) -> None:
        conn = open_conn()
        try:
            allowed = is_allowed(conn, message.from_user.id if message.from_user else 0)
        finally:
            conn.close()
        if not allowed:
            return await deny(message)
        max_mb = int(config.telegram.get("max_inbox_mb", 20) or 20)
        file_obj = message.video if message.video is not None else message.document
        if file_obj is None:
            return
        filename = file_obj.file_name or ""
        size_mb = (file_obj.file_size or 0) / 1e6

        if filename.endswith(".json"):
            status = await message.answer("📥 Скачиваю сессию…")
            file = await bot.get_file(file_obj.file_id)
            data = await bot.download_file(file.file_path or "")
            payload = data.read() if data is not None else b""
            saved = _save_session_file(config, filename, payload)
            await status.edit_text(f"✅ Сессия сохранена: <b>{saved.name}</b>")
            return

        video_exts = tuple(config.watcher.get("extensions", [".mp4"]))
        if filename.lower().endswith(video_exts):
            if size_mb > max_mb:
                await message.answer(
                    f"📏 Файл {size_mb:.0f} МБ больше лимита {max_mb} МБ — "
                    "скопируй его на сервер в <code>data/inbox</code> вручную."
                )
                return
            status = await message.answer(f"📥 Скачиваю <b>{filename}</b> ({size_mb:.1f} МБ)…")
            file = await bot.get_file(file_obj.file_id)
            data = await bot.download_file(file.file_path or "")
            payload = data.read() if data is not None else b""
            saved_path = _save_incoming_video(config, filename, payload)
            if saved_path is not None:
                await status.edit_text(
                    f"✅ Видео в inbox: <b>{saved_path.name}</b>\n"
                    "Дальше: ⚙️ Стадии → ▶️ Весь пайплайн"
                )
                return
        await message.answer("🤔 Пришли видеофайл (.mp4 …) или session .json")

    logger.info(
        "Starting Telegram bot (allowed users: %s)",
        sorted({int(u) for u in config.telegram.get("allowed_users", [])}),
    )
    dp.run_polling(bot, allowed_updates=["message", "callback_query"])
