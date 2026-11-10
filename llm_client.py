"""Google GenAI LLM Client for WorldQuant BRAIN Fast Expression alpha generation.

Migrated to google-genai SDK. Manages API interactions, key rotation upon
HTTP 429 rate limits, JSON parsing for proposed alpha expressions, and
portfolio manager critique filtering.
"""

import json
import logging
import time
from typing import Any

from google import genai
from google.genai import errors, types

import config

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


def _strip_markdown_code_block(text: str) -> str:
    """Robustly strip markdown code fences and backticks from response text."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Drop opening fence (e.g. ``` or ```json)
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Drop closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    elif cleaned.startswith("`") and cleaned.endswith("`"):
        cleaned = cleaned.strip("`").strip()
    return cleaned


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if exception represents an HTTP 429 rate limit or quota exhaustion."""
    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        status_code = getattr(exc, "status_code", None)
        if code == 429 or status_code == 429:
            return True

    err_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if err_code == 429:
        return True

    err_str = str(exc)
    return "429" in err_str or "RESOURCE_EXHAUSTED" in err_str


class LLMClient:
    """Client for generating and critiquing Fast Expression alphas via Google Gemini."""

    def __init__(self, api_keys: list[str] | None = None) -> None:
        """Initialize LLM client with configured Gemini API keys.

        Args:
            api_keys: Optional list of Gemini API keys. If None, loaded from config.
        """
        self.api_keys: list[str] = (
            api_keys if api_keys is not None else list(config.GEMINI_API_KEYS)
        )
        if not self.api_keys:
            raise RuntimeError("Missing Gemini API keys in configuration.")

        self.current_key_index: int = 0
        self._init_client()

    def _init_client(self) -> None:
        """Instantiate genai.Client with the current API key."""
        api_key = self.api_keys[self.current_key_index]
        try:
            self.client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(api_version="v1beta"),
            )
        except Exception:
            self.client = genai.Client(api_key=api_key)

    def _rotate_key(self) -> None:
        """Rotate to next API key, re-instantiate client, and log warning."""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        self._init_client()
        log.warning(f"Rate limit hit. Rotating to API key index {self.current_key_index}")

    def _call_model(self, prompt: str, system_prompt: str) -> str | None:
        """Execute model content generation with single-shot 429 rate limit retry.

        Args:
            prompt: User prompt content.
            system_prompt: System instruction directing model persona and format.

        Returns:
            str | None: Generated text response, or None if failed.
        """
        def _exec_generate() -> str | None:
            gen_config = types.GenerateContentConfig(
                response_mime_type="application/json",
                system_instruction=system_prompt,
            )
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=gen_config,
            )
            return response.text

        try:
            return _exec_generate()
        except Exception as e:
            if _is_rate_limit_error(e):
                log.warning("Rate limit hit on Gemini API: %s", e)
                if len(self.api_keys) > 1:
                    self._rotate_key()
                else:
                    time.sleep(60)

                # Retry ONCE
                try:
                    return _exec_generate()
                except Exception as retry_err:
                    log.error("Gemini API call failed after rate limit retry: %s", retry_err)
                    return None
            else:
                log.error("Gemini API call failed: %s", e)
                return None

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call LLM with system prompt and user prompt, returning raw text.

        Args:
            system_prompt: System instruction directing model persona and format.
            user_prompt: User prompt content.

        Returns:
            str: Generated text response, or empty string on failure.
        """
        result = self._call_model(prompt=user_prompt, system_prompt=system_prompt)
        return result if result is not None else ""

    def _parse_alphas(self, raw_text: str) -> list[str]:
        """Parse raw response text into a list of alpha expression strings.

        Args:
            raw_text: Raw response string from model.

        Returns:
            list[str]: Parsed alpha expression strings.

        Raises:
            ValueError: If parsing fails or output is not a JSON array.
        """
        cleaned = _strip_markdown_code_block(raw_text)
        data = json.loads(cleaned)
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array, got {type(data).__name__}")
        return [str(item) for item in data]

    def generate_alphas(
        self,
        known_fields: dict[str, list[str]],
        operators: list[str] = DEFAULT_OPERATORS,
        num_alphas: int = 5,
    ) -> list[str]:
        """Propose Fast Expression alphas constrained to known fields and operators.

        Args:
            known_fields: Dictionary of verified fields by category.
            operators: List of allowed mathematical/time-series operators. Defaults
                to DEFAULT_OPERATORS including advanced operators.
            num_alphas: Number of alpha expressions to request (default 5).

        Returns:
            list[str]: Generated alpha expression strings.
        """
        if operators is None:
            operators = DEFAULT_OPERATORS

        system_prompt = (
            f"You are an elite quantitative researcher. Propose exactly {num_alphas} unique alpha expressions. "
            "Use ADVANCED operators whenever possible to avoid correlation and reduce turnover. "
            "Simple rank(close) alphas are rejected. You MUST use operators like ts_decay_linear, group_neutralize, trade_when, and ts_zscore. "
            "Mix data categories: Price-Volume, Fundamental, Analyst. "
            f"You MUST ONLY use fields from this list: {known_fields}. "
            f"You MUST ONLY use these operators: {operators}. "
            'Return ONLY a JSON array of strings, e.g. ["ts_decay_linear(rank(close), 5)", "group_neutralize(ts_zscore(volume, 20), sector)"]. '
            "Do NOT wrap in markdown block or add explanations."
        )
        prompt = f"Propose {num_alphas} unique Fast Expression alphas following the system instructions."

        raw_text = self._call_llm(system_prompt=system_prompt, user_prompt=prompt)
        if isinstance(raw_text, list):
            return [str(item) for item in raw_text]
        if not raw_text:
            return []

        try:
            return self._parse_alphas(raw_text)
        except Exception as parse_err:
            log.warning("LLM output invalid JSON on first attempt (%s). Retrying once...", parse_err)
            # Retry ONCE
            retry_text = self._call_llm(system_prompt=system_prompt, user_prompt=prompt)
            if isinstance(retry_text, list):
                return [str(item) for item in retry_text]
            if not retry_text:
                return []
            try:
                return self._parse_alphas(retry_text)
            except Exception as retry_parse_err:
                log.error("LLM output invalid JSON on second attempt: %s", retry_parse_err)
                return []

    def critique_alphas(self, expressions: list[str]) -> list[str]:
        """Critique and filter candidate alphas using a strict PM persona.

        Args:
            expressions: Candidate Fast Expression strings.

        Returns:
            list[str]: Approved expressions. If parsing fails, returns original
                input expressions (fail-open).
        """
        if not expressions:
            return []

        system_prompt = (
            "You are a strict Quantitative Portfolio Manager. Review the following Fast Expression alphas. "
            "Veto any that are mathematically trivial, redundant (like adding X and subtracting X), "
            "or lack economic intuition. Return ONLY a JSON array of the approved expressions. "
            "Do not include explanations."
        )
        user_prompt = json.dumps(expressions)

        try:
            raw = self._call_llm(system_prompt=system_prompt, user_prompt=user_prompt)
            if isinstance(raw, list):
                return [str(item) for item in raw]
            if not raw or not isinstance(raw, str):
                return expressions
            return self._parse_alphas(raw)
        except Exception as e:
            log.warning("Critique failed or returned invalid JSON (%s); failing open.", e)
            return expressions
