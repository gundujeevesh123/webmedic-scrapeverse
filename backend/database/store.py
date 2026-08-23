"""SQLite persistence layer for WebMedic (guide §13).

Tables:
    scrapers          — one row per target scraper (URL + schema + current version).
    scraper_versions  — every version of every scraper's strategy (JSON blob).
    runs              — every extraction attempt with its health report.
    repair_events     — every repair attempt: candidates, chosen candidate, gate result.

We use raw `sqlite3` on purpose — the whole system runs in one process and we
want the smallest reproducible surface (guide §20 warns against unnecessary
microservices; the same reasoning applies to unneeded ORMs).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from backend.config import settings
from backend.scraper.strategy import Strategy


_LOCK = threading.RLock()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scrapers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    target_url TEXT NOT NULL,
    schema TEXT NOT NULL,               -- JSON list of field names
    current_version INTEGER NOT NULL DEFAULT 1,
    health_score REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'unknown',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scraper_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraper_id INTEGER NOT NULL REFERENCES scrapers(id),
    version INTEGER NOT NULL,
    selectors TEXT NOT NULL,            -- JSON serialized Strategy
    created_at REAL NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    UNIQUE(scraper_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraper_id INTEGER NOT NULL REFERENCES scrapers(id),
    version INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    records_expected INTEGER,
    records_received INTEGER NOT NULL,
    health_score REAL NOT NULL,
    status TEXT NOT NULL,
    signals TEXT NOT NULL,              -- JSON list
    report TEXT NOT NULL                -- JSON full HealthReport
);

CREATE TABLE IF NOT EXISTS repair_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraper_id INTEGER NOT NULL REFERENCES scrapers(id),
    old_version INTEGER NOT NULL,
    new_version INTEGER,                -- NULL if plan was rejected
    failure_reason TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    selected_candidate TEXT,            -- JSON of winning ScoredCandidate
    confidence REAL NOT NULL,
    plan TEXT NOT NULL,                 -- JSON full RepairPlan
    action TEXT NOT NULL,               -- 'promote' | 'shadow' | 'rejected' | 'rollback'
    timestamp REAL NOT NULL
);
"""


def _db_path() -> Path:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", "", 1))
    return Path(url)


@contextmanager
def connect() -> Iterable[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA_SQL)


# --------------------------------------------------------------------------- #
# Scrapers
# --------------------------------------------------------------------------- #


def upsert_scraper(name: str, target_url: str, schema_fields: list[str]) -> int:
    now = time.time()
    with connect() as conn:
        row = conn.execute("SELECT id FROM scrapers WHERE name=?", (name,)).fetchone()
        if row:
            conn.execute(
                "UPDATE scrapers SET target_url=?, schema=?, updated_at=? WHERE id=?",
                (target_url, json.dumps(schema_fields), now, row["id"]),
            )
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO scrapers (name, target_url, schema, current_version, "
            "health_score, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, 0, 'unknown', ?, ?)",
            (name, target_url, json.dumps(schema_fields), now, now),
        )
        return int(cur.lastrowid)


def set_current_version(scraper_id: int, version: int) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE scrapers SET current_version=?, updated_at=? WHERE id=?",
            (version, now, scraper_id),
        )


def set_health(scraper_id: int, health_score: float, status: str) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE scrapers SET health_score=?, status=?, updated_at=? WHERE id=?",
            (health_score, status, now, scraper_id),
        )


def get_scraper(scraper_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM scrapers WHERE id=?", (scraper_id,)).fetchone()
        return dict(row) if row else None


def list_scrapers() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM scrapers ORDER BY id").fetchall()]


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #


def add_version(
    scraper_id: int, strategy: Strategy, reason: str, confidence: float = 1.0
) -> int:
    """Insert a new strategy version. Version number auto-increments per scraper."""
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS mx FROM scraper_versions WHERE scraper_id=?",
            (scraper_id,),
        ).fetchone()
        v = int(row["mx"]) + 1
        conn.execute(
            "INSERT INTO scraper_versions (scraper_id, version, selectors, created_at, reason, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scraper_id, v, json.dumps(strategy.to_dict()), now, reason, confidence),
        )
        return v


def get_version(scraper_id: int, version: int) -> Optional[Strategy]:
    with connect() as conn:
        row = conn.execute(
            "SELECT selectors FROM scraper_versions WHERE scraper_id=? AND version=?",
            (scraper_id, version),
        ).fetchone()
        if not row:
            return None
        return Strategy.from_dict(json.loads(row["selectors"]))


def list_versions(scraper_id: int) -> list[dict]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, version, reason, confidence, created_at "
                "FROM scraper_versions WHERE scraper_id=? ORDER BY version",
                (scraper_id,),
            ).fetchall()
        ]


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


def record_run(
    scraper_id: int,
    version: int,
    records_expected: Optional[int],
    records_received: int,
    health_score: float,
    status: str,
    signals: list[str],
    full_report: dict,
) -> int:
    now = time.time()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (scraper_id, version, timestamp, records_expected, records_received, "
            "health_score, status, signals, report) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scraper_id,
                version,
                now,
                records_expected,
                records_received,
                health_score,
                status,
                json.dumps(signals),
                json.dumps(full_report),
            ),
        )
        return int(cur.lastrowid)


def list_runs(scraper_id: int, limit: int = 50) -> list[dict]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, version, timestamp, records_expected, records_received, "
                "health_score, status, signals FROM runs WHERE scraper_id=? "
                "ORDER BY timestamp DESC LIMIT ?",
                (scraper_id, limit),
            ).fetchall()
        ]


# --------------------------------------------------------------------------- #
# Repair events
# --------------------------------------------------------------------------- #


def record_repair_event(
    scraper_id: int,
    old_version: int,
    new_version: Optional[int],
    failure_reason: str,
    candidate_count: int,
    selected_candidate: Optional[dict],
    confidence: float,
    plan: dict,
    action: str,
) -> int:
    now = time.time()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO repair_events (scraper_id, old_version, new_version, failure_reason, "
            "candidate_count, selected_candidate, confidence, plan, action, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scraper_id,
                old_version,
                new_version,
                failure_reason,
                candidate_count,
                json.dumps(selected_candidate) if selected_candidate else None,
                confidence,
                json.dumps(plan),
                action,
                now,
            ),
        )
        return int(cur.lastrowid)


def list_repair_events(scraper_id: int, limit: int = 50) -> list[dict]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, old_version, new_version, failure_reason, candidate_count, "
                "confidence, action, timestamp FROM repair_events WHERE scraper_id=? "
                "ORDER BY timestamp DESC LIMIT ?",
                (scraper_id, limit),
            ).fetchall()
        ]


def rollback(scraper_id: int, to_version: int, reason: str) -> None:
    """Roll back to a prior version — one write."""
    with connect() as conn:
        row = conn.execute(
            "SELECT current_version FROM scrapers WHERE id=?", (scraper_id,)
        ).fetchone()
        current = int(row["current_version"]) if row else None
    set_current_version(scraper_id, to_version)
    record_repair_event(
        scraper_id=scraper_id,
        old_version=current if current is not None else to_version,
        new_version=to_version,
        failure_reason=reason,
        candidate_count=0,
        selected_candidate=None,
        confidence=1.0,
        plan={"kind": "rollback", "from": current, "to": to_version, "reason": reason},
        action="rollback",
    )


def reset_database() -> None:
    """Nuke and recreate — used by tests."""
    path = _db_path()
    if path.exists():
        path.unlink()
    init_db()
