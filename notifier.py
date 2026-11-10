"""Telegram notification and interaction module for WorldQuant BRAIN pipeline.

Dispatches passing alpha candidate digests to Telegram and processes user
inline button interactions (Submit / Skip) via long-polling.
"""

import argparse
import json
import logging
from pathlib import Path
import time
from typing import Any
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, settings
import database

log = logging.getLogger(__name__)


def format_alpha_message(alpha: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Format an alpha candidate into display text and an inline keyboard markup.

    Args:
        alpha: Dictionary of alpha record containing alpha_id, expression, sharpe, etc.

    Returns:
        tuple[str, dict[str, Any]]: (formatted_message_text, reply_markup_dict)
    """
    alpha_id = str(alpha.get("alpha_id", ""))
    expression = str(alpha.get("expression", ""))
    sharpe = float(alpha.get("sharpe") or 0.0)
    fitness = float(alpha.get("fitness") or 0.0)
    turnover = float(alpha.get("turnover") or 0.0)

    text = (
        f"🎯 Alpha: {alpha_id}\n"
        f"{expression}\n"
        f"Sharpe: {sharpe:.2f} | Fitness: {fitness:.2f} | Turnover: {turnover:.2%}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Submit", "callback_data": f"submit:{alpha_id}"},
                {"text": "❌ Skip", "callback_data": f"skip:{alpha_id}"},
            ]
        ]
    }
    return text, reply_markup


def send_digest(limit: int | None = None, db_path: Path | str | None = None) -> list[int]:
    """Pull top passing alphas from database and send interactive cards to Telegram.

    Args:
        limit: Max number of passing alphas to send (defaults to settings.top_n).
        db_path: Optional SQLite database path.

    Returns:
        list[int]: List of sent Telegram message IDs.
    """
    count = limit if limit is not None else getattr(settings, "top_n", 4)
    candidates = database.list_passing(limit=count, db_path=db_path)

    if not candidates:
        print("No passing alphas to send.")
        return []

    message_ids: list[int] = []
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    for candidate in candidates:
        alpha_id = candidate.get("alpha_id")
        text, reply_markup = format_alpha_message(candidate)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "reply_markup": reply_markup,
        }

        try:
            resp = requests.post(f"{base_url}/sendMessage", json=payload, timeout=10)
            data: dict[str, Any] = {}
            try:
                data = resp.json()
            except Exception:
                pass

            if resp.status_code == 200 and data.get("ok"):
                msg_id = data["result"]["message_id"]
                message_ids.append(msg_id)
                database.mark_notified(str(alpha_id), db_path=db_path)
                log.info("Sent alpha %s to Telegram (message_id %s)", alpha_id, msg_id)
            else:
                desc = data.get("description", resp.text)
                log.error("Telegram API error sending alpha %s: %s", alpha_id, desc)
                print(f"Telegram API error: {desc}")
        except Exception as e:
            log.exception("HTTP error sending alpha %s: %s", alpha_id, e)
            print(f"HTTP error sending alpha {alpha_id}: {e}")

    return message_ids


def send_alert(message: str) -> None:
    """Send an urgent alert message to Telegram. Catches all exceptions quietly.

    Args:
        message: Alert description text.
    """
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"🚨 ALERT: {message}",
    }
    try:
        requests.post(f"{base_url}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        log.warning("Failed to dispatch Telegram alert: %s", e)


def poll_actions(timeout_seconds: int = 600, db_path: Path | str | None = None) -> None:
    """Poll for Telegram callback queries and process user button interactions.

    Args:
        timeout_seconds: Maximum seconds to listen before terminating loop (default 600).
        db_path: Optional SQLite database path.
    """
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    offset = 0
    start_time = time.monotonic()
    log.info("Starting Telegram poll_actions listener for %ds", timeout_seconds)

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed >= timeout_seconds:
            break

        remaining = timeout_seconds - elapsed
        poll_timeout = min(5, max(1, int(remaining)))

        try:
            resp = requests.get(
                f"{base_url}/getUpdates",
                params={"offset": offset, "timeout": poll_timeout},
                timeout=poll_timeout + 5,
            )
            data: dict[str, Any] = {}
            try:
                data = resp.json()
            except Exception:
                pass

            if not data.get("ok"):
                desc = data.get("description", resp.text)
                log.error("Telegram getUpdates error: %s", desc)
                time.sleep(1)
                continue

            updates = data.get("result", [])
            for update in updates:
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    cb = update["callback_query"]
                    cb_id = cb.get("id")
                    cb_data = str(cb.get("data", ""))
                    msg = cb.get("message", {})
                    msg_id = msg.get("message_id")
                    chat_id = msg.get("chat", {}).get("id") or TELEGRAM_CHAT_ID
                    original_text = msg.get("text", "")

                    if cb_data.startswith("submit:"):
                        alpha_id = cb_data.split(":", 1)[1]
                        database.mark_submitted(alpha_id, db_path=db_path)
                        log.info("User clicked Submit for alpha %s", alpha_id)

                        # Acknowledge user click
                        requests.post(
                            f"{base_url}/answerCallbackQuery",
                            json={"callback_query_id": cb_id, "text": "Marked for manual submission"},
                            timeout=10,
                        )

                        # Update card to show queued status and remove buttons
                        new_text = f"{original_text}\n\n✅ queued for manual submission"
                        requests.post(
                            f"{base_url}/editMessageText",
                            json={
                                "chat_id": chat_id,
                                "message_id": msg_id,
                                "text": new_text,
                                "reply_markup": {"inline_keyboard": []},
                            },
                            timeout=10,
                        )

                    elif cb_data.startswith("skip:"):
                        alpha_id = cb_data.split(":", 1)[1]
                        database.mark_notified(alpha_id, db_path=db_path)
                        log.info("User clicked Skip for alpha %s", alpha_id)

                        # Acknowledge user click
                        requests.post(
                            f"{base_url}/answerCallbackQuery",
                            json={"callback_query_id": cb_id, "text": "Skipped"},
                            timeout=10,
                        )

                        # Update card to show skipped status and remove buttons
                        new_text = f"{original_text}\n\n⏭ skipped"
                        requests.post(
                            f"{base_url}/editMessageText",
                            json={
                                "chat_id": chat_id,
                                "message_id": msg_id,
                                "text": new_text,
                                "reply_markup": {"inline_keyboard": []},
                            },
                            timeout=10,
                        )

        except Exception as e:
            log.exception("Error in poll_actions loop: %s", e)
            time.sleep(1)


def _main() -> None:
    parser = argparse.ArgumentParser(description="WorldQuant BRAIN Telegram Notifier")
    parser.add_argument("--dry-run", action="store_true", help="Build JSON payload for top candidate and print it")
    parser.add_argument("--test", action="store_true", help="Send a simple pipeline alive message to chat")
    parser.add_argument("--send", action="store_true", help="Send passing alphas digest to Telegram")
    parser.add_argument("--listen", action="store_true", help="Listen for button callbacks for 600 seconds")
    args = parser.parse_args()

    if args.dry_run:
        candidates = database.list_passing(limit=1)
        if not candidates:
            sample = {
                "alpha_id": "A123Sample",
                "expression": "rank(close)",
                "sharpe": 1.75,
                "fitness": 1.35,
                "turnover": 0.22,
            }
            text, reply_markup = format_alpha_message(sample)
        else:
            text, reply_markup = format_alpha_message(candidates[0])

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "reply_markup": reply_markup,
        }
        print(json.dumps(payload, indent=2))

    elif args.test:
        base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        resp = requests.post(
            f"{base_url}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": "🚀 Pipeline alive!"},
            timeout=10,
        )
        data: dict[str, Any] = {}
        try:
            data = resp.json()
        except Exception:
            pass

        if resp.status_code == 200 and data.get("ok"):
            print("OK")
        else:
            desc = data.get("description", resp.text)
            print(f"Telegram error: {desc}")

    elif args.send:
        sent = send_digest()
        print(f"Sent {len(sent)} alphas.")

    elif args.listen:
        poll_actions(600)

    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
