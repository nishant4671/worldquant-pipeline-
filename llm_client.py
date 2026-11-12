"""Multi-Provider Resilient LLM Client using LiteLLM Router.

Replaces google-genai SDK with LiteLLM Router for automated failovers across
multiple free-tier LLM providers (Gemini, Groq, OpenRouter, Cloudflare),
enforcing Blind-Spot Diversity and resilience against single-provider outages.
"""

import json
import logging
import os
from typing import Any

from litellm import Router
from litellm.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from config import ROUTER

log = logging.getLogger(__name__)

DEFAULT_OPERATORS: list[str] = [
    "rank",
    "ts_rank",
    "ts_delta",
    "ts_mean",
    "ts_zscore",
    "ts_decay_linear",
    "group_neutralize",
    "trade_when",
]


class AllProvidersFailedException(Exception):
    """Raised when all LLM providers in the Router fail."""
    pass


# Backward compatibility alias
LLMOutageError = AllProvidersFailedException


class LLMClient:
    """Client for proposing and critiquing Fast Expression alphas via LiteLLM Router."""

    def __init__(
        self,
        router: Router | None = None,
        api_keys: list[str] | None = None,
    ) -> None:
        """Initialize LLMClient with configured Router or default global ROUTER."""
        self.router: Router = router or ROUTER
        self.api_keys: list[str] | None = api_keys

    def _parse_heavy_json(self, raw_text: str, expect_list: bool) -> list | dict:
        """Robustly extract and parse JSON lists or objects from LLM output.

        Finds the outermost matching brackets ('[' and ']' for lists, '{' and '}' for dicts)
        ignoring surrounding conversational or markdown text.
        """
        if not isinstance(raw_text, str):
            if expect_list and isinstance(raw_text, list):
                return raw_text
            if not expect_list and isinstance(raw_text, dict):
                return raw_text
            return [] if expect_list else {}

        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        start_char = "[" if expect_list else "{"
        end_char = "]" if expect_list else "}"
        start_idx = cleaned.find(start_char)
        end_idx = cleaned.rfind(end_char)

        if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
            log.warning("JSON delimiter not found in text: %s", raw_text[:200])
            return [] if expect_list else {}

        json_str = cleaned[start_idx : end_idx + 1]
        try:
            data = json.loads(json_str)
            if expect_list:
                if isinstance(data, list):
                    return data
                log.warning("Expected JSON list, got %s", type(data).__name__)
                return []
            else:
                if isinstance(data, dict):
                    return data
                log.warning("Expected JSON dict, got %s", type(data).__name__)
                return {}
        except Exception as exc:
            log.warning("Heavy JSON parser failed for extracted substring: %s (%s)", json_str[:200], exc)
            return [] if expect_list else {}

    def _call_router(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        expect_list: bool,
    ) -> list | dict:
        """Execute router completion and parse structured JSON response.

        Args:
            model: Model alias configured in LiteLLM router.
            system_prompt: System persona instruction.
            user_prompt: User instruction or candidate expression payload.
            expect_list: True if output should be a JSON array, False for JSON object.

        Returns:
            list | dict: Parsed JSON array or dictionary.

        Raises:
            AllProvidersFailedException: When all router deployments fail.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = self.router.completion(model=model, messages=messages)
        except (
            APIError,
            AuthenticationError,
            RateLimitError,
            ServiceUnavailableError,
            Timeout,
            Exception,
        ) as exc:
            log.error("LiteLLM Router call failed for model '%s': %s", model, exc)
            raise AllProvidersFailedException(
                f"All LLM providers failed for {model}: {exc}"
            ) from exc

        raw_text = ""
        if response and hasattr(response, "choices") and response.choices:
            raw_text = response.choices[0].message.content or ""
        elif isinstance(response, dict) and "choices" in response:
            raw_text = response["choices"][0]["message"]["content"] or ""

        return self._parse_heavy_json(raw_text, expect_list=expect_list)

    def generate_alphas(
        self,
        known_fields: dict[str, list[str]],
        operators: list[str] = DEFAULT_OPERATORS,
        num_alphas: int = 5,
        near_misses: list[dict] | None = None,
    ) -> list[dict]:
        """Propose Fast Expression alphas constrained to known fields and operators.

        Args:
            known_fields: Dictionary of verified fields by category.
            operators: List of allowed mathematical/time-series operators.
            num_alphas: Number of alpha expressions to request.
            near_misses: Optional list of near-miss alpha dictionaries from previous runs.

        Returns:
            list[dict]: Generated alpha dictionaries.
        """
        if operators is None:
            operators = DEFAULT_OPERATORS

        system_prompt = (
            "You are a Senior Quantitative Researcher. Generate novel, uncorrelated FASTEXPR alphas. "
            "Combine Price-Volume with Fundamental data. Use operators like ts_decay_linear, group_neutralize, "
            "trade_when, ts_zscore. Every alpha must have a clear economic hypothesis. Reject textbook formulas. "
            "Output a strict JSON array where each object contains these 7 keys in order: "
            "{'mechanism': 'Why does this anomaly exist?', "
            "'counter_party': 'Who is losing money on the other side and why?', "
            "'fields': 'Which specific data fields are used?', "
            "'direction': 'Long what, short what?', "
            "'turnover_control': 'How does the alpha avoid excessive turnover (e.g., decay, trade_when)?', "
            "'failure_mode': 'Under what market conditions will this alpha fail?', "
            "'expression': 'The FASTEXPR code'}. "
            "You MUST combine at least one Price-Volume field with one of the new Analyst, Options, or Short Interest fields to create cross-category alphas. Do not propose alphas using only price and volume. Use fields like est_eps_revision, iv_skew, short_interest_ratio, and operating_cash_flow alongside price-volume metrics. "
            "EVENT-SPARSE REQUIREMENT: At least 30% of the generated alphas MUST be 'event-sparse'. Instead of holding a position every day, you must use trade_when to activate only during specific events (e.g., earnings windows, abnormal volume spikes, or extreme volatility). Example: trade_when(ts_zscore(volume, 60) > 1.5, group_neutralize(ts_decay_linear(rank(ts_delta(est_eps_revision, 5)), 10), industry), -1). Holding a position only during events drastically reduces correlation with the crowd."
        )
        prompt = f"Propose {num_alphas} unique Fast Expression alphas following the system instructions."

        if near_misses:
            miss_lines = "\n".join(
                f"- {item.get('expression', str(item)) if isinstance(item, dict) else str(item)}"
                for item in near_misses
            )
            prompt += (
                f"\n\nYesterday, these alphas almost passed:\n{miss_lines}\n"
                "Propose 3 new alphas that mutate these ideas (e.g., change the window, add decay, "
                "or change neutralization) to push them over the threshold."
            )

        parsed = self._call_router(
            model="proposer-primary",
            system_prompt=system_prompt,
            user_prompt=prompt,
            expect_list=True,
        )

        results: list[dict] = []
        for item in parsed:
            if isinstance(item, dict):
                results.append(item)
            elif isinstance(item, str):
                results.append({"expression": item})
        return results

    def critique_alphas(self, expressions: list[Any]) -> list[Any]:
        """Critique and filter candidate alphas using strict PM persona.

        Args:
            expressions: Candidate Fast Expression strings or alpha dicts.

        Returns:
            list[Any]: Approved expressions. If rejected, returns empty list.
                If parsing fails, returns original expressions (fail-open).
        """
        if not expressions:
            return []

        system_prompt = (
            "You are a ruthless Portfolio Manager. Review the provided alphas. Each alpha contains a "
            "'mechanism', 'direction', and 'expression'. Independently derive what the sign of the "
            "expression SHOULD be based on the mechanism and direction. If the expression's math is "
            "inverted relative to the stated hypothesis (e.g., hypothesis says long momentum, but the "
            "expression has a negative sign), you MUST reject the alpha. Reject formulas with excessive "
            "turnover. Reject if the hypothesis does not match the math. Output a strict JSON object: "
            "{'decision': 'APPROVE/REJECT', 'critique': '...'}."
        )
        user_prompt = json.dumps(expressions)

        data = self._call_router(
            model="critic-primary",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expect_list=False,
        )

        if isinstance(data, dict):
            decision = str(data.get("decision", "")).strip().upper()
            if decision == "REJECT":
                critique = str(data.get("critique", "")).strip()
                log.warning("PM Critic rejected alphas: %s", critique)
                return []

        return expressions
