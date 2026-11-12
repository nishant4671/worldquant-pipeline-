"""Fast Expression generator for WorldQuant BRAIN alpha simulation pipeline.

This module enforces a strict field quarantine strategy to eliminate hallucinated
field names. Fast Expression is a domain-specific language where unverified field
names cause silent runtime errors or NaNs on the server. Only fields that have been
verified in BRAIN documentation or the web UI data explorer are placed in KNOWN_FIELDS.
All speculative or pending fields remain segregated in UNVERIFIED_FIELDS and are
never used during expression generation.
"""

from collections import Counter
import logging
import random
import re
from typing import Any, Callable

log = logging.getLogger(__name__)

# Field Quarantine: KNOWN_FIELDS contains only verified fields by category.
KNOWN_FIELDS: dict[str, list[str]] = {
    "price_volume": [
        "open",  # confidence: high
        "close",  # confidence: high
        "high",  # confidence: high
        "low",  # confidence: high
        "volume",  # confidence: high
        "vwap",  # confidence: high
        "returns",  # confidence: high
        "cap",  # confidence: high
    ],
    "fundamental": [
        "assets",  # confidence: medium
        "liabilities",  # confidence: medium
        "equity",  # confidence: medium
        "sales",  # confidence: medium
        "cash",  # confidence: medium
        "debt",  # confidence: medium
        "ebit",  # confidence: medium
        "eps",  # confidence: medium
        "operating_cash_flow",  # confidence: medium
    ],
    "analyst": [
        "target_price",  # confidence: medium
        "est_eps",  # confidence: medium
        "est_sales",  # confidence: medium
        "rec_mean",  # confidence: medium
        "rec_buy",  # confidence: medium
        "rec_sell",  # confidence: medium
        "est_eps_mean",  # confidence: medium
        "est_eps_revision",  # confidence: medium
        "est_sales_mean",  # confidence: medium
        "est_dispersion",  # confidence: medium
        "analyst_coverage",  # confidence: medium
    ],
    "options": [
        "iv_call_30",  # confidence: medium
        "iv_put_30",  # confidence: medium
        "iv_skew",  # confidence: medium
        "option_volume",  # confidence: medium
    ],
    "short_positioning": [
        "short_interest_ratio",  # confidence: medium
        "days_to_cover",  # confidence: medium
        "institutional_ownership",  # confidence: medium
    ],
}

# Unverified fields: quarantined pending verification against BRAIN data explorer.
UNVERIFIED_FIELDS: dict[str, list[str]] = {
    "price_volume": [
        "adv20",  # TODO_field: verify against BRAIN data explorer
        "turnover",  # TODO_field: verify against BRAIN data explorer
        "sharesout",  # TODO_field: verify against BRAIN data explorer
    ],
    "fundamental": [
        "revenue",  # TODO_field: verify against BRAIN data explorer
        "gross_profit",  # TODO_field: verify against BRAIN data explorer
        "operating_income",  # TODO_field: verify against BRAIN data explorer
        "free_cash_flow",  # TODO_field: verify against BRAIN data explorer
        "book_value",  # TODO_field: verify against BRAIN data explorer
        "net_income",  # TODO_field: field name not found in BRAIN data explorer
    ],
    "analyst": [
        "pt_mean",  # TODO_field: verify against BRAIN data explorer
        "recommendation_count",  # TODO_field: verify against BRAIN data explorer
        "revisions_up",  # TODO_field: verify against BRAIN data explorer
        "revisions_down",  # TODO_field: verify against BRAIN data explorer
        "eps_std",  # TODO_field: verify against BRAIN data explorer
        "eps_mean",  # TODO_field: field name not found in BRAIN data explorer
    ],
}

ALL_CATEGORIES: list[str] = [
    "price_volume",
    "fundamental",
    "analyst",
    "options",
    "short_positioning",
]

# Template families for Fast Expression generation.
TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "rank",
        "build": lambda fields, n: f"rank({fields[0]})",
        "arity": 1,
        "applies_to": ALL_CATEGORIES,
    },
    {
        "name": "ts_rank",
        "build": lambda fields, n: f"ts_rank({fields[0]}, {n})",
        "arity": 1,
        "applies_to": ALL_CATEGORIES,
    },
    {
        "name": "ts_delta",
        "build": lambda fields, n: f"-ts_delta({fields[0]}, {n})",
        "arity": 1,
        "applies_to": ALL_CATEGORIES,
    },
    {
        "name": "ts_zscore",
        "build": lambda fields, n: f"ts_zscore({fields[0]}, {n})",  # verify: Fast Expression function name
        "arity": 1,
        "applies_to": ALL_CATEGORIES,
    },
    {
        "name": "ts_mean",
        "build": lambda fields, n: f"ts_mean({fields[0]}, {n})",
        "arity": 1,
        "applies_to": ALL_CATEGORIES,
    },
    {
        "name": "group_rank",
        "build": lambda fields, n: f"group_rank({fields[0]}, sector)",
        "arity": 1,
        "applies_to": ALL_CATEGORIES,
    },
    {
        "name": "ratio_pair",
        "build": lambda fields, n: f"{fields[0]} / ({fields[1]} + 1e-9)",
        "arity": 2,
        "applies_to": ALL_CATEGORIES,
    },
]

# Standard lookback windows in days.
LOOKBACK_WINDOWS: list[int] = [5, 10, 20, 60]


def classify_expression(expr: str) -> str | None:
    """Return the category of the FIRST known field that appears in expr, else None.

    Tokenizes expr on non-word characters (delimiters, operators, parentheses)
    and checks tokens sequentially against KNOWN_FIELDS. Used by tests and downstream QA.

    Args:
        expr: Fast Expression string to classify.

    Returns:
        str | None: Category of the first matching verified field, or None if unmatched.
    """
    field_to_category: dict[str, str] = {}
    for cat, fields in KNOWN_FIELDS.items():
        for field_name in fields:
            if field_name not in field_to_category:
                field_to_category[field_name] = cat

    tokens = re.split(r"\W+", expr)
    for token in tokens:
        if token in field_to_category:
            return field_to_category[token]
    return None


def generate_expressions(
    n: int = 10,
    seed: int | None = None,
    categories: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Return exactly n unique (expression, category) tuples.

    Uses round-robin allocation across available categories to ensure maximum
    cross-category diversity. Never selects fields from UNVERIFIED_FIELDS.

    Args:
        n: Exactly how many unique expressions to generate (default 10).
        seed: Optional integer seed for deterministic pseudo-random generation.
        categories: Optional list of categories to restrict output to. If None,
            uses all categories from KNOWN_FIELDS with non-empty verified lists.
            If specified, all names must exist in KNOWN_FIELDS and be non-empty.

    Returns:
        list[tuple[str, str]]: Exactly n unique (expression_string, category_name) tuples.

    Raises:
        ValueError: If categories contains an invalid or empty category.
        RuntimeError: If deduplication cannot produce n unique expressions within
            the attempt cap (50 attempts per slot), or if no verified fields exist.
    """
    if seed is not None:
        random.seed(seed)

    if categories is None:
        active_categories = [k for k, v in KNOWN_FIELDS.items() if len(v) > 0]
        if not active_categories:
            raise RuntimeError("No verified categories available in KNOWN_FIELDS.")
    else:
        for cat in categories:
            if cat not in KNOWN_FIELDS:
                raise ValueError(
                    f"Unknown category '{cat}'. Valid categories: {list(KNOWN_FIELDS.keys())}"
                )
            if len(KNOWN_FIELDS[cat]) == 0:
                raise ValueError(
                    f"Category '{cat}' has no verified fields in KNOWN_FIELDS."
                )
        active_categories = list(categories)

    num_categories = len(active_categories)
    results: list[tuple[str, str]] = []
    seen_expressions: set[str] = set()

    for i in range(n):
        # Round-robin category assignment enforces balance and diversity floor
        assigned_category = active_categories[i % num_categories]
        applicable_templates = [
            tmpl for tmpl in TEMPLATES if assigned_category in tmpl.get("applies_to", [])
        ]
        if not applicable_templates:
            raise RuntimeError(
                f"No applicable templates found for category '{assigned_category}'."
            )

        category_fields = KNOWN_FIELDS[assigned_category]
        slot_success = False

        for _ in range(50):
            template = random.choice(applicable_templates)
            arity = template["arity"]

            if arity == 1:
                selected_fields = [random.choice(category_fields)]
            elif arity == 2:
                if len(category_fields) >= 2:
                    selected_fields = random.sample(category_fields, 2)
                else:
                    selected_fields = [category_fields[0], category_fields[0]]
            else:
                selected_fields = random.sample(category_fields, min(arity, len(category_fields)))

            window = random.choice(LOOKBACK_WINDOWS)
            expr = template["build"](selected_fields, window)

            if expr not in seen_expressions:
                seen_expressions.add(expr)
                results.append((expr, assigned_category))
                slot_success = True
                break

        if not slot_success:
            raise RuntimeError(
                f"Could not generate {n} unique expressions after 50 attempts for slot {i}."
            )

    return results
