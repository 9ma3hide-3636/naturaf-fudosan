"""
SQLite データベース管理モジュール
物件の重複チェックと管理を担当
"""
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List

DB_PATH = Path(__file__).parent / "properties.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """データベースとテーブルを初期化"""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS properties (
                id          TEXT PRIMARY KEY,
                site        TEXT NOT NULL,
                name        TEXT,
                structure   TEXT,
                price       REAL,
                yield_pct   REAL,
                area        TEXT,
                station     TEXT,
                walk_min    INTEGER,
                url         TEXT,
                score       INTEGER,
                comment     TEXT,
                found_at    TEXT NOT NULL,
                notified    INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notified ON properties(notified)
        """)
        conn.commit()


def make_id(url: str) -> str:
    """URLからユニークIDを生成"""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def is_new(url: str) -> bool:
    """未登録の物件かどうかを返す"""
    pid = make_id(url)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM properties WHERE id = ?", (pid,)
        ).fetchone()
    return row is None


def insert_property(prop: Dict, notified: bool = False) -> str:
    """物件を登録してIDを返す"""
    pid = make_id(prop["url"])
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO properties
            (id, site, name, structure, price, yield_pct, area,
             station, walk_min, url, score, comment, found_at, notified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            pid,
            prop.get("site", ""),
            prop.get("name", ""),
            prop.get("structure", ""),
            prop.get("price"),
            prop.get("yield_pct"),
            prop.get("area", ""),
            prop.get("station", ""),
            prop.get("walk_min"),
            prop.get("url", ""),
            prop.get("score"),
            prop.get("comment", ""),
            now,
            1 if notified else 0,
        ))
        conn.commit()
    return pid


def mark_notified(url: str):
    """通知済みフラグを立てる"""
    pid = make_id(url)
    with get_connection() as conn:
        conn.execute(
            "UPDATE properties SET notified = 1 WHERE id = ?", (pid,)
        )
        conn.commit()


def get_unnotified() -> List[Dict]:
    """未通知物件の一覧を返す"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM properties WHERE notified = 0"
        ).fetchall()
    return [dict(r) for r in rows]
