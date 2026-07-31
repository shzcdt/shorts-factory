"""SQLite connection handling and schema migrations."""

from pathlib import Path
import sqlite3

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and foreign keys enabled.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Configured sqlite3 connection.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    """Return the latest applied schema version.

    Args:
        conn: Open database connection.

    Returns:
        Highest version recorded in the schema_version table.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version;").fetchone()
    return row["v"] or 0


def init_db(db_path: Path | str) -> sqlite3.Connection:
    """Apply pending migrations and return an open connection.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Open connection with all migrations applied.
    """
    conn = connect(db_path)
    version = current_version(conn)
    applied = False
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        migration_id = int(path.stem.split("_")[0])
        if migration_id <= version:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_version (version) VALUES (?);", (migration_id,))
        applied = True
    if applied:
        conn.commit()
    return conn
