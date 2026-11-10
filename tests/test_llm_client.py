"""Unit tests for llm_client module using google-genai SDK."""

from google.genai import errors, models
import pytest

import config
from llm_client import LLMClient


def test_key_rotation_on_429_multi_key(mocker):
    """Test that 429 APIError triggers key rotation and retry when multiple keys exist."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1", "key2"])

    mock_resp = mocker.MagicMock()
    mock_resp.text = '["rank(close)", "ts_delta(volume, 5)"]'

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
    assert res == ["rank(close)", "ts_delta(volume, 5)"]
    assert mock_generate.call_count == 2


def test_single_key_retry_on_429(mocker):
    """Test that single key configuration sleeps 60s and retries on 429 APIError."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])
    mock_sleep = mocker.patch("time.sleep")

    mock_resp = mocker.MagicMock()
    mock_resp.text = '["rank(close)"]'

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
    assert res == ["rank(close)"]
    assert mock_generate.call_count == 2


def test_json_parsing(mocker):
    """Test stripping of markdown fences and backticks from JSON array response."""
    mocker.patch.object(config, "GEMINI_API_KEYS", ["key1"])

    mock_resp = mocker.MagicMock()
    mock_resp.text = '```json\n["rank(close)"]\n```'

    mocker.patch.object(
        models.Models,
        "generate_content",
        return_value=mock_resp,
    )

    client = LLMClient()
    res = client.generate_alphas(known_fields={"price_volume": ["close"]}, operators=["rank"])

    assert res == ["rank(close)"]


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
