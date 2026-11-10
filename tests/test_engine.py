"""Unit tests for engine module."""

import dataclasses
import json
import threading
import time
import pytest
import requests

from api_client import BrainApiError, BrainClient
import config
import database
from engine import AlphaEngine, BreakerState, CircuitBreaker, SimulationResult


def _make_mock_client_for_metrics(mocker, metrics, progress=1.0, alpha_id="A123"):
    """Helper to configure a mock BrainClient returning defined simulation and metrics responses."""
    mock_client = mocker.MagicMock(spec=BrainClient)

    def mock_req(method, path, **kwargs):
        resp = mocker.MagicMock(spec=requests.Response)
        if method.upper() == "POST":
            resp.status_code = 201
            resp.headers = {"Location": "https://api.worldquantbrain.com/simulations/sim-1"}
            resp.json.return_value = {"id": "sim-1"}
            resp.text = json.dumps({"id": "sim-1"})
            return resp

        if "/simulations/" in path:
            resp.status_code = 200
            json_data = {"progress": progress, "alpha": alpha_id, "status": "COMPLETE"}
            resp.json.return_value = json_data
            resp.text = json.dumps(json_data)
            return resp

        if "/alphas/" in path:
            resp.status_code = 200
            json_data = {"id": alpha_id, "is": metrics}
            resp.json.return_value = json_data
            resp.text = json.dumps(json_data)
            return resp

        resp.status_code = 200
        resp.json.return_value = {}
        resp.text = "{}"
        return resp

    mock_client.request.side_effect = mock_req
    return mock_client


def test_concurrency_limit(mocker, tmp_path):
    """Test that max_workers=3 limits concurrent active requests to exactly 3."""
    active_threads = 0
    max_active_threads = 0
    lock = threading.Lock()
    db_file = tmp_path / "test_concurrency.db"

    def mock_request(method, path, **kwargs):
        nonlocal active_threads, max_active_threads
        with lock:
            active_threads += 1
            if active_threads > max_active_threads:
                max_active_threads = active_threads

        try:
            time.sleep(0.05)
            mock_resp = mocker.MagicMock(spec=requests.Response)
            if method.upper() == "POST":
                mock_resp.status_code = 201
                mock_resp.headers = {"Location": "https://api.worldquantbrain.com/simulations/sim-test"}
                mock_resp.json.return_value = {"id": "sim-test"}
                mock_resp.text = json.dumps({"id": "sim-test"})
                return mock_resp

            if "/simulations/" in path:
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"progress": 1.0, "alpha": "A123", "status": "COMPLETE"}
                mock_resp.text = json.dumps({"progress": 1.0, "alpha": "A123"})
                return mock_resp

            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "id": "A123",
                "is": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.2},
            }
            mock_resp.text = json.dumps({"id": "A123", "is": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.2}})
            return mock_resp
        finally:
            with lock:
                active_threads -= 1

    mock_client = mocker.MagicMock(spec=BrainClient)
    mock_client.request.side_effect = mock_request

    engine = AlphaEngine(client=mock_client, max_workers=3, db_path=db_file)
    expressions = [(f"expr_{i}", "price_volume") for i in range(10)]
    results = engine.run_batch(expressions)

    assert len(results) == 10
    assert max_active_threads == 3


def test_filter_pass(mocker, tmp_path):
    """Test that an alpha meeting all quality thresholds receives status='passed'."""
    db_file = tmp_path / "test_filter_pass.db"
    metrics = {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.3}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client, db_path=db_file)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "passed"
    assert result.alpha_id == "A123"
    assert result.metrics == metrics


def test_filter_fail_sharpe(mocker, tmp_path):
    """Test that an alpha failing Sharpe threshold receives status='failed_filters'."""
    db_file = tmp_path / "test_fail_sharpe.db"
    metrics = {"sharpe": 1.0, "fitness": 1.5, "turnover": 0.3}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client, db_path=db_file)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_filter_fail_fitness(mocker, tmp_path):
    """Test that an alpha failing fitness threshold receives status='failed_filters'."""
    db_file = tmp_path / "test_fail_fitness.db"
    metrics = {"sharpe": 2.0, "fitness": 0.9, "turnover": 0.3}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client, db_path=db_file)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_filter_fail_turnover_low(mocker, tmp_path):
    """Test that an alpha failing minimum turnover threshold receives status='failed_filters'."""
    db_file = tmp_path / "test_fail_turnover_low.db"
    metrics = {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.005}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client, db_path=db_file)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_filter_fail_turnover_high(mocker, tmp_path):
    """Test that an alpha failing maximum turnover threshold receives status='failed_filters'."""
    db_file = tmp_path / "test_fail_turnover_high.db"
    metrics = {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.8}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client, db_path=db_file)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_api_error_handling(mocker, tmp_path):
    """Test that BrainApiError during simulation is caught and returns status='error'."""
    db_file = tmp_path / "test_api_err.db"
    mock_client = mocker.MagicMock(spec=BrainClient)
    mock_client.request.side_effect = BrainApiError(500, "Internal Server Error")

    engine = AlphaEngine(client=mock_client, db_path=db_file)
    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "error"
    assert result.error is not None
    assert "Internal Server Error" in result.error


def test_polling_timeout(mocker, monkeypatch, tmp_path):
    """Test that simulation timing out returns status='error' with timeout message."""
    db_file = tmp_path / "test_timeout.db"
    mock_client = mocker.MagicMock(spec=BrainClient)

    def mock_req(method, path, **kwargs):
        resp = mocker.MagicMock(spec=requests.Response)
        if method.upper() == "POST":
            resp.status_code = 201
            resp.headers = {"Location": "https://api.worldquantbrain.com/simulations/sim-1"}
            resp.json.return_value = {"id": "sim-1"}
            resp.text = json.dumps({"id": "sim-1"})
            return resp

        resp.status_code = 200
        resp.json.return_value = {"progress": 0.5}
        resp.text = json.dumps({"progress": 0.5})
        return resp

    mock_client.request.side_effect = mock_req

    fake_settings = dataclasses.replace(
        config.settings,
        poll_timeout_seconds=0.1,
        poll_interval_seconds=0.01,
    )
    monkeypatch.setattr("engine.settings", fake_settings)

    engine = AlphaEngine(client=mock_client, db_path=db_file)
    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "error"
    assert result.error is not None
    assert "timed out" in result.error.lower() or "timeout" in result.error.lower()


def test_missing_metrics_handling(mocker, tmp_path):
    """Test that missing 'is' metrics dictionary returns status='error'."""
    db_file = tmp_path / "test_missing_metrics.db"
    mock_client = mocker.MagicMock(spec=BrainClient)

    def mock_req(method, path, **kwargs):
        resp = mocker.MagicMock(spec=requests.Response)
        if method.upper() == "POST":
            resp.status_code = 201
            resp.headers = {"Location": "https://api.worldquantbrain.com/simulations/sim-1"}
            resp.json.return_value = {"id": "sim-1"}
            resp.text = json.dumps({"id": "sim-1"})
            return resp

        if "/simulations/" in path:
            resp.status_code = 200
            resp.json.return_value = {"progress": 1.0, "alpha": "A123", "status": "COMPLETE"}
            resp.text = json.dumps({"progress": 1.0, "alpha": "A123"})
            return resp

        if "/alphas/" in path:
            resp.status_code = 200
            resp.json.return_value = {"id": "A123"}  # No "is" key
            resp.text = json.dumps({"id": "A123"})
            return resp

        resp.status_code = 200
        resp.json.return_value = {}
        resp.text = "{}"
        return resp

    mock_client.request.side_effect = mock_req

    engine = AlphaEngine(client=mock_client, db_path=db_file)
    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "error"


def test_engine_saves_passing_to_db(mocker, tmp_path):
    """Test that a passing simulation result is persisted with passed_filters=1."""
    db_file = tmp_path / "test_engine_save_pass.db"
    metrics = {"sharpe": 1.95, "fitness": 1.45, "turnover": 0.28}
    client = _make_mock_client_for_metrics(mocker, metrics, progress=1.0, alpha_id="alpha-pass-999")

    engine = AlphaEngine(client=client, db_path=db_file)
    result = engine._simulate_one("ts_rank(close, 20)", "price_volume")

    assert result.status == "passed"
    row = database.get_alpha("alpha-pass-999", db_path=db_file)
    assert row is not None
    assert row["alpha_id"] == "alpha-pass-999"
    assert row["expression"] == "ts_rank(close, 20)"
    assert row["data_category"] == "price_volume"
    assert row["sharpe"] == 1.95
    assert row["fitness"] == 1.45
    assert row["turnover"] == 0.28
    assert row["passed_filters"] == 1


def test_engine_ignores_failing_in_db(mocker, tmp_path):
    """Test that an alpha failing filters is saved with passed_filters=0."""
    db_file = tmp_path / "test_engine_failing.db"
    metrics = {"sharpe": 0.5, "fitness": 0.2, "turnover": 0.05}
    client = _make_mock_client_for_metrics(mocker, metrics, progress=1.0, alpha_id="alpha-fail-111")

    engine = AlphaEngine(client=client, db_path=db_file)
    result = engine._simulate_one("rank(volume)", "price_volume")

    assert result.status == "failed_filters"
    row = database.get_alpha("alpha-fail-111", db_path=db_file)
    assert row is not None
    assert row["alpha_id"] == "alpha-fail-111"
    assert row["passed_filters"] == 0


def test_engine_skips_duplicates(mocker, tmp_path):
    """Mock database.expression_exists to return True and assert API is never called."""
    db_file = tmp_path / "test_engine_dup.db"
    mocker.patch("database.expression_exists", return_value=True)
    mock_client = mocker.MagicMock(spec=BrainClient)

    engine = AlphaEngine(client=mock_client, db_path=db_file)
    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "skipped"
    assert result.error == "Duplicate expression"
    assert mock_client.request.call_count == 0


def test_polling_survives_500(mocker, tmp_path):
    """Mock polling GET to return 500 three times before progress=1.0; assert simulation succeeds."""
    db_file = tmp_path / "test_survives_500.db"
    mocker.patch("time.sleep")
    mock_client = mocker.MagicMock(spec=BrainClient)

    poll_call_count = 0

    def mock_req(method, path, **kwargs):
        nonlocal poll_call_count
        resp = mocker.MagicMock(spec=requests.Response)
        if method.upper() == "POST":
            resp.status_code = 201
            resp.headers = {"Location": "https://api.worldquantbrain.com/simulations/sim-retry-500"}
            resp.json.return_value = {"id": "sim-retry-500"}
            return resp

        if "/simulations/" in path:
            poll_call_count += 1
            if poll_call_count <= 3:
                raise BrainApiError(500, "Internal Server Error")
            resp.status_code = 200
            resp.json.return_value = {
                "progress": 1.0,
                "alpha": "alpha-survived-500",
                "status": "COMPLETE",
            }
            return resp

        if "/alphas/" in path:
            resp.status_code = 200
            resp.json.return_value = {
                "id": "alpha-survived-500",
                "is": {"sharpe": 1.85, "fitness": 1.35, "turnover": 0.22},
            }
            return resp

        resp.status_code = 200
        resp.json.return_value = {}
        return resp

    mock_client.request.side_effect = mock_req

    engine = AlphaEngine(client=mock_client, db_path=db_file)
    result = engine._simulate_one("ts_rank(close, 10)", "price_volume")

    assert result.status == "passed"
    assert result.alpha_id == "alpha-survived-500"
    assert poll_call_count == 4


def test_circuit_breaker_trips_on_three_429s(mocker):
    """Assert circuit breaker trips to OPEN after 3 consecutive 429 failures and sends alert."""
    mock_alert = mocker.patch("notifier.send_alert")
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
    assert cb.state == BreakerState.CLOSED

    cb.record_failure(429)
    assert cb.state == BreakerState.CLOSED
    assert cb.consecutive_failures == 1
    mock_alert.assert_not_called()

    cb.record_failure(429)
    assert cb.state == BreakerState.CLOSED
    assert cb.consecutive_failures == 2
    mock_alert.assert_not_called()

    cb.record_failure(429)
    assert cb.state == BreakerState.OPEN
    assert cb.consecutive_failures == 3
    mock_alert.assert_called_once_with(
        "CIRCUIT BREAKER TRIPPED: 3 consecutive 429s. Halting pipeline."
    )

    with pytest.raises(BrainApiError) as exc_info:
        cb.check_state()
    assert exc_info.value.status == 429
    assert "Circuit breaker OPEN" in str(exc_info.value)


def test_circuit_breaker_cooldown(mocker):
    """Assert circuit breaker transitions to HALF_OPEN after cooldown period elapses."""
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
    now = 1770000000.0
    mocker.patch("time.time", return_value=now)
    cb.record_failure(429)
    cb.record_failure(429)
    cb.record_failure(429)
    assert cb.state == BreakerState.OPEN
    assert cb.last_failure_time == now

    # Cooldown not elapsed (e.g. 100s later)
    mocker.patch("time.time", return_value=now + 100)
    with pytest.raises(BrainApiError) as exc_info:
        cb.check_state()
    assert exc_info.value.status == 429
    assert cb.state == BreakerState.OPEN

    # Cooldown elapsed (301s later)
    mocker.patch("time.time", return_value=now + 301)
    cb.check_state()
    assert cb.state == BreakerState.HALF_OPEN


def test_circuit_breaker_record_success():
    """Assert record_success resets failures and sets state to CLOSED."""
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
    cb.consecutive_failures = 2
    cb.state = BreakerState.HALF_OPEN
    cb.record_success()
    assert cb.consecutive_failures == 0
    assert cb.state == BreakerState.CLOSED


def test_engine_trips_circuit_breaker(mocker, tmp_path):
    """Assert AlphaEngine records 429 failures and trips breaker."""
    db_file = tmp_path / "test_engine_breaker.db"
    mocker.patch("notifier.send_alert")
    mock_client = mocker.MagicMock(spec=BrainClient)
    mock_client.request.side_effect = BrainApiError(429, "Too Many Requests")

    engine = AlphaEngine(client=mock_client, db_path=db_file)
    for i in range(3):
        res = engine._simulate_one(f"expr_{i}", "price_volume")
        assert res.status == "error"

    assert engine.breaker.state == BreakerState.OPEN
    # Next call should be blocked by open breaker without calling request
    req_count = mock_client.request.call_count
    res_blocked = engine._simulate_one("expr_blocked", "price_volume")
    assert res_blocked.status == "error"
    assert "Circuit breaker OPEN" in str(res_blocked.error)
    assert mock_client.request.call_count == req_count



