"""Database operations module for WorldQuant BRAIN pipeline.

Provides SQLite persistence for simulated alphas, quality filters,
Telegram notification status, web UI submission tracking, and
cryptographic deduplication of alpha expressions.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from config import settings

log = logging.getLogger(__name__)

_initialized_dbs: set[str] = set()
_init_lock = threading.Lock()


def structural_fingerprint(expr: str) -> str:
    """Compute structural fingerprint of an expression, replacing numbers with {N}.

    Args:
        expr: Alpha expression string.

    Returns:
        str: Transformed structural template.
    """
    normalized = re.sub(r"\s+", "", expr.lower())
    return re.sub(r"\d+", "{N}", normalized)


def hash_expression(expr: str) -> str:
    """Compute deterministic SHA-256 hash of normalized expression string.

    Args:
        expr: Raw Fast Expression string.

    Returns:
        str: Hexadecimal SHA-256 digest of normalized expression.
    """
    normalized = expr.lower().replace(" ", "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _resolve_db_path(db_path: Path | str | None = None) -> Path | str:
    """Resolve database path, defaulting to settings.db_path."""
    if db_path is None:
        return settings.db_path
    return db_path


def _get_db_key(db_path: Path | str | None = None) -> str:
    """Generate a canonical key for tracking initialized database files."""
    target = _resolve_db_path(db_path)
    try:
        return str(Path(target).resolve())
    except Exception:
        return str(target)


def _ensure_db(db_path: Path | str | None = None) -> Path | str:
    """Resolve target path and ensure database schema is initialized once."""
    target = _resolve_db_path(db_path)
    key = _get_db_key(target)
    if key not in _initialized_dbs:
        init_db(target)
    return target


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite database connection with Row factory enabled.

    Args:
        db_path: Optional path to the database file. If None, uses settings.db_path.

    Returns:
        sqlite3.Connection: Database connection configured with sqlite3.Row factory.
    """
    target = _resolve_db_path(db_path)
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str | None = None, force: bool = False) -> None:
    """Initialize database schema, creating the alphas table and indexes if missing.

    Handles schema migrations for existing tables lacking the expression_hash column.

    Args:
        db_path: Optional path to the database file.
        force: If True, re-executes DDL even if already initialized.
    """
    target = _resolve_db_path(db_path)
    key = _get_db_key(target)

    with _init_lock:
        if not force and key in _initialized_dbs:
            return

        log.info("Initializing database schema at %s", target)
        with connect(target) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alphas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alpha_id TEXT UNIQUE NOT NULL,
                    expression TEXT NOT NULL,
                    expression_hash TEXT UNIQUE,
                    structural_hash TEXT,
                    data_category TEXT,
                    sharpe REAL,
                    fitness REAL,
                    turnover REAL,
                    passed_filters INTEGER NOT NULL DEFAULT 0,
                    notified INTEGER NOT NULL DEFAULT 0,
                    submitted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    raw_response TEXT
                );
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_alphas_alpha_id ON alphas(alpha_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alphas_passing ON alphas(passed_filters, notified, sharpe DESC);"
            )

            # Check if migration is needed for existing tables without expression_hash or structural_hash
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(alphas)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "expression_hash" not in columns:
                conn.execute("ALTER TABLE alphas ADD COLUMN expression_hash TEXT")
                cursor.execute("SELECT id, expression FROM alphas WHERE expression_hash IS NULL")
                rows = cursor.fetchall()
                for row in rows:
                    h = hash_expression(row["expression"])
                    conn.execute("UPDATE alphas SET expression_hash = ? WHERE id = ?", (h, row["id"]))
            else:
                # Backfill any existing rows with null expression_hash
                cursor.execute("SELECT id, expression FROM alphas WHERE expression_hash IS NULL")
                rows = cursor.fetchall()
                for row in rows:
                    h = hash_expression(row["expression"])
                    conn.execute("UPDATE alphas SET expression_hash = ? WHERE id = ?", (h, row["id"]))

            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_alphas_expression_hash ON alphas(expression_hash);"
            )

            if "structural_hash" not in columns:
                conn.execute("ALTER TABLE alphas ADD COLUMN structural_hash TEXT")
                cursor.execute("SELECT id, expression FROM alphas WHERE structural_hash IS NULL")
                rows = cursor.fetchall()
                for row in rows:
                    fp = structural_fingerprint(row["expression"])
                    conn.execute("UPDATE alphas SET structural_hash = ? WHERE id = ?", (fp, row["id"]))
            else:
                # Backfill any existing rows with null structural_hash
                cursor.execute("SELECT id, expression FROM alphas WHERE structural_hash IS NULL")
                rows = cursor.fetchall()
                for row in rows:
                    fp = structural_fingerprint(row["expression"])
                    conn.execute("UPDATE alphas SET structural_hash = ? WHERE id = ?", (fp, row["id"]))

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alphas_structural_hash ON alphas(structural_hash);"
            )

            conn.commit()

        _initialized_dbs.add(key)


def insert_alpha(record: dict[str, Any], db_path: Path | str | None = None) -> None:
    """Insert or replace an alpha record based on alpha_id.

    Automatically computes and persists normalized expression_hash and structural_hash.

    Args:
        record: Dictionary containing alpha metadata and metrics.
        db_path: Optional path to the database file.
    """
    target = _ensure_db(db_path)

    alpha_id = str(record["alpha_id"])
    expression = str(record.get("expression", ""))
    expr_hash = record.get("expression_hash") or hash_expression(expression)
    struct_hash = record.get("structural_hash") or structural_fingerprint(expression)
    data_category = record.get("data_category") or record.get("category")
    data_category = str(data_category) if data_category is not None else None

    sharpe = record.get("sharpe")
    sharpe_val = float(sharpe) if isinstance(sharpe, (int, float)) else None

    fitness = record.get("fitness")
    fitness_val = float(fitness) if isinstance(fitness, (int, float)) else None

    turnover = record.get("turnover")
    turnover_val = float(turnover) if isinstance(turnover, (int, float)) else None

    passed_filters = int(record.get("passed_filters", 0))
    notified = int(record.get("notified", 0))
    submitted = int(record.get("submitted", 0))

    created_at = record.get("created_at")
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()

    raw_response = record.get("raw_response")
    if raw_response is None:
        raw_response = record.get("raw_metrics") or record.get("metrics")
    if isinstance(raw_response, (dict, list)):
        raw_response_str = json.dumps(raw_response)
    elif raw_response is not None:
        raw_response_str = str(raw_response)
    else:
        raw_response_str = "{}"

    with connect(target) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO alphas (
                alpha_id, expression, expression_hash, structural_hash, data_category, sharpe, fitness, turnover,
                passed_filters, notified, submitted, created_at, raw_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alpha_id,
                expression,
                expr_hash,
                struct_hash,
                data_category,
                sharpe_val,
                fitness_val,
                turnover_val,
                passed_filters,
                notified,
                submitted,
                str(created_at),
                raw_response_str,
            ),
        )
        conn.commit()


def expression_exists(expr: str, db_path: Path | str | None = None) -> bool:
    """Check if a structurally equivalent expression has already been simulated and recorded in the database.

    Args:
        expr: Alpha expression string.
        db_path: Optional path to the database file.

    Returns:
        bool: True if structural fingerprint exists in database, False otherwise.
    """
    target = _ensure_db(db_path)
    struct_hash = structural_fingerprint(expr)
    with connect(target) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM alphas WHERE structural_hash = ? LIMIT 1", (struct_hash,))
        return cursor.fetchone() is not None


def get_alpha(alpha_id: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Retrieve an alpha record by alpha_id.

    Args:
        alpha_id: Unique alpha ID string.
        db_path: Optional path to the database file.

    Returns:
        dict[str, Any] | None: Record as a dictionary, or None if not found.
    """
    target = _ensure_db(db_path)
    with connect(target) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alphas WHERE alpha_id = ?", (alpha_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)


def list_passing(limit: int = 4, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return top unnotified passing alphas sorted by Sharpe ratio descending.

    Args:
        limit: Maximum number of records to return (default 4).
        db_path: Optional path to the database file.

    Returns:
        list[dict[str, Any]]: List of dictionary alpha records.
    """
    target = _ensure_db(db_path)
    with connect(target) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM alphas
            WHERE passed_filters = 1 AND notified = 0
            ORDER BY sharpe DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_near_misses(limit: int = 3, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return alphas that failed quality gates but were near misses on Sharpe or Fitness.

    Args:
        limit: Maximum number of records to return (default 3).
        db_path: Optional path to the database file.

    Returns:
        list[dict[str, Any]]: List of dictionary alpha records.
    """
    target = _ensure_db(db_path)
    with connect(target) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM alphas
            WHERE passed_filters = 0
              AND (
                (sharpe >= 1.0 AND sharpe <= 1.24)
                OR
                (fitness >= 0.8 AND fitness <= 0.99)
              )
            ORDER BY sharpe DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def mark_notified(alpha_id: str, db_path: Path | str | None = None) -> None:
    """Mark an alpha as notified.

    Args:
        alpha_id: Unique alpha ID string.
        db_path: Optional path to the database file.
    """
    target = _ensure_db(db_path)
    with connect(target) as conn:
        conn.execute("UPDATE alphas SET notified = 1 WHERE alpha_id = ?", (alpha_id,))
        conn.commit()


def mark_submitted(alpha_id: str, db_path: Path | str | None = None) -> None:
    """Mark an alpha as submitted to WorldQuant.

    Args:
        alpha_id: Unique alpha ID string.
        db_path: Optional path to the database file.
    """
    target = _ensure_db(db_path)
    with connect(target) as conn:
        conn.execute("UPDATE alphas SET submitted = 1 WHERE alpha_id = ?", (alpha_id,))
        conn.commit()


def summary(db_path: Path | str | None = None) -> dict[str, int]:
    """Return summary counts of total, passed, notified, and submitted alphas.

    Args:
        db_path: Optional path to the database file.

    Returns:
        dict[str, int]: Counts dictionary with keys total, passed, notified, submitted.
    """
    target = _ensure_db(db_path)
    with connect(target) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN passed_filters = 1 THEN 1 ELSE 0 END), 0) AS passed,
                COALESCE(SUM(CASE WHEN notified = 1 THEN 1 ELSE 0 END), 0) AS notified,
                COALESCE(SUM(CASE WHEN submitted = 1 THEN 1 ELSE 0 END), 0) AS submitted
            FROM alphas
            """
        )
        row = cursor.fetchone()
        if row is None:
            return {"total": 0, "passed": 0, "notified": 0, "submitted": 0}
        return {
            "total": int(row["total"]),
            "passed": int(row["passed"]),
            "notified": int(row["notified"]),
            "submitted": int(row["submitted"]),
        }
