"""HTTP client for WorldQuant BRAIN API.

This module is the ONLY component in the pipeline that performs HTTP communication
with the WorldQuant BRAIN platform. All other modules interface through
`BrainClient.request()` and never invoke `requests` directly.

Verified BRAIN API endpoints:
1. POST /authentication
   Authenticates using HTTP Basic Auth (email/password). Returns HTTP 201 with a
   session token (key 'token' and session cookie 't') valid for 14400 seconds (4 hours).
2. POST /simulations
   Submits alpha simulation payloads for processing. Returns HTTP 201 with a
   'Location' header for polling simulation progress.
3. GET /alphas/{id}
   Retrieves simulation results, in-sample performance metrics (Sharpe, turnover,
   fitness, margin, etc.), and validation checks.
"""

from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any
import requests

from config import (
    BRAIN_BASE_URL,
    BRAIN_EMAIL,
    BRAIN_PASSWORD,
    TOKEN_REFRESH_MARGIN_SECONDS,
    TOKEN_TTL_SECONDS,
)

log = logging.getLogger(__name__)


class BrainApiError(Exception):
    """Raised when an API request returns an unexpected non-2xx status code."""

    def __init__(self, status: int, body: str) -> None:
        """Initialize BrainApiError.

        Args:
            status: HTTP response status code.
            body: HTTP response body snippet.
        """
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status: int = status
        self.body: str = body

    def __str__(self) -> str:
        """Format error message with status code and truncated body snippet."""
        return f"HTTP {self.status}: {self.body[:200]}"


class BrainClient:
    """Thread-safe HTTP client managing authentication and requests to WorldQuant BRAIN API."""

    def __init__(self) -> None:
        """Initialize requests session, token cache, and concurrency lock."""
        self._session: requests.Session = requests.Session()
        self._token: str | None = None
        self._expiry: datetime | None = None  # UTC, timezone-aware
        self._lock: threading.Lock = threading.Lock()

    def _authenticate(self) -> None:
        """Execute authentication request and update cached session token.

        Uses HTTP Basic Auth with BRAIN_EMAIL and BRAIN_PASSWORD against the
        /authentication endpoint. Updates self._token and self._expiry on 2xx.

        Raises:
            BrainApiError: If the server returns a non-2xx HTTP status code.
        """
        url = f"{BRAIN_BASE_URL.rstrip('/')}/authentication"
        resp = self._session.post(url, auth=(BRAIN_EMAIL, BRAIN_PASSWORD))

        if not (200 <= resp.status_code < 300):
            raise BrainApiError(resp.status_code, resp.text)

        try:
            data = resp.json()
        except Exception:
            data = {}

        token_data = data.get("token")
        if isinstance(token_data, str):
            self._token = token_data
        elif self._session.cookies.get("t"):
            self._token = self._session.cookies.get("t")
        elif resp.cookies.get("t"):
            self._token = resp.cookies.get("t")
        elif isinstance(token_data, dict) and "id" in token_data:
            self._token = str(token_data["id"])
        else:
            self._token = str(token_data) if token_data is not None else None

        now_utc = datetime.now(timezone.utc)
        self._expiry = now_utc + timedelta(seconds=TOKEN_TTL_SECONDS)

    def _ensure_token(self) -> None:
        """Ensure a valid, non-expired authentication token is available.

        Performs double-checked locking: verifies token freshness outside the lock,
        and if absent or approaching expiry, acquires the lock and calls _authenticate().
        """
        if self.is_token_valid:
            return

        with self._lock:
            if not self.is_token_valid:
                self._authenticate()

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Send an authenticated HTTP request to the BRAIN API with automatic 401 retry.

        Args:
            method: HTTP method string (e.g. 'GET', 'POST').
            path: Relative API path (e.g. '/alphas/X') or absolute URL.
            **kwargs: Additional parameters forwarded to `requests.Session.request`.

        Returns:
            requests.Response: Successful 2xx HTTP response object.

        Raises:
            BrainApiError: On repeated 401 Unauthorized or any non-2xx response.
        """
        self._ensure_token()

        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{BRAIN_BASE_URL.rstrip('/')}/{path.lstrip('/')}"

        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {self._token}"

        resp = self._session.request(method, url, headers=headers, **kwargs)

        if resp.status_code == 401:
            log.warning("Received 401 Unauthorized for %s %s; attempting single re-auth and retry", method, url)
            # A race condition can occur where the cached token expires on the server
            # slightly before our local TTL calculation or is invalidated remotely.
            # We acquire the lock, invalidate the stale token, re-authenticate to obtain
            # a fresh token, and retry the request exactly once.
            with self._lock:
                self._token = None
                self._expiry = None
                self._authenticate()

            headers["Authorization"] = f"Bearer {self._token}"
            resp = self._session.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401:
                raise BrainApiError(401, resp.text)

        if not (200 <= resp.status_code < 300):
            raise BrainApiError(resp.status_code, resp.text)

        return resp

    def invalidate_token(self) -> None:
        """Thread-safely clear cached token and expiry timestamp."""
        with self._lock:
            self._token = None
            self._expiry = None

    def close(self) -> None:
        """Close the underlying requests Session."""
        self._session.close()

    @property
    def token(self) -> str | None:
        """Read-only access to cached token for debugging and testing."""
        return self._token

    @property
    def is_token_valid(self) -> bool:
        """Return True if token exists and is within the validity window before margin."""
        if self._token is None or self._expiry is None:
            return False
        now_utc = datetime.now(timezone.utc)
        margin = timedelta(seconds=TOKEN_REFRESH_MARGIN_SECONDS)
        return now_utc < (self._expiry - margin)

    def __enter__(self) -> "BrainClient":
        """Enter context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager, closing the underlying session."""
        self.close()
