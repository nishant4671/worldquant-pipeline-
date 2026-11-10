"""Fast Expression static validator (The Gatekeeper).

Performs static linting and hallucination detection on candidate alpha
expressions before submission to the WorldQuant BRAIN simulation API.
"""

import keyword
import logging
import re

from generator import KNOWN_FIELDS

log = logging.getLogger(__name__)

# Flatten verified fields into a single set
ALL_KNOWN_FIELDS: set[str] = {
    field for fields in KNOWN_FIELDS.values() for field in fields
}

# Allowed Fast Expression operators and group identifiers
ALLOWED_OPERATORS: set[str] = {
    "rank",
    "ts_rank",
    "ts_delta",
    "ts_mean",
    "ts_zscore",
    "ts_decay_linear",
    "group_neutralize",
    "group_rank",
    "trade_when",
    "sector",
    "industry",
    "subindustry",
}

# Alias for backwards compatibility
KNOWN_OPERATORS = ALLOWED_OPERATORS

# Allowed character set: a-zA-Z0-9_() ,+-*/?<>=!
ALLOWED_CHARS_SET: set[str] = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_() ,+-*/?<>=!"
)

# Python keywords that must not appear in expressions
PYTHON_KEYWORDS: set[str] = set(keyword.kwlist)


def _is_number(val: str) -> bool:
    """Check if a string represents a valid integer or float."""
    try:
        float(val)
        return True
    except ValueError:
        return False


def _check_group_rank_arity(expr: str) -> bool:
    """Ensure every group_rank call has at least two comma-separated arguments."""
    pos = 0
    while True:
        idx = expr.find("group_rank", pos)
        if idx == -1:
            break
        open_paren = expr.find("(", idx)
        if open_paren == -1:
            return False
        depth = 0
        has_comma = False
        for i in range(open_paren, len(expr)):
            char = expr[i]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            elif depth == 1 and char == ",":
                has_comma = True
        if not has_comma:
            return False
        pos = idx + len("group_rank")
    return True


def validate_expression(expr: str) -> tuple[bool, str]:
    """Statically lint and validate a single Fast Expression string.

    Args:
        expr: Alpha expression string to validate.

    Returns:
        tuple[bool, str]: (True, "") if valid, or (False, reason) if invalid.
    """
    # Check 1: Balanced parentheses
    if expr.count("(") != expr.count(")"):
        return False, "Unbalanced parentheses"

    # Check 2: No invalid characters
    for char in expr:
        if char not in ALLOWED_CHARS_SET:
            return False, "Invalid characters in expression"

    # Strip numeric literals (including scientific notation like 1e-9) before tokenizing identifiers
    sanitized = re.sub(r"(?:\b\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?\b", " ", expr)

    # Find all identifier tokens
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sanitized)

    # Check 3: No Python keywords
    for token in words:
        if token in PYTHON_KEYWORDS:
            return False, "Invalid keyword"

    # Check 4: Hard Gate (Hallucination check)
    for word in words:
        if _is_number(word):
            continue
        if word in ALL_KNOWN_FIELDS:
            continue
        if word in ALLOWED_OPERATORS:
            continue
        return False, f"Hallucinated field/operator: {word}"

    # Check 5: Basic arity for group_rank
    if "group_rank" in expr:
        if not _check_group_rank_arity(expr):
            return False, "group_rank requires at least 2 arguments (missing comma)"

    return True, ""


def validate_batch(
    expressions: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Validate a batch of Fast Expression strings.

    Args:
        expressions: List of expression strings.

    Returns:
        tuple[list[str], list[tuple[str, str]]]: (valid_expressions, rejected_expressions_with_reasons)
    """
    valid: list[str] = []
    rejected: list[tuple[str, str]] = []

    for expr in expressions:
        is_valid, reason = validate_expression(expr)
        if is_valid:
            valid.append(expr)
        else:
            rejected.append((expr, reason))

    return valid, rejected
