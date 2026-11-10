# WorldQuant BRAIN Autonomous Alpha Pipeline

[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![pytest](https://img.shields.io/badge/pytest-85%2B%20Passing-brightgreen?logo=pytest&logoColor=white)](https://pytest.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot%20API-2CA5E0?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An enterprise-grade, multi-agent quantitative research and alpha discovery engine for the WorldQuant BRAIN platform. The system orchestrates generative AI hypothesis formulation, deterministic AST static validation, concurrent distributed backtesting, cryptographic deduplication, and human-in-the-loop Telegram approval.

---

## Overview

Developing mathematical trading signals (alphas) at scale requires exploring vast hypothesis spaces while maintaining strict data governance, avoiding platform rate limits, and preventing redundant simulation costs. 

The **WorldQuant BRAIN Autonomous Alpha Pipeline** provides an automated, resilient quantitative workflow:
1. **Hypothesis Generation**: Gemini 2.5 models formulate mathematically sound FASTEXPR alpha candidates across price-volume, fundamental, and analyst datasets.
2. **Deterministic Gatekeeping**: A static expression linter validates operator arity, balance, and strictly verified fields before hitting network endpoints.
3. **Concurrent Backtesting**: A strictly throttled (3-worker) simulation engine coordinates simulations, monitors asynchronous polling states, and extracts in-sample metrics.
4. **Resilience & Fault Tolerance**: Built-in Circuit Breakers, exponential backoff for HTTP 429s, network dropout recovery, and multi-key rotation ensure continuous 24/7 reliability.
5. **Human-in-the-Loop Governance**: Alphas exceeding production thresholds (Sharpe ≥ 1.25, Fitness > 1.0, Turnover within 1–70%) are delivered via Telegram cards for manual review and submission.

---

## System Architecture

![Architecture Diagram](architecture.png)
*Figure 1: End-to-end architecture detailing the autonomous flow from multi-agent LLM hypothesis generation and static gatekeeping to concurrent simulation, cryptographic deduplication, and Telegram review.*

---

## Key Features

- **Multi-Agent LLM Generation & Critique**: Uses the official Google GenAI SDK (`gemini-2.5-flash`) with a Proposer agent enforcing advanced FASTEXPR operators (`ts_decay_linear`, `group_neutralize`, `ts_zscore`) and an adversarial Portfolio Manager (PM) Critic filtering unviable economic rationales.
- **Dynamic API Key Rotation**: Round-robin failover across multiple Gemini API keys (`GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, ...) with automatic recovery on HTTP 429 quota exhaustion.
- **Deterministic Hard-Gate Validator**: Rejects hallucinated data fields, validates operator arity (such as `group_rank` parameter requirements), catches Python injection attempts, and allows valid scientific notation constants (e.g., `1e-9`).
- **Cryptographic SHA-256 Deduplication**: Expression normalization and hashing prevent running duplicate backtests, preserving API simulation limits and SQLite storage integrity.
- **Strict Concurrency Control**: Worker thread pooling capped strictly at 3 concurrent simulations to adhere to WorldQuant BRAIN terms of service.
- **Circuit Breaker Kill Switch**: Thread-safe three-state state machine (`CLOSED`, `OPEN`, `HALF_OPEN`) that halts simulation requests upon 3 consecutive 429 responses, alerts Telegram, and enforces a 300-second cooldown.
- **Interactive Telegram Bot**: Formats candidate performance cards (Sharpe, Fitness, Turnover) with inline buttons (`Submit` / `Skip`) backed by a non-blocking background polling daemon.
- **Production Windows Daemon**: VBScript wrapper (`run_listener.vbs`) and PowerShell watchdog (`watchdog_listener.ps1`) for unattended 24/7 background operation.

---

## Directory Structure

```text
worldquant-pipeline/
├── api_client.py          # Thread-safe WorldQuant BRAIN REST client (auth, retry, backoff)
├── config.py              # Environment configuration, pydantic settings, dynamic key loader
├── database.py            # SQLite schema, migration, SHA-256 deduplication, query layer
├── engine.py              # 3-worker simulation engine, filter gates, Circuit Breaker
├── generator.py           # Field quarantine repository & rule-based template fallback
├── llm_client.py          # Multi-key Gemini 2.5 SDK integration (Proposer & Critic)
├── main.py                # Unified CLI entry point with file & console logging
├── notifier.py            # Telegram card formatting, alert dispatch, and callback polling
├── validator.py           # Hard Gatekeeper: static AST & regex FASTEXPR validator
├── run_listener.vbs       # Silent background daemon runner for Windows
├── watchdog_listener.ps1  # Auto-recovery health-check script for Task Scheduler
├── DEPLOYMENT.md          # Comprehensive daemon and task scheduling documentation
├── requirements.txt       # Production dependencies
└── tests/                 # Comprehensive test suite (85+ passing tests)
```

---

## Installation & Setup

### Prerequisites
- Python 3.13+ (or 3.11+)
- Git
- Active WorldQuant BRAIN platform credentials
- Telegram Bot Token & Chat ID
- One or more Google Gemini API keys

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/worldquant-pipeline.git
cd worldquant-pipeline
```

### 2. Create and Activate Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the project root:
```ini
# WorldQuant BRAIN Credentials
BRAIN_EMAIL=your_email@domain.com
BRAIN_PASSWORD=your_brain_password

# Telegram Notification Settings
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789

# Gemini API Keys (Dynamic Key Rotation)
GEMINI_API_KEY_1=AIzaSyD-YourFirstKeyHere
GEMINI_API_KEY_2=AIzaSyD-YourSecondKeyHere
GEMINI_API_KEY_3=AIzaSyD-YourThirdKeyHere

# Optional Engine Threshold Overrides
MIN_SHARPE=1.25
MIN_FITNESS=1.0
MIN_TURNOVER=0.01
MAX_TURNOVER=0.70
```

---

## Usage

The CLI (`main.py`) provides direct control over individual subsystems and autonomous pipelines:

### 1. Verify Authentication
Test API connectivity and retrieve a valid session bearer token:
```bash
python main.py auth
```

### 2. Run Autonomous Pipeline
Execute the full workflow: Proposer generates hypotheses $\rightarrow$ Validator lints $\rightarrow$ Critic reviews $\rightarrow$ Engine simulates $\rightarrow$ Telegram delivers passing alphas:
```bash
# Run pipeline with 50 alphas
python main.py pipeline 50

# Test run in mock mode (no credits or tokens consumed)
python main.py pipeline 5 --mock
```

### 3. Dispatch Passing Alphas Digest
Send cards for passing alphas in the database to Telegram:
```bash
python main.py digest --limit 5
```

### 4. Start Telegram Interaction Listener
Listen for user clicks on Telegram buttons (`Submit` / `Skip`):
```bash
# Interactive console listener (600s default)
python main.py listen

# Or launch as a silent Windows background daemon:
wscript run_listener.vbs
```

### 5. Rule-Based Generation (Fallback)
Generate and simulate rule-based alphas without LLM dependencies:
```bash
python main.py simulate 10
```

---

## Deployment & 24/7 Automation

For hands-off overnight operation on Windows:
- **Silent Background Daemon**: Launch `run_listener.vbs` to execute `pythonw.exe main.py listen` with no console window.
- **Watchdog Recovery**: Schedule `watchdog_listener.ps1` in Windows Task Scheduler every 15 minutes to guarantee listener uptime.
- **Overnight Discovery**: Schedule `python main.py pipeline 50` daily at 2:00 AM.

Refer to [DEPLOYMENT.md](DEPLOYMENT.md) for step-by-step Task Scheduler instructions and CLI automation commands.

---

## Testing

The codebase includes comprehensive unit and integration tests covering API retries, circuit breaking, SQLite migrations, static AST linting, prompt structuring, and Telegram interactions.

Run the test suite:
```bash
# Run unit tests
pytest -q -m "not integration"

# Run tests with detailed verbose output
pytest -v
```

```text
======================= 85 passed, 1 deselected in 7.11s =======================
```

---

## Disclaimer

> [!IMPORTANT]
> This software is intended strictly for quantitative research, educational experimentation, and portfolio demonstration purposes. Automated alpha submission to WorldQuant BRAIN competitions is intentionally disabled; the platform employs human-in-the-loop Telegram verification to ensure compliance with WorldQuant BRAIN platform guidelines and submission policies.
