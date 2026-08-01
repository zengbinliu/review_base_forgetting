"""SQLite 数据库初始化与连接。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from app.config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    code_snippet TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    stage INTEGER NOT NULL DEFAULT 0,
    next_review_at TEXT NOT NULL,
    last_reviewed_at TEXT,
    questions_status TEXT NOT NULL DEFAULT 'pending',
    questions_error TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    original_name TEXT NOT NULL,
    relative_path TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    stem TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    qtype TEXT NOT NULL DEFAULT 'short',
    options_json TEXT NOT NULL DEFAULT '',
    generation_batch INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    question_id INTEGER,
    user_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    feedback TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL,
    question_stem TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS wrong_book (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL UNIQUE,
    stage INTEGER NOT NULL DEFAULT 0,
    next_review_at TEXT NOT NULL,
    last_reviewed_at TEXT,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notify_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_desktop_date TEXT,
    last_browser_hint TEXT,
    last_desktop_fp TEXT
);

CREATE TABLE IF NOT EXISTS practice_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    item_ids_json TEXT NOT NULL DEFAULT '[]',
    expand INTEGER NOT NULL DEFAULT 0,
    question_count INTEGER NOT NULL DEFAULT 8,
    status TEXT NOT NULL DEFAULT 'ready',
    error TEXT,
    questions_json TEXT NOT NULL DEFAULT '[]',
    answers_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO notify_state (id, last_desktop_date, last_browser_hint)
VALUES (1, NULL, NULL);
"""


def get_connection() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    ensure_dirs()
    with db_session() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(questions)").fetchall()
    }
    if "qtype" not in cols:
        conn.execute(
            "ALTER TABLE questions ADD COLUMN qtype TEXT NOT NULL DEFAULT 'short'"
        )
    if "options_json" not in cols:
        conn.execute(
            "ALTER TABLE questions ADD COLUMN options_json TEXT NOT NULL DEFAULT ''"
        )
    if "generation_batch" not in cols:
        conn.execute(
            "ALTER TABLE questions ADD COLUMN generation_batch INTEGER NOT NULL DEFAULT 1"
        )
    _migrate_review_logs(conn)
    _migrate_practice_sets(conn)
    _migrate_notify_state(conn)


def _migrate_notify_state(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(notify_state)").fetchall()
    }
    if not cols:
        return
    if "last_desktop_fp" not in cols:
        conn.execute(
            "ALTER TABLE notify_state ADD COLUMN last_desktop_fp TEXT"
        )


def _migrate_practice_sets(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(practice_sets)").fetchall()
    }
    if not cols:
        return
    if "answers_json" not in cols:
        conn.execute(
            "ALTER TABLE practice_sets ADD COLUMN answers_json TEXT NOT NULL DEFAULT '{}'"
        )


def _migrate_review_logs(conn: sqlite3.Connection) -> None:
    """题干快照 + 禁止删题时静默清空 question_id。"""
    log_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(review_logs)").fetchall()
    }
    if not log_cols:
        return
    if "question_stem" not in log_cols:
        conn.execute(
            "ALTER TABLE review_logs ADD COLUMN question_stem TEXT NOT NULL DEFAULT ''"
        )
        # 能关联上的历史记录，补全题干快照
        conn.execute(
            """
            UPDATE review_logs
            SET question_stem = (
                SELECT q.stem FROM questions q WHERE q.id = review_logs.question_id
            )
            WHERE question_id IS NOT NULL
              AND (question_stem IS NULL OR question_stem = '')
              AND EXISTS (SELECT 1 FROM questions q WHERE q.id = review_logs.question_id)
            """
        )

    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='review_logs'"
    ).fetchone()
    sql = (create_sql[0] if create_sql else "") or ""
    if "ON DELETE SET NULL" not in sql:
        return

    conn.executescript(
        """
        CREATE TABLE review_logs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            question_id INTEGER,
            user_answer TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            feedback TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT NOT NULL,
            question_stem TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE RESTRICT
        );
        INSERT INTO review_logs_new (
            id, item_id, question_id, user_answer, is_correct, feedback, reviewed_at, question_stem
        )
        SELECT
            id, item_id, question_id, user_answer, is_correct, feedback, reviewed_at,
            COALESCE(question_stem, '')
        FROM review_logs;
        DROP TABLE review_logs;
        ALTER TABLE review_logs_new RENAME TO review_logs;
        """
    )


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict]:
    if row is None:
        return None
    return dict(row)
