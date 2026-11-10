"""Unit tests for engine module."""

import dataclasses
import json
import threading
import time
import pytest
import requests

from api_client import BrainApiError, BrainClient
import config
from engine import AlphaEngine, SimulationResult


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


def test_concurrency_limit(mocker):
    """Test that max_workers=3 limits concurrent active requests to exactly 3."""
    active_threads = 0
    max_active_threads = 0
    lock = threading.Lock()

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

    engine = AlphaEngine(client=mock_client, max_workers=3)
    expressions = [(f"expr_{i}", "price_volume") for i in range(10)]
    results = engine.run_batch(expressions)

    assert len(results) == 10
    assert max_active_threads == 3


def test_filter_pass(mocker):
    """Test that an alpha meeting all quality thresholds receives status='passed'."""
    metrics = {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.3}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "passed"
    assert result.alpha_id == "A123"
    assert result.metrics == metrics


def test_filter_fail_sharpe(mocker):
    """Test that an alpha failing Sharpe threshold receives status='failed_filters'."""
    metrics = {"sharpe": 1.0, "fitness": 1.5, "turnover": 0.3}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_filter_fail_fitness(mocker):
    """Test that an alpha failing fitness threshold receives status='failed_filters'."""
    metrics = {"sharpe": 2.0, "fitness": 0.9, "turnover": 0.3}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_filter_fail_turnover_low(mocker):
    """Test that an alpha failing minimum turnover threshold receives status='failed_filters'."""
    metrics = {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.005}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_filter_fail_turnover_high(mocker):
    """Test that an alpha failing maximum turnover threshold receives status='failed_filters'."""
    metrics = {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.8}
    client = _make_mock_client_for_metrics(mocker, metrics)
    engine = AlphaEngine(client=client)

    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "failed_filters"
    assert result.metrics == metrics


def test_api_error_handling(mocker):
    """Test that BrainApiError during simulation is caught and returns status='error'."""
    mock_client = mocker.MagicMock(spec=BrainClient)
    mock_client.request.side_effect = BrainApiError(500, "Internal Server Error")

    engine = AlphaEngine(client=mock_client)
    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "error"
    assert result.error is not None
    assert "Internal Server Error" in result.error


def test_polling_timeout(mocker, monkeypatch):
    """Test that simulation timing out returns status='error' with timeout message."""
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

    engine = AlphaEngine(client=mock_client)
    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "error"
    assert result.error is not None
    assert "timed out" in result.error.lower() or "timeout" in result.error.lower()


def test_missing_metrics_handling(mocker):
    """Test that missing 'is' metrics dictionary returns status='error'."""
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

    engine = AlphaEngine(client=mock_client)
    result = engine._simulate_one("rank(close)", "price_volume")

    assert result.status == "error"
