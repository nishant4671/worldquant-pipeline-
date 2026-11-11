# Contributing Guidelines & Rules of Engagement

Welcome to the **WorldQuant BRAIN Autonomous Alpha Pipeline**. To maintain quantitative rigor, platform compliance, security, and continuous deployment reliability across our engineering team, all contributors must adhere strictly to these guidelines.

---

## 1. Core Rules of Engagement

These six rules are non-negotiable architectural and operational constraints:

### Rule 1: Never Hardcode Credentials or API Keys
- Under no circumstances should passwords, bearer tokens, Telegram bot tokens, chat IDs, or Gemini API keys be committed to Git.
- All secrets must reside exclusively in `.env`, which is permanently listed in `.gitignore`.
- Configuration files must access secrets only via `config.py`.
- If an API key or password is ever accidentally committed, immediately notify the team, invalidate the key on the provider console, and purge Git history.

### Rule 2: Always Use Virtual Environments
- Never install packages into the global system Python environment.
- Always create and activate a project virtual environment:
  ```bash
  python3 -m venv venv
  source venv/bin/activate  # Linux / macOS
  .\venv\Scripts\activate   # Windows
  ```
- Any newly introduced dependency must be pinned in `requirements.txt` with an exact version (`package==x.y.z`).

### Rule 3: Mandatory Pre-Commit Testing
- No code may be committed or merged into `main` without passing the complete unit test suite:
  ```bash
  python -m compileall . -q
  pytest -q -m "not integration"
  ```
- All 85+ unit tests must pass cleanly (0 failures, 0 errors).
- New features or bug fixes must include corresponding unit tests in `tests/`.

### Rule 4: Never Auto-Submit Alphas to WorldQuant BRAIN
- The pipeline is architected around **human-in-the-loop governance**.
- Passing alphas are delivered via Telegram cards for manual human review and discretionary submission.
- **Automated, unattended script submission of alphas directly to BRAIN competitions is strictly prohibited**. Bypassing the Telegram review workflow violates platform competition rules and risks account termination.

### Rule 5: Concurrency & Rate Limit Discipline
- The simulation worker pool is strictly capped at **3 concurrent threads** (`max_workers = 3` in `engine.py`).
- Do not increase `max_workers` under any circumstances; exceeding 3 concurrent simulations triggers server-side HTTP 429 throttling and account flagging on WorldQuant BRAIN.
- Never bypass or disable the `CircuitBreaker`. The kill-switch prevents API blacklisting when the server issues consecutive 429 errors.

### Rule 6: Respect the Field Quarantine Strategy
- FASTEXPR is a domain-specific language where referencing an unverified field causes silent runtime evaluation errors or NaN results.
- Never add fields directly into `KNOWN_FIELDS` in `generator.py` based on assumptions.
- Any prospective field must remain in `UNVERIFIED_FIELDS` until verified against the WorldQuant BRAIN Data Explorer UI or official documentation.

---

## 2. Development Workflow

### 2.1 Branching Strategy
- `main`: Production branch; corresponds directly to the deployed Azure instance. Must remain stable and passing all tests at all times.
- Feature branches: Created off `main` using descriptive naming:
  - `feat/feature-name`: New capabilities or algorithms.
  - `fix/bug-description`: Bug fixes or error handling improvements.
  - `docs/doc-update`: Documentation or diagram updates.
  - `refactor/component-name`: Code refactoring without behavior modification.

### 2.2 Conventional Commits
All commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```text
<type>(<scope>): <short description in imperative mood>

[optional body explaining context and rationale]
```

**Allowed Types**:
- `feat`: A new feature (e.g. `feat(llm): add prompt template for intraday mean reversion`)
- `fix`: A bug fix (e.g. `fix(config): define GEMINI_API_KEYS as module-level global`)
- `docs`: Documentation updates (e.g. `docs(arch): add Mermaid data flow diagram`)
- `refactor`: Code changes that neither fix a bug nor add a feature
- `test`: Adding or correcting tests (e.g. `test(engine): add test for 429 circuit breaker trip`)
- `chore`: Maintenance tasks, dependencies, `.gitignore` updates

---

## 3. Code Quality Standards

1. **Python Version**: Target Python 3.11+ / 3.13.
2. **Type Annotations**: All function signatures must include full type hints (`int`, `str | None`, `list[tuple[str, str]]`).
3. **Docstrings**: Use Google-style docstrings for all modules, classes, and public methods detailing `Args`, `Returns`, and `Raises`.
4. **Error Handling**: Use custom exceptions (such as `BrainApiError`) rather than bare `except Exception:` unless explicitly implementing a fail-open guarantee (such as the PM Critic). Always log errors using Python's standard `logging` module.

---

## 4. Pull Request Checklist

Before submitting a Pull Request for review:
- [ ] Code is formatted and compliant with PEP 8.
- [ ] No hardcoded keys, passwords, or tokens in code or diffs.
- [ ] `python -m compileall . -q` runs cleanly without error.
- [ ] `pytest -q -m "not integration"` passes 100% (all 85+ tests).
- [ ] Documentation has been updated to reflect any changes in `docs/`.
- [ ] PR title follows Conventional Commits format.
