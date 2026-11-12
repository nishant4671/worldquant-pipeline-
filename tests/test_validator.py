"""Unit tests for validator module (The Gatekeeper)."""

import pytest

from validator import validate_batch, validate_expression


def test_valid_expression():
    """Test that a well-formed Fast Expression passes validation."""
    valid, reason = validate_expression("rank(close)")
    assert valid is True
    assert reason == ""


def test_unbalanced_parens():
    """Test that unmatched parentheses fail validation."""
    valid, reason = validate_expression("ts_rank(close, 5")
    assert valid is False
    assert reason == "Unbalanced parentheses"


def test_hallucinated_field():
    """Test that unverified/hallucinated fields are caught and rejected."""
    valid, reason = validate_expression("rank(magic_profit)")
    assert valid is False
    assert reason == "Hallucinated field/operator: magic_profit"


def test_python_keywords():
    """Test that expressions containing Python keywords are rejected."""
    valid, reason = validate_expression("import os")
    assert valid is False
    assert reason == "Invalid keyword"


def test_valid_batch():
    """Test batch validation with all valid expressions."""
    expressions = [
        {"expression": "rank(close)"},
        {"expression": "-ts_delta(volume, 5)"},
        {"expression": "group_rank(returns, sector)"},
    ]
    valid, rejected = validate_batch(expressions)
    assert valid == expressions
    assert rejected == []


def test_mixed_batch():
    """Test batch validation separating valid and hallucinated expressions."""
    expressions = [
        {"expression": "rank(close)"},
        {"expression": "rank(hallucinated_alpha_signal)"},
    ]
    valid, rejected = validate_batch(expressions)
    assert valid == [{"expression": "rank(close)"}]
    assert len(rejected) == 1
    rejected_expr, reason = rejected[0]
    assert rejected_expr == {"expression": "rank(hallucinated_alpha_signal)"}
    assert reason == "Hallucinated field/operator: hallucinated_alpha_signal"


def test_invalid_characters():
    """Test rejection of disallowed characters (e.g., semicolons, brackets)."""
    valid, reason = validate_expression("rank(close); drop table")
    assert valid is False
    assert reason == "Invalid characters in expression"


def test_group_rank_arity():
    """Test that group_rank requires at least 2 arguments separated by a comma."""
    valid, reason = validate_expression("group_rank(close)")
    assert valid is False
    assert "group_rank requires at least 2 arguments" in reason


def test_hallucinated_field_rejected():
    """Assert validate_expression('rank(magic_income)') returns hallucinated field error."""
    valid, reason = validate_expression("rank(magic_income)")
    assert valid is False
    assert reason == "Hallucinated field/operator: magic_income"


def test_hallucinated_operator_rejected():
    """Assert validate_expression('magic_operator(close)') returns hallucinated operator error."""
    valid, reason = validate_expression("magic_operator(close)")
    assert valid is False
    assert reason == "Hallucinated field/operator: magic_operator"


def test_complex_valid_expression():
    """Assert complex valid expression with group_neutralize passes validation."""
    valid, reason = validate_expression("group_neutralize(ts_decay_linear(close, 20), sector)")
    assert valid is True
    assert reason == ""


def test_math_constants_allowed():
    """Assert scientific notation and math constants are allowed in expressions."""
    valid, reason = validate_expression("sales / (debt + 1e-9)")
    assert valid is True
    assert reason == ""


def test_new_fields_valid():
    """Assert newly expanded whitelist fields pass static validation."""
    assert validate_expression("rank(est_dispersion)") == (True, "")
    assert validate_expression("rank(iv_skew)") == (True, "")
    assert validate_expression("rank(short_interest_ratio)") == (True, "")

