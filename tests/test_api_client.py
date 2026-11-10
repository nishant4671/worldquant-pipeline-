"""Tests for api_client module."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import pytest
import requests

from api_client import BrainApiError, BrainClient
from config import BRAIN_EMAIL, BRAIN_PASSWORD, TOKEN_REFRESH_MARGIN_SECONDS


def test_auth_sets_token(mocker):
    """Test successful authentication sets token and marks token valid."""
    client = BrainClient()
    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "fake-token-12345678"}
    mocker.patch.object(client._session, "post", return_value=mock_resp)

    client._authenticate()

    assert client.token == "fake-token-12345678"
    assert client.is_token_valid is True
    client.close()


def test_auth_failure_raises(mocker):
    """Test non-2xx authentication raises BrainApiError."""
    client = BrainClient()
    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 401
    mock_resp.text = '{"detail": "Invalid credentials"}'
    mocker.patch.object(client._session, "post", return_value=mock_resp)

    with pytest.raises(BrainApiError) as exc_info:
        client._authenticate()

    assert exc_info.value.status == 401
    assert "Invalid credentials" in exc_info.value.body
    client.close()


def test_token_cached_no_repeat_auth(mocker):
    """Test cached token prevents repeated authentication calls."""
    client = BrainClient()
    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "t1"}
    mock_req = mocker.patch.object(client._session, "request", return_value=mock_resp)

    client._ensure_token()
    client._ensure_token()

    assert mock_req.call_count == 1
    assert client.token == "t1"
    client.close()


def test_token_expiry_triggers_reauth(mocker):
    """Test token expiration forces re-authentication."""
    client = BrainClient()
    resp1 = mocker.MagicMock(spec=requests.Response)
    resp1.status_code = 200
    resp1.json.return_value = {"token": "t1"}

    resp2 = mocker.MagicMock(spec=requests.Response)
    resp2.status_code = 200
    resp2.json.return_value = {"token": "t2"}

    mock_req = mocker.patch.object(client._session, "request", side_effect=[resp1, resp2])

    client._ensure_token()
    assert client.token == "t1"

    now_utc = datetime.now(timezone.utc)
    client._expiry = now_utc - timedelta(seconds=1)

    client._ensure_token()
    assert client.token == "t2"
    assert mock_req.call_count == 2
    client.close()


def test_token_refresh_margin(mocker):
    """Test proactive refresh when inside safety margin before expiration."""
    client = BrainClient()
    resp1 = mocker.MagicMock(spec=requests.Response)
    resp1.status_code = 200
    resp1.json.return_value = {"token": "token-initial"}

    resp2 = mocker.MagicMock(spec=requests.Response)
    resp2.status_code = 200
    resp2.json.return_value = {"token": "token-refreshed"}

    mock_req = mocker.patch.object(client._session, "request", side_effect=[resp1, resp2])

    client._ensure_token()
    assert client.token == "token-initial"

    now_utc = datetime.now(timezone.utc)
    client._expiry = now_utc + timedelta(seconds=TOKEN_REFRESH_MARGIN_SECONDS - 60)

    client._ensure_token()
    assert client.token == "token-refreshed"
    assert mock_req.call_count == 2
    client.close()


def test_request_injects_bearer_header(mocker):
    """Test request method injects Authorization Bearer header."""
    client = BrainClient()
    client._token = "abc123"
    client._expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_req = mocker.patch.object(client._session, "request", return_value=mock_resp)

    client.request("GET", "/alphas/X")

    mock_req.assert_called_once()
    kwargs = mock_req.call_args[1]
    assert kwargs["headers"]["Authorization"] == "Bearer abc123"
    client.close()


def test_401_triggers_single_reauth_and_retry(mocker):
    """Test single 401 triggers re-authentication and succeeds on retry with new token."""
    client = BrainClient()
    client._token = "old-token"
    client._expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    resp_401 = mocker.MagicMock(spec=requests.Response)
    resp_401.status_code = 401
    resp_401.text = "Unauthorized"

    resp_200 = mocker.MagicMock(spec=requests.Response)
    resp_200.status_code = 200
    resp_200.text = "OK"

    mock_req = mocker.patch.object(client._session, "request", side_effect=[resp_401, resp_200])

    def mock_auth():
        client._token = "new-fresh-token"
        client._expiry = datetime.now(timezone.utc) + timedelta(hours=4)

    mocker.patch.object(client, "_authenticate", side_effect=mock_auth)

    resp = client.request("GET", "/alphas/X")

    assert resp.status_code == 200
    assert mock_req.call_count == 2
    second_call_kwargs = mock_req.call_args_list[1][1]
    assert second_call_kwargs["headers"]["Authorization"] == "Bearer new-fresh-token"
    client.close()


def test_double_401_raises(mocker):
    """Test two consecutive 401s raise BrainApiError without looping infinitely."""
    client = BrainClient()
    client._token = "initial-token"
    client._expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    mock_req = mocker.patch.object(client._session, "request", return_value=mock_resp)

    with pytest.raises(BrainApiError) as exc_info:
        client.request("GET", "/alphas/test")

    assert exc_info.value.status == 401
    assert mock_req.call_count == 2
    client.close()


def test_non_2xx_raises(mocker):
    """Test non-2xx responses (e.g. 500) raise BrainApiError."""
    client = BrainClient()
    client._token = "valid-token"
    client._expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mocker.patch.object(client._session, "request", return_value=mock_resp)

    with pytest.raises(BrainApiError) as exc_info:
        client.request("GET", "/status")

    assert exc_info.value.status == 500
    assert "Internal Server Error" in exc_info.value.body
    client.close()


def test_concurrent_ensure_token_single_auth(mocker):
    """Test concurrent worker threads perform only a single authentication call."""
    client = BrainClient()
    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "threaded-token"}
    mock_req = mocker.patch.object(client._session, "request", return_value=mock_resp)

    barrier = threading.Barrier(5)

    def worker():
        barrier.wait()
        client._ensure_token()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert mock_req.call_count == 1
    assert client.token == "threaded-token"
    client.close()


def test_absolute_url_passthrough(mocker):
    """Test absolute URL is passed through without re-prefixing."""
    client = BrainClient()
    client._token = "valid-token"
    client._expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = mocker.MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_req = mocker.patch.object(client._session, "request", return_value=mock_resp)

    abs_url = "https://api.worldquantbrain.com/simulations/abc"
    client.request("GET", abs_url)

    mock_req.assert_called_once()
    called_url = mock_req.call_args[0][1] if len(mock_req.call_args[0]) > 1 else mock_req.call_args[1].get("url")
    assert called_url == abs_url
    client.close()


@pytest.mark.integration
def test_real_auth_roundtrip():
    """Test real roundtrip authentication and authenticated GET request against live API."""
    if not BRAIN_EMAIL:
        pytest.skip("no creds")
    client = BrainClient()
    try:
        client._authenticate()
        assert client.token is not None
        assert len(client.token) > 50
        resp = client.request("GET", "/authentication")
        assert 200 <= resp.status_code < 300
    finally:
        client.close()


def test_no_hardcoded_credentials_in_api_client():
    """Test api_client.py contains no hardcoded credentials."""
    api_client_path = Path(__file__).resolve().parent.parent / "api_client.py"
    content = api_client_path.read_text(encoding="utf-8")
    assert BRAIN_EMAIL not in content
    assert BRAIN_PASSWORD not in content


def test_no_print_statements():
    """Test api_client.py contains no print() calls."""
    api_client_path = Path(__file__).resolve().parent.parent / "api_client.py"
    content = api_client_path.read_text(encoding="utf-8")
    assert "print(" not in content
