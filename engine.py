"""Simulation engine for WorldQuant BRAIN alpha generation pipeline.

Orchestrates concurrent alpha simulations, monitors polling endpoints,
retrieves in-sample performance metrics, and applies quality filter gates.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import tempfile
import time
from typing import Any
import requests

from api_client import BrainClient
from config import settings
import database
from generator import generate_expressions

log = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Container for alpha simulation output and quality evaluation."""

    expression: str
    category: str
    alpha_id: str | None
    status: str  # "passed", "failed_filters", or "error"
    metrics: dict[str, Any] | None = None
    error: str | None = None


class AlphaEngine:
    """Executes and manages concurrent alpha simulations on WorldQuant BRAIN."""

    def __init__(
        self,
        client: BrainClient,
        max_workers: int = 3,
        poll_interval: float | None = None,
        poll_timeout: float | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        """Initialize engine with BrainClient and concurrency parameters.

        Args:
            client: BrainClient instance for communicating with BRAIN API.
            max_workers: Max concurrent simulation threads (default 3).
            poll_interval: Polling interval in seconds (default from settings).
            poll_timeout: Max polling timeout in seconds (default from settings).
            db_path: Optional path to SQLite database. If None, uses default.
        """
        self.client = client
        self.max_workers = max_workers
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else getattr(settings, "poll_interval_seconds", 5)
        )
        self.poll_timeout = (
            poll_timeout
            if poll_timeout is not None
            else getattr(settings, "poll_timeout_seconds", 1800)
        )
        self.db_path = db_path

    def _simulate_one(self, expr: str, category: str) -> SimulationResult:
        """Submit a single simulation, poll for completion, and filter metrics.

        Args:
            expr: Fast Expression string to simulate.
            category: Data category of the alpha expression.

        Returns:
            SimulationResult: Result object containing status, alpha_id, metrics, or error.
        """
        log.info("Starting simulation for %s: %s", category, expr)
        try:
            payload = {
                "type": "REGULAR",
                "settings": {
                    "instrumentType": "EQUITY",
                    "region": "USA",
                    "universe": "TOP3000",
                    "delay": 1,
                    "decay": 0,
                    "neutralization": "INDUSTRY",
                    "truncation": 0.08,
                    "pasteurization": "ON",
                    "unitHandling": "VERIFY",
                    "nanHandling": "OFF",
                    "language": "FASTEXPR",
                    "visualization": False,
                    "testPeriod": "P0Y0M",
                },
                "regular": expr,
            }

            post_resp = self.client.request("POST", "/simulations", json=payload)
            location = post_resp.headers.get("Location") or post_resp.headers.get("location")
            if not location:
                raise RuntimeError(
                    f"Simulation creation failed: missing Location header (status {post_resp.status_code})"
                )

            # Polling loop
            start_time = time.monotonic()
            alpha_id: str | None = None

            while True:
                poll_resp = self.client.request("GET", location)
                poll_data = poll_resp.json()

                progress = float(poll_data.get("progress", 0.0) or 0.0)
                status_val = str(poll_data.get("status", "")).upper()

                if status_val in ("ERROR", "FAILED"):
                    err_msg = poll_data.get("message") or f"Simulation failed with status {status_val}"
                    return SimulationResult(
                        expression=expr,
                        category=category,
                        alpha_id=poll_data.get("alpha") or poll_data.get("alphaId") or poll_data.get("id"),
                        status="error",
                        metrics=None,
                        error=err_msg,
                    )

                if progress >= 1.0 or status_val in ("COMPLETE", "COMPLETED") or "alpha" in poll_data:
                    alpha_id = (
                        poll_data.get("alpha")
                        or poll_data.get("alphaId")
                        or poll_data.get("id")
                    )
                    break

                if time.monotonic() - start_time > self.poll_timeout:
                    return SimulationResult(
                        expression=expr,
                        category=category,
                        alpha_id=None,
                        status="error",
                        metrics=None,
                        error=f"Simulation timed out after {self.poll_timeout}s",
                    )

                time.sleep(self.poll_interval)

            if not alpha_id:
                return SimulationResult(
                    expression=expr,
                    category=category,
                    alpha_id=None,
                    status="error",
                    metrics=None,
                    error="Simulation completed but returned no alpha ID",
                )

            # Fetch metrics from /alphas/{alpha_id}
            alpha_resp = self.client.request("GET", f"/alphas/{alpha_id}")
            alpha_data = alpha_resp.json()
            is_metrics = alpha_data.get("is")
            if not isinstance(is_metrics, dict):
                return SimulationResult(
                    expression=expr,
                    category=category,
                    alpha_id=alpha_id,
                    status="error",
                    metrics=None,
                    error=f"Alpha {alpha_id} response missing 'is' metrics dictionary",
                )

            # Apply quality filters
            sharpe = is_metrics.get("sharpe")
            fitness = is_metrics.get("fitness")
            turnover = is_metrics.get("turnover")

            min_sharpe = getattr(settings, "min_sharpe", 1.25)
            min_fitness = getattr(settings, "min_fitness", 1.0)
            min_turnover = getattr(settings, "min_turnover", 0.01)
            max_turnover = getattr(settings, "max_turnover", 0.70)

            if (
                isinstance(sharpe, (int, float))
                and isinstance(fitness, (int, float))
                and isinstance(turnover, (int, float))
                and sharpe >= min_sharpe
                and fitness > min_fitness
                and min_turnover <= turnover <= max_turnover
            ):
                result_status = "passed"
            else:
                result_status = "failed_filters"

            # Persist completed simulation result to database
            if alpha_id:
                try:
                    database.insert_alpha(
                        {
                            "alpha_id": alpha_id,
                            "expression": expr,
                            "data_category": category,
                            "sharpe": float(sharpe) if isinstance(sharpe, (int, float)) else None,
                            "fitness": float(fitness) if isinstance(fitness, (int, float)) else None,
                            "turnover": float(turnover) if isinstance(turnover, (int, float)) else None,
                            "passed_filters": 1 if result_status == "passed" else 0,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "raw_response": is_metrics,
                        },
                        db_path=self.db_path,
                    )
                except Exception as db_err:
                    log.exception("Failed to insert alpha %s into database: %s", alpha_id, db_err)

            return SimulationResult(
                expression=expr,
                category=category,
                alpha_id=alpha_id,
                status=result_status,
                metrics=is_metrics,
                error=None,
            )

        except Exception as e:
            log.exception("Simulation error for %s (%s): %s", expr, category, e)
            return SimulationResult(
                expression=expr,
                category=category,
                alpha_id=None,
                status="error",
                metrics=None,
                error=str(e),
            )

    def run_batch(self, expressions: list[tuple[str, str]]) -> list[SimulationResult]:
        """Execute a batch of expressions concurrently across worker threads.

        Args:
            expressions: List of (expression, category) tuples to simulate.

        Returns:
            list[SimulationResult]: Completed simulation results for all expressions.
        """
        results: list[SimulationResult] = []
        if not expressions:
            return results

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_expr = {
                executor.submit(self._simulate_one, expr, cat): (expr, cat)
                for expr, cat in expressions
            }
            for future in as_completed(future_to_expr):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    expr, cat = future_to_expr[future]
                    results.append(
                        SimulationResult(
                            expression=expr,
                            category=cat,
                            alpha_id=None,
                            status="error",
                            metrics=None,
                            error=str(e),
                        )
                    )
        return results


class FakeResponse:
    """Mock HTTP response matching requests.Response interface."""

    def __init__(
        self,
        status_code: int,
        json_data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self.text = json.dumps(json_data)

    def json(self) -> dict[str, Any]:
        return self._json_data


class FakeClient(BrainClient):
    """Mock client subclassing BrainClient for zero-network testing and concurrency proofs."""

    def __init__(self) -> None:
        super().__init__()
        self._counter = 0

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Simulate network latency and return mock BRAIN API responses."""
        if method.upper() == "POST":
            # Simulate 1.0s network/submission latency
            time.sleep(1.0)
            self._counter += 1
            sim_id = f"sim-mock-{self._counter}"
            return FakeResponse(
                status_code=201,
                json_data={"id": sim_id},
                headers={"Location": f"https://api.worldquantbrain.com/simulations/{sim_id}"},
            )

        if "/simulations/" in path:
            sim_id = path.rstrip("/").split("/")[-1]
            return FakeResponse(
                status_code=200,
                json_data={
                    "id": sim_id,
                    "status": "COMPLETE",
                    "progress": 1.0,
                    "alpha": f"alpha-{sim_id}",
                },
            )

        if "/alphas/" in path:
            alpha_id = path.rstrip("/").split("/")[-1]
            return FakeResponse(
                status_code=200,
                json_data={
                    "id": alpha_id,
                    "is": {
                        "sharpe": 1.45,
                        "fitness": 1.15,
                        "turnover": 0.22,
                        "margin": 0.0018,
                    },
                },
            )

        return FakeResponse(status_code=200, json_data={})


def _main() -> None:
    parser = argparse.ArgumentParser(description="WorldQuant BRAIN Simulation Engine")
    parser.add_argument("--mock", action="store_true", help="Run 10 mock simulations to verify concurrency")
    parser.add_argument("--real", action="store_true", help="Run 1 real simulation against live BRAIN API")
    args = parser.parse_args()

    if args.mock:
        print("=== Running Mock Concurrency Test (10 expressions, max_workers=3) ===")
        expressions = generate_expressions(10, seed=42)
        fake_client = FakeClient()
        temp_db_path = Path(tempfile.gettempdir()) / f"alphas_mock_{int(time.time())}.db"

        try:
            engine = AlphaEngine(client=fake_client, max_workers=3, db_path=temp_db_path)

            start_time = time.perf_counter()
            results = engine.run_batch(expressions)
            elapsed = time.perf_counter() - start_time

            for i, res in enumerate(results, 1):
                sharpe = res.metrics.get("sharpe") if res.metrics else None
                fitness = res.metrics.get("fitness") if res.metrics else None
                turnover = res.metrics.get("turnover") if res.metrics else None
                print(
                    f"[{i:2d}] status={res.status:14s} | alpha_id={res.alpha_id} | "
                    f"metrics={{sharpe={sharpe}, fitness={fitness}, turnover={turnover}}} | "
                    f"expr={res.expression}"
                )

            print(f"\nWall-clock elapsed time: {elapsed:.2f}s")
            assert elapsed < 5.0, f"Expected elapsed time < 5.0s, got {elapsed:.2f}s"
            print("ASSERTION PASSED: Concurrency verified (10 items across 3 workers completed in < 5.0s).")
        finally:
            if temp_db_path.exists():
                try:
                    temp_db_path.unlink()
                except OSError:
                    pass

    elif args.real:
        print("=== Running Single Real Simulation against WorldQuant BRAIN API ===")
        client = BrainClient()
        engine = AlphaEngine(client=client, max_workers=1)
        expr_tuple = ("rank(close)", "price_volume")
        print(f"Submitting expression: {expr_tuple[0]} (category: {expr_tuple[1]})...")

        results = engine.run_batch([expr_tuple])
        res = results[0]

        print(f"\nResult:")
        print(f"  expression: {res.expression}")
        print(f"  category:   {res.category}")
        print(f"  alpha_id:   {res.alpha_id}")
        print(f"  status:     {res.status}")
        if res.metrics:
            print(f"  metrics:    Sharpe={res.metrics.get('sharpe')}, Fitness={res.metrics.get('fitness')}, Turnover={res.metrics.get('turnover')}")
        if res.error:
            print(f"  error:      {res.error}")

        client.close()
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
