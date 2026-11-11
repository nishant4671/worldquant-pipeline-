"""Unit tests for llm_client module using google-genai SDK."""

from google.genai import errors, models
import pytest

import config
from llm_client import LLMClient


def test_key_rotation_on_429_multi_key(mocker):
    """Test that 429 APIError triggers key rotation and retry when multiple keys exist."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1", "key2"])

    mock_resp = mocker.MagicMock()
    mock_resp.text = '[{"hypothesis": "h1", "expression": "rank(close)", "expected_turnover": "low"}, {"hypothesis": "h2", "expression": "ts_delta(volume, 5)", "expected_turnover": "medium"}]'

    mock_generate = mocker.patch.object(
        models.Models,
        "generate_content",
        side_effect=[
            errors.APIError(code=429, response_json={}),
            mock_resp,
        ],
    )

    client = LLMClient()
    assert client.current_key_index == 0
    assert client.api_keys[client.current_key_index] == "key1"

    res = client.generate_alphas(
        known_fields={"price_volume": ["close", "volume"]},
        operators=["rank", "ts_delta"],
    )

    assert client.current_key_index == 1
    assert client.api_keys[client.current_key_index] == "key2"
    assert res == [
        {"hypothesis": "h1", "expression": "rank(close)", "expected_turnover": "low"},
        {"hypothesis": "h2", "expression": "ts_delta(volume, 5)", "expected_turnover": "medium"},
    ]
    assert mock_generate.call_count == 2


def test_single_key_retry_on_429(mocker):
    """Test that single key configuration sleeps 60s and retries on 429 APIError."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])
    mock_sleep = mocker.patch("time.sleep")

    mock_resp = mocker.MagicMock()
    mock_resp.text = '[{"hypothesis": "h1", "expression": "rank(close)", "expected_turnover": "low"}]'

    mock_generate = mocker.patch.object(
        models.Models,
        "generate_content",
        side_effect=[
            errors.APIError(code=429, response_json={}),
            mock_resp,
        ],
    )

    client = LLMClient()
    res = client.generate_alphas(known_fields={"price_volume": ["close"]}, operators=["rank"])

    mock_sleep.assert_called_once_with(60)
    assert res == [{"hypothesis": "h1", "expression": "rank(close)", "expected_turnover": "low"}]
    assert mock_generate.call_count == 2


def test_json_parsing(mocker):
    """Test stripping of markdown fences and backticks from JSON array response."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])

    mock_resp = mocker.MagicMock()
    mock_resp.text = '```json\n[{"hypothesis": "test", "expression": "rank(close)", "expected_turnover": "low"}]\n```'

    mocker.patch.object(
        models.Models,
        "generate_content",
        return_value=mock_resp,
    )

    client = LLMClient()
    res = client.generate_alphas(known_fields={"price_volume": ["close"]}, operators=["rank"])

    assert res == [{"hypothesis": "test", "expression": "rank(close)", "expected_turnover": "low"}]


def test_invalid_json_handling(mocker):
    """Test that unparseable JSON output retries and gracefully returns an empty list."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])

    mock_resp = mocker.MagicMock()
    mock_resp.text = "Here are your alphas: rank(close)"

    mock_generate = mocker.patch.object(
        models.Models,
        "generate_content",
        return_value=mock_resp,
    )

    client = LLMClient()
    res = client.generate_alphas(known_fields={"price_volume": ["close"]}, operators=["rank"])

    assert res == []
    # Attempt 1 + 1 retry = 2 calls
    assert mock_generate.call_count == 2


def test_critic_approves(mocker):
    """Test that critique_alphas returns approved list from _call_llm."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])
    client = LLMClient()
    mocker.patch.object(client, "_call_llm", return_value='{"decision": "APPROVE", "critique": "Good"}')

    inputs = ["rank(close)", "rank(close) - rank(close)"]
    approved = client.critique_alphas(inputs)
    assert approved == inputs


def test_critic_rejects(mocker):
    """Test that critique_alphas returns empty list when PM Critic rejects."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])
    client = LLMClient()
    mocker.patch.object(client, "_call_llm", return_value='{"decision": "REJECT", "critique": "Trivial math"}')

    inputs = ["rank(close)"]
    approved = client.critique_alphas(inputs)
    assert approved == []


def test_critic_fails_open(mocker):
    """Test that critique_alphas fails open and returns original list on invalid JSON."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])
    client = LLMClient()
    mocker.patch.object(client, "_call_llm", return_value="invalid JSON response")

    inputs = ["rank(close)", "ts_delta(volume, 5)"]
    approved = client.critique_alphas(inputs)
    assert approved == inputs


def test_advanced_prompt_contains_operators(mocker):
    """Test that generate_alphas includes advanced operators in system prompt."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])
    client = LLMClient()
    mock_call = mocker.patch.object(client, "_call_llm", return_value='[{"hypothesis": "h", "expression": "rank(close)", "expected_turnover": "low"}]')

    client.generate_alphas(known_fields={"price_volume": ["close"]})

    assert mock_call.called
    kwargs = mock_call.call_args.kwargs
    args = mock_call.call_args.args
    system_prompt = kwargs.get("system_prompt") or (args[0] if args else "")
    assert "ts_decay_linear" in system_prompt
    assert "group_neutralize" in system_prompt


def test_prompt_includes_near_misses(mocker):
    """Test that generate_alphas includes near-miss feedback in user prompt when provided."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])
    client = LLMClient()
    mock_call = mocker.patch.object(
        client,
        "_call_llm",
        return_value='[{"hypothesis": "h", "expression": "rank(close)", "expected_turnover": "low"}]',
    )

    near_misses = [
        {"alpha_id": "nm1", "expression": "rank(close - open)", "sharpe": 1.15},
        {"alpha_id": "nm2", "expression": "ts_rank(sales, 60)", "fitness": 0.85},
    ]

    client.generate_alphas(
        known_fields={"price_volume": ["close"]},
        near_misses=near_misses,
    )

    assert mock_call.called
    kwargs = mock_call.call_args.kwargs
    args = mock_call.call_args.args
    user_prompt = kwargs.get("user_prompt") or (args[1] if len(args) > 1 else "")
    assert "Yesterday, these alphas almost passed:" in user_prompt
    assert "rank(close - open)" in user_prompt
    assert "ts_rank(sales, 60)" in user_prompt


