"""Unit tests for notifier module."""

import json
import pytest
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
import database
import notifier


def test_dry_run_payload(mocker):
    """Test candidate payload and inline keyboard structure."""
    candidate = {
        "alpha_id": "alpha_101",
        "expression": "group_rank(close, sector)",
        "sharpe": 1.85,
        "fitness": 1.45,
        "turnover": 0.25,
    }
    mocker.patch.object(database, "list_passing", return_value=[candidate])

    text, reply_markup = notifier.format_alpha_message(candidate)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "reply_markup": reply_markup,
    }

    assert "alpha_101" in payload["text"]
    assert "1.85" in payload["text"]
    assert "1.45" in payload["text"]
    assert "25.00%" in payload["text"]

    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert len(buttons) == 2
    assert buttons[0]["text"] == "✅ Submit"
    assert buttons[0]["callback_data"] == "submit:alpha_101"
    assert buttons[1]["text"] == "❌ Skip"
    assert buttons[1]["callback_data"] == "skip:alpha_101"


def test_send_digest_marks_notified(mocker):
    """Test send_digest sends message and marks alpha as notified."""
    candidate = {
        "alpha_id": "alpha_202",
        "expression": "rank(open)",
        "sharpe": 1.60,
        "fitness": 1.20,
        "turnover": 0.15,
    }
    mocker.patch.object(database, "list_passing", return_value=[candidate])
    mock_mark = mocker.patch.object(database, "mark_notified")

    fake_resp = mocker.MagicMock(spec=requests.Response)
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "application/json"}
    fake_resp.json.return_value = {"ok": True, "result": {"message_id": 777}}
    mock_post = mocker.patch("requests.post", return_value=fake_resp)

    sent_ids = notifier.send_digest(limit=1)

    assert sent_ids == [777]
    mock_mark.assert_called_once_with("alpha_202", db_path=None)
    mock_post.assert_called_once()
    assert "/sendMessage" in mock_post.call_args[0][0]


def test_send_digest_empty(mocker):
    """Test send_digest returns [] and makes 0 HTTP calls when no passing alphas exist."""
    mocker.patch.object(database, "list_passing", return_value=[])
    mock_post = mocker.patch("requests.post")

    sent_ids = notifier.send_digest()

    assert sent_ids == []
    mock_post.assert_not_called()


def test_poll_submit_action(mocker):
    """Test poll_actions processes submit callback query and calls database.mark_submitted."""
    mock_mark_submitted = mocker.patch.object(database, "mark_submitted")

    fake_updates_resp = mocker.MagicMock(spec=requests.Response)
    fake_updates_resp.status_code = 200
    fake_updates_resp.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb_sub_1",
                    "data": "submit:123",
                    "message": {
                        "message_id": 888,
                        "text": "🎯 Alpha: 123",
                        "chat": {"id": 12345},
                    },
                },
            }
        ],
    }
    fake_empty_resp = mocker.MagicMock(spec=requests.Response)
    fake_empty_resp.status_code = 200
    fake_empty_resp.json.return_value = {"ok": True, "result": []}

    def fake_get(*args, **kwargs):
        offset = kwargs.get("params", {}).get("offset", 0)
        return fake_updates_resp if offset == 0 else fake_empty_resp

    mock_get = mocker.patch("requests.get", side_effect=fake_get)
    mock_post = mocker.patch("requests.post")
    mocker.patch("time.monotonic", side_effect=[0.0, 0.0, 1.0])

    notifier.poll_actions(timeout_seconds=0.01)

    mock_mark_submitted.assert_called_once_with("123", db_path=None)
    assert mock_post.call_count == 2
    called_endpoints = [call[0][0] for call in mock_post.call_args_list]
    assert any("answerCallbackQuery" in ep for ep in called_endpoints)
    assert any("editMessageText" in ep for ep in called_endpoints)


def test_poll_skip_action(mocker):
    """Test poll_actions processes skip callback query and calls database.mark_notified."""
    mock_mark_notified = mocker.patch.object(database, "mark_notified")

    fake_updates_resp = mocker.MagicMock(spec=requests.Response)
    fake_updates_resp.status_code = 200
    fake_updates_resp.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 2,
                "callback_query": {
                    "id": "cb_skip_1",
                    "data": "skip:456",
                    "message": {
                        "message_id": 999,
                        "text": "🎯 Alpha: 456",
                        "chat": {"id": 12345},
                    },
                },
            }
        ],
    }
    fake_empty_resp = mocker.MagicMock(spec=requests.Response)
    fake_empty_resp.status_code = 200
    fake_empty_resp.json.return_value = {"ok": True, "result": []}

    def fake_get(*args, **kwargs):
        offset = kwargs.get("params", {}).get("offset", 0)
        return fake_updates_resp if offset == 0 else fake_empty_resp

    mock_get = mocker.patch("requests.get", side_effect=fake_get)
    mock_post = mocker.patch("requests.post")
    mocker.patch("time.monotonic", side_effect=[0.0, 0.0, 1.0])

    notifier.poll_actions(timeout_seconds=0.01)

    mock_mark_notified.assert_called_once_with("456", db_path=None)
    assert mock_post.call_count == 2
    called_endpoints = [call[0][0] for call in mock_post.call_args_list]
    assert any("answerCallbackQuery" in ep for ep in called_endpoints)
    assert any("editMessageText" in ep for ep in called_endpoints)


def test_send_system_alert(mocker):
    """Test send_system_alert dispatches high priority message with 🚨 emoji."""
    fake_resp = mocker.MagicMock(spec=requests.Response)
    fake_resp.status_code = 200
    mock_post = mocker.patch("requests.post", return_value=fake_resp)

    success = notifier.send_system_alert("Gemini API is down.")

    assert success is True
    mock_post.assert_called_once()
    called_url = mock_post.call_args[0][0]
    called_payload = mock_post.call_args[1]["json"]
    assert f"bot{TELEGRAM_BOT_TOKEN}" in called_url
    assert called_payload["chat_id"] == TELEGRAM_CHAT_ID
    assert "🚨" in called_payload["text"]
    assert "Gemini API is down." in called_payload["text"]

