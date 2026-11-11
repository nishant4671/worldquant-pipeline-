"""Seed database with curated golden alphas."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import database

GOLDEN_ALPHAS: list[dict[str, Any]] = [
    {
        "alpha_id": "GOLDEN_01",
        "expression": "trade_when(ts_zscore(volume, 20) > 1.0, group_neutralize(ts_rank(ts_delta(sales, 60), 252), sector) * rank(-1 * ts_delta(close, 5)), -1)",
        "data_category": "golden_seed",
        "passed_filters": 1,
        "notified": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": '{"source": "hand_crafted"}',
    },
    {
        "alpha_id": "GOLDEN_02",
        "expression": "ts_decay_linear(rank(ebit / close) - ts_rank(ts_delta(close, 20), 60), 10)",
        "data_category": "golden_seed",
        "passed_filters": 1,
        "notified": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": '{"source": "hand_crafted"}',
    },
    {
        "alpha_id": "GOLDEN_03",
        "expression": "group_neutralize(ts_zscore(eps - ts_delay(eps, 60), 252) * rank(-1 * returns), industry)",
        "data_category": "golden_seed",
        "passed_filters": 1,
        "notified": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": '{"source": "hand_crafted"}',
    },
    {
        "alpha_id": "GOLDEN_04",
        "expression": "rank(-1 * ts_delta(close, 3) / ts_decay_linear(volume, 20)) * ts_rank(sales, 252)",
        "data_category": "golden_seed",
        "passed_filters": 1,
        "notified": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": '{"source": "hand_crafted"}',
    },
    {
        "alpha_id": "GOLDEN_05",
        "expression": "trade_when(ts_rank(volume, 10) < 0.2, group_neutralize(ts_rank(close - open, 20), subindustry), -1)",
        "data_category": "golden_seed",
        "passed_filters": 1,
        "notified": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": '{"source": "hand_crafted"}',
    },
]


def seed_database(db_path: Path | str | None = None) -> None:
    """Initialize database and insert golden alphas."""
    database.init_db(db_path)
    count = 0
    for alpha in GOLDEN_ALPHAS:
        record = dict(alpha)
        record["created_at"] = datetime.now(timezone.utc).isoformat()
        database.insert_alpha(record, db_path=db_path)
        count += 1
    print(f"Successfully seeded {count} golden alphas.")


if __name__ == "__main__":
    seed_database()
