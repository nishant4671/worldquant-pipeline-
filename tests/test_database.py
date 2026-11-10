"""Unit tests for database module."""

import sqlite3
import pytest

import database


def test_init_db(tmp_path):
    """Ensure the alphas table and its indexes are created properly."""
    db_file = tmp_path / "test_init.db"
    database.init_db(db_file)

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alphas'")
    table = cursor.fetchone()
    conn.close()

    assert table is not None
    assert table[0] == "alphas"


def test_insert_and_get(tmp_path):
    """Insert a row, retrieve it by alpha_id, and assert all fields match."""
    db_file = tmp_path / "test_insert.db"
    record = {
        "alpha_id": "alpha_test_123",
        "expression": "rank(close)",
        "data_category": "price_volume",
        "sharpe": 1.75,
        "fitness": 1.30,
        "turnover": 0.25,
        "passed_filters": 1,
        "notified": 0,
        "submitted": 0,
        "created_at": "2026-11-10T14:00:00+00:00",
        "raw_response": {"sharpe": 1.75, "fitness": 1.30},
    }

    database.insert_alpha(record, db_path=db_file)
    retrieved = database.get_alpha("alpha_test_123", db_path=db_file)

    assert retrieved is not None
    assert retrieved["alpha_id"] == "alpha_test_123"
    assert retrieved["expression"] == "rank(close)"
    assert retrieved["data_category"] == "price_volume"
    assert retrieved["sharpe"] == 1.75
    assert retrieved["fitness"] == 1.30
    assert retrieved["turnover"] == 0.25
    assert retrieved["passed_filters"] == 1
    assert retrieved["notified"] == 0
    assert retrieved["submitted"] == 0
    assert retrieved["created_at"] == "2026-11-10T14:00:00+00:00"
    assert "1.75" in retrieved["raw_response"]


def test_list_passing(tmp_path):
    """Insert 3 rows (2 passing, 1 failing); assert list_passing returns passing in Sharpe desc order."""
    db_file = tmp_path / "test_list_passing.db"

    # Row 1: passing, sharpe 1.4
    database.insert_alpha(
        {
            "alpha_id": "alpha_p1",
            "expression": "ts_rank(open, 10)",
            "data_category": "price_volume",
            "sharpe": 1.4,
            "fitness": 1.1,
            "turnover": 0.2,
            "passed_filters": 1,
        },
        db_path=db_file,
    )

    # Row 2: passing, sharpe 1.8
    database.insert_alpha(
        {
            "alpha_id": "alpha_p2",
            "expression": "group_rank(close, sector)",
            "data_category": "price_volume",
            "sharpe": 1.8,
            "fitness": 1.5,
            "turnover": 0.2,
            "passed_filters": 1,
        },
        db_path=db_file,
    )

    # Row 3: failing, sharpe 0.5
    database.insert_alpha(
        {
            "alpha_id": "alpha_f1",
            "expression": "rank(volume)",
            "data_category": "price_volume",
            "sharpe": 0.5,
            "fitness": 0.3,
            "turnover": 0.1,
            "passed_filters": 0,
        },
        db_path=db_file,
    )

    passing = database.list_passing(limit=10, db_path=db_file)
    assert len(passing) == 2
    assert passing[0]["alpha_id"] == "alpha_p2"
    assert passing[0]["sharpe"] == 1.8
    assert passing[1]["alpha_id"] == "alpha_p1"
    assert passing[1]["sharpe"] == 1.4


def test_mark_notified(tmp_path):
    """Insert a passing row; assert it appears in list_passing then disappears once marked notified."""
    db_file = tmp_path / "test_notified.db"
    database.insert_alpha(
        {
            "alpha_id": "alpha_notif_1",
            "expression": "rank(returns)",
            "data_category": "price_volume",
            "sharpe": 1.9,
            "fitness": 1.6,
            "turnover": 0.3,
            "passed_filters": 1,
            "notified": 0,
        },
        db_path=db_file,
    )

    # Must appear initially
    passing_before = database.list_passing(limit=5, db_path=db_file)
    assert len(passing_before) == 1
    assert passing_before[0]["alpha_id"] == "alpha_notif_1"

    # Mark notified
    database.mark_notified("alpha_notif_1", db_path=db_file)

    # Must no longer appear in unnotified passing list
    passing_after = database.list_passing(limit=5, db_path=db_file)
    assert len(passing_after) == 0

    # Record itself should show notified=1
    row = database.get_alpha("alpha_notif_1", db_path=db_file)
    assert row is not None
    assert row["notified"] == 1


def test_mark_submitted(tmp_path):
    """Mark an alpha as submitted and verify submitted column is updated to 1."""
    db_file = tmp_path / "test_submitted.db"
    database.insert_alpha(
        {
            "alpha_id": "alpha_sub_1",
            "expression": "ts_mean(close, 20)",
            "data_category": "price_volume",
            "sharpe": 1.6,
            "fitness": 1.2,
            "turnover": 0.15,
            "passed_filters": 1,
            "submitted": 0,
        },
        db_path=db_file,
    )

    row_before = database.get_alpha("alpha_sub_1", db_path=db_file)
    assert row_before is not None
    assert row_before["submitted"] == 0

    database.mark_submitted("alpha_sub_1", db_path=db_file)

    row_after = database.get_alpha("alpha_sub_1", db_path=db_file)
    assert row_after is not None
    assert row_after["submitted"] == 1


def test_summary(tmp_path):
    """Insert various alpha records and assert summary counts are accurate."""
    db_file = tmp_path / "test_summary.db"

    # 1: passed=1, notified=1, submitted=1
    database.insert_alpha(
        {"alpha_id": "a1", "expression": "e1", "passed_filters": 1, "notified": 1, "submitted": 1},
        db_path=db_file,
    )
    # 2: passed=1, notified=0, submitted=0
    database.insert_alpha(
        {"alpha_id": "a2", "expression": "e2", "passed_filters": 1, "notified": 0, "submitted": 0},
        db_path=db_file,
    )
    # 3: passed=0, notified=0, submitted=0
    database.insert_alpha(
        {"alpha_id": "a3", "expression": "e3", "passed_filters": 0, "notified": 0, "submitted": 0},
        db_path=db_file,
    )

    stats = database.summary(db_path=db_file)
    assert stats["total"] == 3
    assert stats["passed"] == 2
    assert stats["notified"] == 1
    assert stats["submitted"] == 1
