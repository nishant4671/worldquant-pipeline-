"""Unit tests for main CLI orchestrator module."""

import sys
import pytest

from engine import SimulationResult
import main


def test_pipeline_mock_executes_flow(mocker, tmp_path):
    """Test that pipeline --mock executes the full flow end-to-end without errors."""
    mock_init_db = mocker.patch("database.init_db")
    mock_near_misses = mocker.patch("database.get_near_misses", return_value=[])
    mock_send_digest = mocker.patch("notifier.send_digest", return_value=[123])

    # Mock engine run_batch to return a passed alpha result
    fake_result = SimulationResult(
        expression="rank(close)",
        category="price_volume",
        alpha_id="alpha-mock-001",
        status="passed",
        metrics={"sharpe": 1.8, "fitness": 1.2, "turnover": 0.25},
    )
    mock_run_batch = mocker.patch("engine.AlphaEngine.run_batch", return_value=[fake_result])

    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "3", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    assert mock_init_db.called
    mock_near_misses.assert_called_once_with(limit=3)
    mock_run_batch.assert_called_once()
    mock_send_digest.assert_called_once()


def test_pipeline_zero_proposed_exits_gracefully(mocker):
    """Test that when 0 alphas are proposed, the pipeline terminates cleanly without error."""
    mocker.patch("database.init_db")
    mocker.patch("database.get_near_misses", return_value=[])
    mocker.patch.object(main.MockLLMClient, "generate_alphas", return_value=[])
    mock_run_batch = mocker.patch("engine.AlphaEngine.run_batch")

    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "3", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    mock_run_batch.assert_not_called()


def test_pipeline_all_rejected_exits_gracefully(mocker):
    """Test that when all proposed alphas fail static validation, the pipeline exits gracefully."""
    mocker.patch("database.init_db")
    mocker.patch("database.get_near_misses", return_value=[])
    mocker.patch(
        "validator.validate_batch",
        return_value=([], [({"expression": "invalid_expr"}, "Unknown field")]),
    )
    mock_run_batch = mocker.patch("engine.AlphaEngine.run_batch")

    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "3", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    mock_run_batch.assert_not_called()


def test_pipeline_all_vetoed_exits_gracefully(mocker):
    """Test that when PM Critic vetoes all alphas, the pipeline exits gracefully."""
    mocker.patch("database.init_db")
    mocker.patch("database.get_near_misses", return_value=[])
    mocker.patch.object(main.MockLLMClient, "critique_alphas", return_value=[])
    mock_run_batch = mocker.patch("engine.AlphaEngine.run_batch")

    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "3", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    mock_run_batch.assert_not_called()


def test_pipeline_logs_validator_rejection_event(mocker):
    """Verify that validator rejections trigger log_event with component='Validator'."""
    mocker.patch("database.init_db")
    mocker.patch("database.get_near_misses", return_value=[])
    mock_log_event = mocker.patch("database.log_event")
    mocker.patch(
        "validator.validate_batch",
        return_value=(
            [],
            [({"expression": "rank(fake_field)"}, "Hallucinated field/operator: fake_field")],
        ),
    )
    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "1", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    mock_log_event.assert_called_once()
    kwargs = mock_log_event.call_args.kwargs
    assert kwargs["component"] == "Validator"
    assert kwargs["status"] == "REJECTED"
    assert "fake_field" in kwargs["reason"]


def test_pipeline_logs_critic_rejection_event(mocker):
    """Verify that PM critic rejections trigger log_event with component='Critic'."""
    mocker.patch("database.init_db")
    mocker.patch("database.get_near_misses", return_value=[])
    mock_log_event = mocker.patch("database.log_event")
    mocker.patch.object(main.MockLLMClient, "critique_alphas", return_value=[])
    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "2", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    assert mock_log_event.call_count >= 1
    call_kwargs = [call.kwargs for call in mock_log_event.call_args_list]
    assert any(
        kw.get("component") == "Critic" and kw.get("status") == "REJECTED"
        for kw in call_kwargs
    )

