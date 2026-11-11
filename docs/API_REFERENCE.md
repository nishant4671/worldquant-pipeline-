# API & Interface Reference

This document provides a technical specification of the WorldQuant BRAIN REST API endpoints used by this system, followed by the exact Python class interfaces and function signatures for the internal pipeline modules.

---

## 1. WorldQuant BRAIN REST API Reference

All requests to the WorldQuant BRAIN platform target `https://api.worldquantbrain.com` and must route through `BrainClient.request()` in `api_client.py`.

### 1.1 Authentication (`POST /authentication`)
Obtains a session authentication token and cookie valid for 14,400 seconds (4 hours).

- **Endpoint**: `POST https://api.worldquantbrain.com/authentication`
- **Headers**:
  - `Authorization`: `Basic <base64(email:password)>`
- **Request Body**: None (credentials passed via HTTP Basic Authentication)
- **Response**: `HTTP 201 Created`
  ```json
  {
    "token": {
      "id": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "expiry": 14400
    },
    "user": {
      "id": "USR-12345",
      "email": "user@example.com"
    }
  }
  ```
- **Cookies**: Sets session cookie `t` containing the session token.

---

### 1.2 Submit Simulation (`POST /simulations`)
Submits a single Fast Expression alpha for asynchronous backtesting across the USA equity universe.

- **Endpoint**: `POST https://api.worldquantbrain.com/simulations`
- **Headers**:
  - `Authorization`: `Bearer <token>`
  - `Content-Type`: `application/json`
- **Request Body**:
  ```json
  {
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
      "visualization": false,
      "testPeriod": "P0Y0M"
    },
    "regular": "ts_decay_linear(rank(close), 5)"
  }
  ```
- **Response**: `HTTP 201 Created`
  - **Headers**:
    - `Location`: `https://api.worldquantbrain.com/simulations/sim-01hxyz987`
  - **Response Body**:
    ```json
    {
      "id": "sim-01hxyz987"
    }
    ```

---

### 1.3 Poll Simulation Progress (`GET /simulations/{id}`)
Polls the execution state of a submitted simulation job.

- **Endpoint**: `GET https://api.worldquantbrain.com/simulations/{id}` (or exact URL provided in the `Location` header)
- **Headers**:
  - `Authorization`: `Bearer <token>`
- **Response States**:
  - **In Progress**: `HTTP 200 OK`
    ```json
    {
      "status": "RUNNING",
      "progress": 0.45
    }
    ```
  - **Completed**: `HTTP 200 OK`
    ```json
    {
      "status": "COMPLETE",
      "progress": 1.0,
      "alpha": "A123BC456"
    }
    ```
  - **Failed**: `HTTP 200 OK`
    ```json
    {
      "status": "ERROR",
      "progress": 0.0,
      "message": "Syntax error in expression: unknown operator"
    }
    ```

---

### 1.4 Retrieve Alpha Performance Metrics (`GET /alphas/{id}`)
Retrieves comprehensive in-sample (IS) performance metrics and check evaluations for a completed alpha.

- **Endpoint**: `GET https://api.worldquantbrain.com/alphas/{alpha_id}`
- **Headers**:
  - `Authorization`: `Bearer <token>`
- **Response**: `HTTP 200 OK`
  ```json
  {
    "id": "A123BC456",
    "type": "REGULAR",
    "regular": "ts_decay_linear(rank(close), 5)",
    "dateCreated": "2026-11-11T12:00:00Z",
    "is": {
      "sharpe": 1.58,
      "fitness": 1.34,
      "turnover": 0.18,
      "returns": 0.14,
      "drawdown": 0.06,
      "margin": 0.0018,
      "longCount": 1450,
      "shortCount": 1420,
      "startDate": "2019-01-01",
      "endDate": "2024-01-01"
    },
    "checks": [
      {"name": "LOW_SHARPE", "result": "PASS"},
      {"name": "HIGH_TURNOVER", "result": "PASS"},
      {"name": "SELF_CORRELATION", "result": "PASS"}
    ]
  }
  ```

---

## 2. Python Module Interfaces & Function Signatures

### 2.1 `engine.py` (Simulation Engine & Circuit Breaker)

```python
class BreakerState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 300) -> None:
        """Initialize circuit breaker with threshold count and cooldown interval."""
        ...

    def record_failure(self, status: int = 429) -> None:
        """Record failure event; trips to OPEN when failure_threshold is reached."""
        ...

    def record_success(self) -> None:
        """Reset consecutive failures to 0 and restore state to CLOSED."""
        ...

    def check_state(self) -> None:
        """Verify state before dispatching requests.
        
        Raises:
            BrainApiError: If state is OPEN and cooldown has not elapsed.
        """
        ...

@dataclass
class SimulationResult:
    expression: str = ""
    category: str = ""
    alpha_id: str | None = None
    status: str = "error"  # 'passed' | 'failed_filters' | 'skipped' | 'error'
    metrics: dict[str, Any] | None = None
    error: str | None = None

class AlphaEngine:
    def __init__(
        self,
        client: BrainClient,
        max_workers: int = 3,
        poll_interval: float | None = None,
        poll_timeout: float | None = None,
        db_path: Path | str | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        """Initialize concurrent engine with API client, thread limit, and storage path."""
        ...

    def _simulate_one(self, expr: str, category: str) -> SimulationResult:
        """Submit single simulation, poll for completion, evaluate gates, and persist.
        
        Args:
            expr: Fast Expression string.
            category: Category name (e.g. 'price_volume', 'fundamental').
            
        Returns:
            SimulationResult with evaluated metrics and status.
        """
        ...

    def run_batch(self, expressions: list[tuple[str, str]]) -> list[SimulationResult]:
        """Execute a batch of (expression, category) tuples concurrently across worker threads.
        
        Args:
            expressions: List of (expression, category) pairs.
            
        Returns:
            list[SimulationResult]: Completed simulation results.
        """
        ...
```

---

### 2.2 `database.py` (Persistence, Deduplication & Migration)

```python
def hash_expression(expr: str) -> str:
    """Compute deterministic SHA-256 hash of normalized (lowercase, whitespace-stripped) expression.
    
    Args:
        expr: Raw Fast Expression string.
        
    Returns:
        str: 64-character hexadecimal SHA-256 digest.
    """
    ...

def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite database connection configured with sqlite3.Row factory.
    
    Args:
        db_path: Path to database file. Defaults to settings.db_path.
    """
    ...

def init_db(db_path: Path | str | None = None, force: bool = False) -> None:
    """Initialize schema, creating 'alphas' table and indexes. Handles automatic column migration.
    
    Args:
        db_path: Optional path to SQLite file.
        force: If True, re-executes DDL even if previously initialized in memory.
    """
    ...

def insert_alpha(record: dict[str, Any], db_path: Path | str | None = None) -> None:
    """Insert or replace an alpha record based on alpha_id. Computes expression_hash automatically.
    
    Args:
        record: Dictionary with alpha_id, expression, metrics, etc.
        db_path: Optional path to SQLite file.
    """
    ...

def expression_exists(expr: str, db_path: Path | str | None = None) -> bool:
    """Check if normalized expression hash already exists in the database.
    
    Args:
        expr: Raw Fast Expression string.
        
    Returns:
        bool: True if already recorded, False otherwise.
    """
    ...

def get_alpha(alpha_id: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Retrieve single alpha record by alpha_id.
    
    Returns:
        Dictionary representation of row, or None if not found.
    """
    ...

def list_passing(limit: int = 4, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return top unnotified passing alphas sorted by Sharpe ratio descending.
    
    Args:
        limit: Max records to return (default 4).
    """
    ...

def mark_notified(alpha_id: str, db_path: Path | str | None = None) -> None:
    """Update record setting notified = 1 for the given alpha_id."""
    ...

def mark_submitted(alpha_id: str, db_path: Path | str | None = None) -> None:
    """Update record setting submitted = 1 for the given alpha_id."""
    ...

def summary(db_path: Path | str | None = None) -> dict[str, int]:
    """Return aggregate counts: total, passed, notified, and submitted alphas.
    
    Returns:
        dict: {"total": int, "passed": int, "notified": int, "submitted": int}
    """
    ...
```

---

### 2.3 `llm_client.py` (Multi-Agent Gemini SDK Client)

```python
DEFAULT_OPERATORS: list[str] = [
    "rank", "ts_rank", "ts_delta", "ts_mean", "ts_zscore",
    "ts_decay_linear", "group_neutralize", "trade_when"
]

def _strip_markdown_code_block(text: str) -> str:
    """Strip markdown code fences (```json ... ```) and backticks from response text."""
    ...

def _is_rate_limit_error(exc: Exception) -> bool:
    """Identify if exception corresponds to HTTP 429 or quota exhaustion."""
    ...

class LLMClient:
    def __init__(self, api_keys: list[str] | None = None) -> None:
        """Initialize client with Gemini API keys. Defaults to config.GEMINI_API_KEYS.
        
        Raises:
            RuntimeError: If no API keys are provided or configured.
        """
        ...

    def _init_client(self) -> None:
        """Instantiate google.genai.Client for the active key index."""
        ...

    def _rotate_key(self) -> None:
        """Cycle to next API key upon 429 quota exhaustion and reinitialize genai.Client."""
        ...

    def generate_alphas(
        self,
        known_fields: dict[str, list[str]],
        operators: list[str] = DEFAULT_OPERATORS,
        num_alphas: int = 5,
    ) -> list[str]:
        """Propose Fast Expression alpha candidates constrained to verified fields and operators.
        
        Args:
            known_fields: Dictionary of verified fields by category.
            operators: Allowed operators list.
            num_alphas: Target count of expressions (default 5).
            
        Returns:
            list[str]: Proposed alpha expression strings.
        """
        ...

    def critique_alphas(self, expressions: list[str]) -> list[str]:
        """Filter candidate expressions through an adversarial Portfolio Manager persona.
        
        Args:
            expressions: Candidate expressions to evaluate.
            
        Returns:
            list[str]: Approved expressions. Fails open (returns input) on parse error.
        """
        ...
```

---

### 2.4 `api_client.py` (BRAIN Transport)

```python
class BrainApiError(Exception):
    status: int
    body: str
    def __init__(self, status: int, body: str) -> None:
        ...

class BrainClient:
    def __init__(self) -> None:
        """Initialize requests session, token cache, and threading lock."""
        ...

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Send authenticated HTTP request with automatic re-auth, retry, and backoff.
        
        Args:
            method: 'GET' | 'POST' | 'PUT' | 'DELETE'
            path: Relative path ('/simulations') or absolute URL.
            **kwargs: Extra parameters passed to session.request.
            
        Returns:
            requests.Response: Successful 2xx response.
            
        Raises:
            BrainApiError: On non-2xx status, unrecovered 429, or network dropouts.
        """
        ...

    def invalidate_token(self) -> None:
        """Thread-safely clear cached token and expiry timestamp."""
        ...

    def close(self) -> None:
        """Close the underlying requests Session."""
        ...

    @property
    def token(self) -> str | None:
        """Read-only access to cached token."""
        ...

    @property
    def is_token_valid(self) -> bool:
        """Return True if token exists and is prior to expiry refresh margin."""
        ...
```

---

### 2.5 `validator.py` (Static Gatekeeper Linter)

```python
def validate_expression(expr: str) -> tuple[bool, str]:
    """Statically lint and validate a single Fast Expression string.
    
    Performs parentheses balancing, character whitelist validation, Python keyword
    injection detection, hallucination checks against KNOWN_FIELDS, and operator arity checks.
    
    Args:
        expr: Raw Fast Expression string.
        
    Returns:
        tuple[bool, str]: (True, "") if valid, or (False, reason) if rejected.
    """
    ...

def validate_batch(expressions: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Validate a batch of expression strings.
    
    Returns:
        tuple[list[str], list[tuple[str, str]]]: (valid_expressions, rejected_expressions_with_reasons)
    """
    ...
```

---

### 2.6 `notifier.py` (Telegram Alerting & Interaction)

```python
def format_alpha_message(alpha: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Format an alpha dictionary into Telegram markdown text and inline keyboard markup."""
    ...

def send_digest(limit: int | None = None, db_path: Path | str | None = None) -> list[int]:
    """Fetch top unnotified passing alphas from SQLite and dispatch interactive cards to Telegram.
    
    Returns:
        list[int]: Sent Telegram message IDs.
    """
    ...

def send_alert(message: str) -> None:
    """Send an urgent high-priority alert message to Telegram chat."""
    ...

def poll_actions(timeout_seconds: int = 600, db_path: Path | str | None = None) -> None:
    """Run long-polling listener processing Submit and Skip callback queries from Telegram."""
    ...
```
