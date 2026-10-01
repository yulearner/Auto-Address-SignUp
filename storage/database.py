"""SQLite 结果存储。

用于持久化每次签到结果，支持历史查询与统计。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from models.result import CheckinResult

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkin_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    site            TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    message         TEXT    DEFAULT '',
    reward          TEXT,
    balance         TEXT,
    duration        REAL    DEFAULT 0.0,
    timestamp       TEXT    NOT NULL,
    error           TEXT,
    screenshot      TEXT,
    used_saved_session INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkin_site ON checkin_results(site);
CREATE INDEX IF NOT EXISTS idx_checkin_created ON checkin_results(created_at);
"""


class Database:
    """签到结果数据库。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save_result(self, result: CheckinResult) -> None:
        """保存单条签到结果。"""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                """
                INSERT INTO checkin_results (
                    site, status, message, reward, balance, duration,
                    timestamp, error, screenshot, used_saved_session, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                """,
                (
                    result.site,
                    result.status.value,
                    result.message,
                    result.reward,
                    result.balance,
                    result.duration,
                    result.timestamp,
                    result.error,
                    result.screenshot,
                    int(result.used_saved_session),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            logger.warning("保存签到结果失败: %s", exc)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None