# Testing & Quality Assurance Strategy

The **WorldQuant BRAIN Autonomous Alpha Pipeline** relies on a strict testing methodology designed to guarantee deterministic behavior across network failures, platform rate limits, generative AI hallucinations, and SQLite database concurrency.

The test suite contains **85+ passing unit tests** that execute in under 10 seconds without making any live network calls or consuming API tokens.

---

## 1. Testing Philosophy

1. **Complete Network Isolation**: Unit tests must never contact `api.worldquantbrain.com` or Google Gemini APIs. All external network interactions are simulated via `pytest-mock`.
2. **Deterministic Reproducibility**: Randomness in alpha generation templates, lookback windows, and network timeouts is seeded or mocked.
3. **Fail-Safe Gate Verification**: Tests explicitly verify that hallucinated expressions, Python keyword injections, and malformed JSON payloads are blocked before reaching simulation execution.
4. **Explicit Marker Segregation**: Live tests that touch real endpoints are marked with `@pytest.mark.integration` and excluded from default CI/CD test runs.

---

## 2. Mocking Strategy with `pytest-mock`

Unit tests make extensive use of the `mocker` fixture provided by `pytest-mock` to simulate edge cases and failure modes.

### 2.1 Simulating WorldQuant BRAIN API Failure Modes
In `tests/test_api_client.py` and `tests/test_engine.py`:

- **Token Expiry & 401 Re-Authentication**:
  Simulates a `401 Unauthorized` response on a cached token. Asserts that `BrainClient` clears its cached token, triggers `_authenticate()`, updates authorization headers, and retries the request seamlessly.
- **HTTP 429 Rate Limiting & `Retry-After`**:
  Simulates HTTP 429 responses with and without `Retry-After` headers. Verifies that `api_client` extracts the wait duration, pauses execution (`time.sleep` mocked), and retries once.
- **Circuit Breaker Kill-Switch Tripping**:
  Mocks three consecutive HTTP 429 simulation responses. Verifies that `CircuitBreaker` transitions from `CLOSED` $\rightarrow$ `OPEN`, blocks subsequent calls without network overhead, and dispatches a critical Telegram alert.
- **Network Drops & Transient Server Errors**:
  Mocks `requests.exceptions.RequestException` to verify single-retry recovery after 5 seconds. Mocks HTTP 500 / 502 responses during simulation polling to verify that the polling loop survives temporary backend hiccups.

### 2.2 Simulating LLM Hallucinations & Malformed Outputs
In `tests/test_llm_client.py` and `tests/test_validator.py`:

- **Markdown Fences & Backtick Stripping**:
  Mocks LLM responses wrapped in ```json\n["rank(close)"]\n```. Asserts that `_strip_markdown_code_block` strips fences and produces clean JSON.
- **Malformed & Non-Array JSON**:
  Mocks unparseable responses (e.g., `"Here are your alphas: rank(close)"`). Tests that the client logs a warning, retries generation once, and gracefully returns `[]` without raising unhandled exceptions.
- **Dynamic Multi-Key Failover (Rotation on 429)**:
  Patches `config.GEMINI_API_KEYS` with multiple keys (`["key1", "key2"]`). Configures `models.Models.generate_content` to raise `google.genai.errors.APIError(code=429)` on the first attempt and succeed on the second. Asserts that `current_key_index` increments from `0` to `1` and the second key is utilized.
- **PM Critic Fail-Open Guarantee**:
  Mocks unparseable critique responses. Verifies that `critique_alphas` logs a warning and returns the original input list intact (failing open) rather than discarding valid hypotheses.

### 2.3 Simulating AST Linting & Injection Prevention
In `tests/test_validator.py`:

- **Hallucinated Field Detection**:
  Validates that unverified fields (e.g., `net_income`, `revenue`, `adv20`) are immediately rejected by `validate_expression` with reason `Hallucinated field/operator`.
- **Python Keyword Injection Detection**:
  Tests that code injection vectors (e.g., `import os`, `class`, `lambda`, `eval`) are blocked with reason `Invalid keyword`.
- **Operator Arity Constraints**:
  Tests that multi-argument operators like `group_rank` are rejected if they lack the required comma-separated arguments (e.g., `group_rank(close)` is rejected, while `group_rank(close, sector)` passes).

---

## 3. Test Suite Inventory

| Test Module | Coverage Scope | Key Tests |
|---|---|---|
| `tests/test_api_client.py` | `BrainClient`, HTTP transport, authentication, tokens, retry backoff | `test_auth_sets_token`, `test_auth_failure_raises`, `test_token_cached_no_repeat_auth`, `test_401_triggers_reauth_and_retry`, `test_429_sleeps_and_retries`, `test_network_error_retry` |
| `tests/test_engine.py` | `AlphaEngine`, `CircuitBreaker`, simulation state polling, metric gates | `test_breaker_trips_after_threshold_429s`, `test_breaker_half_open_recovery`, `test_simulate_one_success_passed`, `test_simulate_one_failed_filters`, `test_simulate_one_duplicate_skipped`, `test_concurrent_batch` |
| `tests/test_validator.py` | `validator.py`, AST parsing, whitelist checks, arity | `test_valid_expressions_pass`, `test_unbalanced_parentheses_fail`, `test_invalid_characters_fail`, `test_hallucinated_fields_fail`, `test_python_keywords_fail`, `test_group_rank_arity` |
| `tests/test_llm_client.py` | `LLMClient`, multi-key rotation, JSON sanitization, PM critique | `test_key_rotation_on_429_multi_key`, `test_single_key_retry_on_429`, `test_json_parsing`, `test_invalid_json_handling`, `test_critic_approves`, `test_critic_fails_open` |
| `tests/test_database.py` | `database.py`, SQLite DDL, migrations, SHA-256 deduplication | `test_init_db_creates_tables`, `test_hash_expression_deterministic`, `test_expression_exists_detects_duplicates`, `test_insert_and_get_alpha`, `test_list_passing_ordering`, `test_mark_submitted` |
| `tests/test_generator.py` | `generator.py`, field quarantine, template generation, diversity | `test_known_fields_categories`, `test_generate_expressions_count`, `test_generate_expressions_unique`, `test_no_unverified_fields_used`, `test_classify_expression` |
| `tests/test_notifier.py` | `notifier.py`, Telegram formatting, callback query processing | `test_format_alpha_message`, `test_send_digest_success`, `test_send_alert`, `test_poll_actions_handles_submit`, `test_poll_actions_handles_skip` |
| `tests/test_main.py` | `main.py`, CLI subcommands, end-to-end pipeline orchestration | `test_cli_auth`, `test_cli_generate`, `test_cli_status`, `test_cli_digest_dry_run`, `test_cli_pipeline_mock` |

---

## 4. Running the Test Suite

### 4.1 Fast Unit Test Run (Recommended)
Excludes live integration tests hitting real BRAIN credentials:
```bash
pytest -q -m "not integration"
```

Expected output:
```text
........................................................................ [ 84%]
.............                                                            [100%]
85 passed, 1 deselected in 7.42s
```

### 4.2 Verbose Test Run with Names
```bash
pytest -v -m "not integration"
```

### 4.3 Running Specific Test Modules
```bash
# Test LLM client and key rotation
pytest tests/test_llm_client.py -v

# Test Simulation Engine and Circuit Breaker
pytest tests/test_engine.py -v

# Test Static Validator and Gatekeeper
pytest tests/test_validator.py -v
```

### 4.4 Syntax & Bytecode Compilation Check
Verify all files compile without syntax or import errors:
```bash
python -m compileall . -q
```

### 4.5 Running Live Integration Tests (Optional)
Requires active WorldQuant BRAIN credentials configured in `.env`:
```bash
pytest -m integration -v
```
