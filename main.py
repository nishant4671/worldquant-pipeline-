"""Main CLI entry point for WorldQuant BRAIN automated pipeline.

Orchestrates authentication, expression generation, simulation, persistence,
Telegram notification, and full end-to-end execution.
"""

import argparse
import json
import logging
import sys
import time
from typing import Any

from api_client import BrainApiError, BrainClient
from config import TELEGRAM_CHAT_ID, settings
import database
from engine import AlphaEngine, FakeClient
from generator import generate_expressions
import notifier

log = logging.getLogger(__name__)


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
    """Run full end-to-end pipeline: Init DB -> generate N -> simulate -> send digest."""
    log.info("Initializing database...")
    database.init_db()

    log.info("Generating %d expressions...", args.n)
    pairs = generate_expressions(n=args.n)

    log.info("Starting simulations (mock=%s)...", args.mock)
    client = FakeClient() if args.mock else BrainClient()
    engine = AlphaEngine(client=client)
    results = engine.run_batch(pairs)

    passed_count = sum(1 for r in results if r.status == "passed")
    print(f"Simulation completed: {len(results)} simulated, {passed_count} passed filters.")

    log.info("Sending Telegram digest...")
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

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

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
