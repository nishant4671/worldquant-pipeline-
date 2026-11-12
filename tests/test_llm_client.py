"""Unit tests for resilient multi-provider llm_client using LiteLLM Router."""

import pytest
from litellm import Router
from litellm.exceptions import ServiceUnavailableError

from llm_client import AllProvidersFailedException, LLMClient


def test_generate_alphas_success(mocker):
    """Test successful alpha proposal parsing with mock LiteLLM Router."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = '[{"expression": "rank(close)"}]'
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    res = client.generate_alphas(known_fields={"price_volume": ["close"]})

    assert res == [{"expression": "rank(close)"}]
    mock_router.completion.assert_called_once()
    assert mock_router.completion.call_args.kwargs["model"] == "proposer-primary"


def test_all_providers_failed(mocker):
    """Test that Router failure raises AllProvidersFailedException."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_router.completion.side_effect = ServiceUnavailableError(
        "503", "gemini", "gemini-2.5-flash"
    )

    client = LLMClient(router=mock_router)
    with pytest.raises(AllProvidersFailedException):
        client.generate_alphas(known_fields={"price_volume": ["close"]})


def test_heavy_json_parser(mocker):
    """Test extracting JSON array embedded within conversational markdown text."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = (
        "Here are the alphas:\n```json\n[{\"expression\": \"rank(volume)\"}]\n```\nHope this helps!"
    )
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    res = client.generate_alphas(known_fields={"price_volume": ["volume"]})

    assert res == [{"expression": "rank(volume)"}]


def test_critic_approves(mocker):
    """Test that critique_alphas returns approved list when critic decision is APPROVE."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = '{"decision": "APPROVE", "critique": "Solid alpha"}'
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    inputs = ["rank(close)", "rank(close) - rank(close)"]
    approved = client.critique_alphas(inputs)

    assert approved == inputs
    assert mock_router.completion.call_args.kwargs["model"] == "critic-primary"


def test_critic_rejects(mocker):
    """Test that critique_alphas returns empty list when PM Critic rejects."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = '{"decision": "REJECT", "critique": "Trivial formula"}'
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    inputs = ["rank(close)"]
    approved = client.critique_alphas(inputs)

    assert approved == []


def test_critic_fails_open(mocker):
    """Test that critique_alphas fails open and returns original list on unparseable JSON."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = "Unparseable response with no JSON blocks"
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    inputs = ["rank(close)", "ts_delta(volume, 5)"]
    approved = client.critique_alphas(inputs)

    assert approved == inputs


def test_advanced_prompt_contains_operators(mocker):
    """Test that generate_alphas includes advanced operators in system prompt."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = '[{"expression": "rank(close)"}]'
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    client.generate_alphas(known_fields={"price_volume": ["close"]})

    call_kwargs = mock_router.completion.call_args.kwargs
    messages = call_kwargs["messages"]
    system_msg = next(m["content"] for m in messages if m["role"] == "system")
    assert "ts_decay_linear" in system_msg
    assert "group_neutralize" in system_msg


def test_prompt_includes_near_misses(mocker):
    """Test that generate_alphas includes near-miss feedback in user prompt when provided."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = '[{"expression": "rank(close)"}]'
    mock_router.completion.return_value = mock_resp

    near_misses = [
        {"alpha_id": "nm1", "expression": "rank(close - open)", "sharpe": 1.15},
        {"alpha_id": "nm2", "expression": "ts_rank(sales, 60)", "fitness": 0.85},
    ]

    client = LLMClient(router=mock_router)
    client.generate_alphas(
        known_fields={"price_volume": ["close"]},
        near_misses=near_misses,
    )

    call_kwargs = mock_router.completion.call_args.kwargs
    messages = call_kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Yesterday, these alphas almost passed:" in user_msg
    assert "rank(close - open)" in user_msg
    assert "ts_rank(sales, 60)" in user_msg


def test_proposer_prompt_forces_cross_category(mocker):
    """Test that generate_alphas enforces cross-category field combination in system prompt."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = '[{"expression": "rank(close)"}]'
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    client.generate_alphas(known_fields={"price_volume": ["close"]})

    call_kwargs = mock_router.completion.call_args.kwargs
    messages = call_kwargs["messages"]
    system_msg = next(m["content"] for m in messages if m["role"] == "system")
    assert "You MUST combine at least one Price-Volume field" in system_msg
    assert "counter_party" in system_msg
    assert "failure_mode" in system_msg


def test_critic_rejects_inverted_sign(mocker):
    """Test that critique_alphas rejects when critic detects an inverted sign."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = (
        '{"decision": "REJECT", "critique": "Sign inversion: hypothesis states long momentum but expression is negative."}'
    )
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    inputs = [
        {
            "mechanism": "Momentum",
            "direction": "Long high returns",
            "expression": "-rank(returns)",
        }
    ]
    approved = client.critique_alphas(inputs)
    assert approved == []


def test_critic_prompt_contains_sign_check(mocker):
    """Test that critique_alphas system prompt contains the independent sign check instruction."""
    mock_router = mocker.MagicMock(spec=Router)
    mock_resp = mocker.MagicMock()
    mock_resp.choices = [mocker.MagicMock()]
    mock_resp.choices[0].message.content = '{"decision": "APPROVE", "critique": "Valid sign"}'
    mock_router.completion.return_value = mock_resp

    client = LLMClient(router=mock_router)
    client.critique_alphas([{"expression": "rank(close)"}])

    call_kwargs = mock_router.completion.call_args.kwargs
    messages = call_kwargs["messages"]
    system_msg = next(m["content"] for m in messages if m["role"] == "system")
    assert "Independently derive what the sign of the expression SHOULD be" in system_msg


