"""Main CLI entry point for WorldQuant BRAIN automated pipeline.

Orchestrates authentication, expression generation, validation, critique,
simulation, persistence, Telegram notification, and full end-to-end execution.
"""

import argparse
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any

from api_client import BrainApiError, BrainClient
from config import TELEGRAM_CHAT_ID, settings
import database
from engine import AlphaEngine, FakeClient, SimulationResult
from generator import KNOWN_FIELDS, generate_expressions
from llm_client import LLMClient
import notifier
import validator

log = logging.getLogger(__name__)

FAST_EXPR_OPERATORS = [
    "rank",
    "ts_rank",
    "ts_delta",
    "ts_mean",
    "ts_zscore",
    "ts_decay_linear",
    "group_rank",
]


class MockLLMClient:
    """Mock LLM client returning verified fast expressions without network calls."""

    def generate_alphas(
        self,
        known_fields: dict[str, list[str]],
        operators: list[str],
        num_alphas: int = 5,
        near_misses: list[dict] | None = None,
    ) -> list[dict]:
        """Propose canned valid alpha expressions for mock runs."""
        sample_fields = ["close", "volume", "open", "high", "low", "returns"]
        return [
            {
                "hypothesis": f"Momentum on {sample_fields[i % len(sample_fields)]}",
                "expression": f"rank({sample_fields[i % len(sample_fields)]})",
                "expected_turnover": "low",
            }
            for i in range(num_alphas)
        ]

    def critique_alphas(self, expressions: list[Any]) -> list[Any]:
        """Approve all expressions in mock runs."""
        return expressions


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging to write to pipeline.log (append mode) and stdout."""
    log_level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    root = logging.getLogger()
    root.setLevel(log_level)

    # Clear existing handlers to prevent duplicate lines
    for h in list(root.handlers):
        root.removeHandler(h)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    file_handler = logging.FileHandler("pipeline.log", mode="a", encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _cmd_auth(args: argparse.Namespace) -> None:
    """Authenticate with WorldQuant BRAIN API and print token expiry."""
    client = BrainClient()
    client._authenticate()
    print(f"Auth OK. Expires at {client._expiry}")


def _cmd_generate(args: argparse.Namespace) -> None:
    """Generate N unique Fast Expressions and print them."""
    pairs = generate_expressions(n=args.n)
    for expr, _ in pairs:
        print(expr)


def _cmd_simulate(args: argparse.Namespace) -> None:
    """Generate N expressions and simulate them (real or mock)."""
    pairs = generate_expressions(n=args.n)
    client = FakeClient() if args.mock else BrainClient()
    engine = AlphaEngine(client=client)
    results = engine.run_batch(pairs)

    for res in results:
        if res.status == "passed":
            print(f"PASS: {res.alpha_id} | {res.expression} | {res.metrics}")
        elif res.status == "failed_filters":
            print(f"FAIL: {res.alpha_id} | {res.expression} | {res.metrics}")
        elif res.status == "skipped":
            print(f"SKIP: {res.expression} | {res.error}")
        else:
            print(f"ERROR: {res.expression} | {res.error}")

    passed_count = sum(1 for r in results if r.status == "passed")
    print(f"Simulated {len(results)} alphas ({passed_count} passed filters).")


def _cmd_status(args: argparse.Namespace) -> None:
    """Print database alpha summary."""
    print(database.summary())


def _cmd_digest(args: argparse.Namespace) -> None:
    """Send digest to Telegram or display dry-run payloads."""
    if args.send:
        sent = notifier.send_digest()
        print(f"Sent {len(sent)} alphas.")
    else:
        candidates = database.list_passing(limit=getattr(settings, "top_n", 4))
        if not candidates:
            sample = {
                "alpha_id": "A123Sample",
                "expression": "rank(close)",
                "sharpe": 1.75,
                "fitness": 1.35,
                "turnover": 0.22,
            }
            text, reply_markup = notifier.format_alpha_message(sample)
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "reply_markup": reply_markup,
            }
            print(json.dumps(payload, indent=2))
        else:
            for cand in candidates:
                text, reply_markup = notifier.format_alpha_message(cand)
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "reply_markup": reply_markup,
                }
                print(json.dumps(payload, indent=2))


def _cmd_listen(args: argparse.Namespace) -> None:
    """Poll for Telegram button interactions for 600 seconds."""
    notifier.poll_actions(600)


def _cmd_pipeline(args: argparse.Namespace) -> None:
    """Run full intelligent pipeline: Init DB -> Propose -> Validate -> Critique -> Simulate -> Digest."""
    log.info("Step 1: Initializing database...")
    database.init_db()

    log.info("Step 2: Initializing LLM client (mock=%s)...", args.mock)
    if args.mock:
        log.info("[MOCK] Running with mock LLM client and mock Brain client.")
        client = FakeClient()
        try:
            inst = LLMClient()
            if isinstance(inst, LLMClient):
                llm_client = MockLLMClient()
            else:
                llm_client = inst
        except Exception:
            llm_client = MockLLMClient()
    else:
        client = BrainClient()
        llm_client = LLMClient()

    near_misses = database.get_near_misses(limit=3)
    log.info("Found %d near-miss alphas for mutation feedback.", len(near_misses))

    log.info("Step 3: Proposing %d Fast Expressions via LLM...", args.n)
    proposed = llm_client.generate_alphas(
        KNOWN_FIELDS, FAST_EXPR_OPERATORS, num_alphas=args.n, near_misses=near_misses
    )
    if not proposed:
        log.warning("No alphas proposed by LLM. Exiting pipeline gracefully.")
        print("Pipeline finished: 0 alphas proposed.")
        return

    log.info("Step 4: Statically validating %d proposed expressions...", len(proposed))
    valid_exprs, rejected = validator.validate_batch(proposed)
    for rej_item, reason in rejected:
        rej_expr = (
            rej_item.get("expression", str(rej_item))
            if isinstance(rej_item, dict)
            else str(rej_item)
        )
        log.warning("Validator rejected '%s': %s", rej_expr, reason)

    if not valid_exprs:
        log.warning("All proposed alphas were rejected by validator. Exiting pipeline gracefully.")
        print("Pipeline finished: 0 valid alphas remaining.")
        return

    log.info("Step 5: Passing %d valid expressions to Portfolio Manager Critic...", len(valid_exprs))
    approved = llm_client.critique_alphas(valid_exprs)
    if not approved:
        log.warning("No alphas approved by PM Critic. Exiting pipeline gracefully.")
        print("Pipeline finished: 0 approved alphas remaining.")
        return

    log.info("Step 6: Simulating %d approved alphas (mock=%s)...", len(approved), args.mock)
    for alpha in approved:
        expr = alpha.get("expression", str(alpha)) if isinstance(alpha, dict) else str(alpha)
        hypothesis = (
            alpha.get("hypothesis", "N/A") if isinstance(alpha, dict) else "N/A"
        )
        log.info("Simulating: %s (Hypothesis: %s)", expr, hypothesis)

    pairs = [
        (
            alpha["expression"] if isinstance(alpha, dict) else str(alpha),
            "llm_generated",
        )
        for alpha in approved
    ]
    engine = AlphaEngine(client=client)
    results = engine.run_batch(pairs)

    passed_count = sum(1 for r in results if r.status == "passed")
    skipped_count = sum(1 for r in results if r.status == "skipped")
    error_count = sum(1 for r in results if r.status == "error")
    print(
        f"Simulation completed: {len(results)} simulated "
        f"({passed_count} passed, {skipped_count} skipped, {error_count} errors)."
    )

    log.info("Step 7: Sending daily digest to Telegram...")
    sent = notifier.send_digest()
    print(f"Digest step completed: sent {len(sent)} alphas.")


def _build_parser() -> argparse.ArgumentParser:
    """Build command-line parser with shared logging options and subcommands."""
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable DEBUG logging"
    )

    parser = argparse.ArgumentParser(
        description="WorldQuant BRAIN Automated Alpha Generation Pipeline",
        parents=[common_parser],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # auth
    auth_p = subparsers.add_parser("auth", parents=[common_parser], help="Authenticate with BRAIN API")
    auth_p.set_defaults(func=_cmd_auth)

    # generate N
    gen_p = subparsers.add_parser("generate", parents=[common_parser], help="Generate N expressions")
    gen_p.add_argument("n", type=int, help="Number of expressions to generate")
    gen_p.set_defaults(func=_cmd_generate)

    # simulate N --mock
    sim_p = subparsers.add_parser("simulate", parents=[common_parser], help="Simulate N expressions")
    sim_p.add_argument("n", type=int, help="Number of expressions to simulate")
    sim_p.add_argument("--mock", action="store_true", help="Run with mock client")
    sim_p.set_defaults(func=_cmd_simulate)

    # status
    status_p = subparsers.add_parser("status", parents=[common_parser], help="Print database summary")
    status_p.set_defaults(func=_cmd_status)

    # digest --send
    dig_p = subparsers.add_parser("digest", parents=[common_parser], help="Telegram digest")
    dig_p.add_argument("--send", action="store_true", help="Send digest to Telegram")
    dig_p.set_defaults(func=_cmd_digest)

    # listen
    listen_p = subparsers.add_parser("listen", parents=[common_parser], help="Listen for Telegram button interactions")
    listen_p.set_defaults(func=_cmd_listen)

    # pipeline N --mock
    pipe_p = subparsers.add_parser("pipeline", parents=[common_parser], help="Run full pipeline")
    pipe_p.add_argument("n", type=int, help="Number of expressions to generate and simulate")
    pipe_p.add_argument("--mock", action="store_true", help="Run with mock client")
    pipe_p.set_defaults(func=_cmd_pipeline)

    return parser


def main() -> int:
    """Main execution function with timing, logging, and error handling."""
    start_time = time.monotonic()
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.verbose)

    exit_code = 0
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("Shutting down...")
        exit_code = 130
    except BrainApiError as e:
        print(f"BRAIN API Error: {e}")
        exit_code = 1
    except Exception as e:
        log.exception("Unexpected pipeline failure: %s", e)
        print(f"Error: {e}")
        exit_code = 1
    finally:
        elapsed = time.monotonic() - start_time
        print(f"DONE in {elapsed:.2f}s")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
