"""Unit tests for main CLI orchestrator module."""

import sys
import pytest

from engine import SimulationResult
import main


def test_pipeline_mock_executes_flow(mocker, tmp_path):
    """Test that pipeline --mock executes the full flow end-to-end without errors."""
    mock_init_db = mocker.patch("database.init_db")
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
    mock_run_batch.assert_called_once()
    mock_send_digest.assert_called_once()


def test_pipeline_zero_proposed_exits_gracefully(mocker):
    """Test that when 0 alphas are proposed, the pipeline terminates cleanly without error."""
    mocker.patch("database.init_db")
    mocker.patch.object(main.MockLLMClient, "generate_alphas", return_value=[])
    mock_run_batch = mocker.patch("engine.AlphaEngine.run_batch")

    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "3", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    mock_run_batch.assert_not_called()


def test_pipeline_all_rejected_exits_gracefully(mocker):
    """Test that when all proposed alphas fail static validation, the pipeline exits gracefully."""
    mocker.patch("database.init_db")
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
    mocker.patch.object(main.MockLLMClient, "critique_alphas", return_value=[])
    mock_run_batch = mocker.patch("engine.AlphaEngine.run_batch")

    mocker.patch.object(sys, "argv", ["main.py", "pipeline", "3", "--mock"])

    exit_code = main.main()

    assert exit_code == 0
    mock_run_batch.assert_not_called()
