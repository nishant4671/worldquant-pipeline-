"""Tests for generator module."""

from collections import Counter
from pathlib import Path
import re
import pytest

import config
from generator import (
    KNOWN_FIELDS,
    TEMPLATES,
    UNVERIFIED_FIELDS,
    classify_expression,
    generate_expressions,
)


def test_returns_n_tuples():
    """Test generate_expressions returns exactly n valid (str, str) tuples."""
    out = generate_expressions(20, seed=1)
    assert len(out) == 20
    assert all(
        isinstance(t, tuple)
        and len(t) == 2
        and isinstance(t[0], str)
        and isinstance(t[1], str)
        for t in out
    )


def test_expressions_non_empty_and_reasonable_length():
    """Test all generated expressions have a length between 4 and 300 characters."""
    out = generate_expressions(30, seed=1)
    for expr, _ in out:
        assert 4 <= len(expr) <= 300


def test_categories_from_known_set():
    """Test all returned categories belong to non-empty KNOWN_FIELDS keys."""
    valid_categories = {k for k, v in KNOWN_FIELDS.items() if len(v) > 0}
    out = generate_expressions(30, seed=1)
    for _, cat in out:
        assert cat in valid_categories


def test_diversity_floor():
    """Test distinct categories generated meets the diversity floor."""
    non_empty_cats = len([k for k, v in KNOWN_FIELDS.items() if len(v) > 0])
    out = generate_expressions(30, seed=1)
    distinct_cats = {cat for _, cat in out}
    assert len(distinct_cats) >= min(3, non_empty_cats)


def test_deterministic_with_seed():
    """Test that specifying the same seed yields exact list equality."""
    run1 = generate_expressions(15, seed=42)
    run2 = generate_expressions(15, seed=42)
    assert run1 == run2


def test_different_seeds_diverge():
    """Test that different seeds produce different sets of expressions."""
    set1 = {expr for expr, _ in generate_expressions(15, seed=1)}
    set2 = {expr for expr, _ in generate_expressions(15, seed=2)}
    assert set1 != set2


def test_no_duplicate_expressions():
    """Test that generated expressions are unique without duplicates."""
    out = generate_expressions(50, seed=3)
    expressions = [expr for expr, _ in out]
    assert len(set(expressions)) == 50


def test_round_robin_balance():
    """Test category balance produced by round-robin assignment."""
    out = generate_expressions(30, seed=7)
    counts = Counter(cat for _, cat in out)
    assert max(counts.values()) - min(counts.values()) <= 2


def test_never_uses_unverified_fields():
    """Test that unverified fields are never included in generated expressions."""
    out = generate_expressions(100, seed=99)
    all_unverified = [f for fields in UNVERIFIED_FIELDS.values() for f in fields]
    for expr, _ in out:
        tokens = re.split(r"\W+", expr)
        for unverified in all_unverified:
            assert unverified not in tokens


def test_categories_filter():
    """Test restricting generation to a single category."""
    out = generate_expressions(10, seed=1, categories=["price_volume"])
    assert len(out) == 10
    assert all(cat == "price_volume" for _, cat in out)


def test_invalid_category_raises():
    """Test requesting a non-existent category raises ValueError."""
    with pytest.raises(ValueError):
        generate_expressions(10, categories=["does_not_exist"])


def test_expression_sanity():
    """Test structural sanity of generated expressions."""
    out = generate_expressions(50, seed=12)
    disallowed_keywords = {"def", "import", "None", "True", "False", "lambda", "class"}
    for expr, _ in out:
        assert expr.count("(") == expr.count(")")
        tokens = set(expr.split())
        assert not tokens.intersection(disallowed_keywords)
        assert expr == expr.strip()
        assert len(expr) > 0


def test_known_fields_well_formed():
    """Test that KNOWN_FIELDS names are valid identifiers without duplicates across categories."""
    identifier_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    all_fields: list[str] = []
    has_non_empty = False

    for cat, fields in KNOWN_FIELDS.items():
        if fields:
            has_non_empty = True
        for field_name in fields:
            assert identifier_pattern.match(field_name) is not None
            all_fields.append(field_name)

    assert has_non_empty is True
    assert len(all_fields) == len(set(all_fields))


def test_templates_callable():
    """Test template registry entries have valid schemas and callable builders."""
    for tmpl in TEMPLATES:
        assert callable(tmpl["build"])
        assert isinstance(tmpl["name"], str) and len(tmpl["name"]) > 0
        assert tmpl["arity"] in (1, 2)


def test_no_hardcoded_credentials_in_generator():
    """Test generator.py contains no hardcoded credentials."""
    generator_path = Path(__file__).resolve().parent.parent / "generator.py"
    content = generator_path.read_text(encoding="utf-8")
    assert config.BRAIN_EMAIL not in content
    assert config.BRAIN_PASSWORD not in content


def test_no_print_statements():
    """Test generator.py contains no print() statements."""
    generator_path = Path(__file__).resolve().parent.parent / "generator.py"
    content = generator_path.read_text(encoding="utf-8")
    assert "print(" not in content
