"""Configuration module for WorldQuant Brain automation pipeline.

Loads environment variables from .env and exposes typed constants and a
frozen Settings dataclass. Every module imports settings/constants from here.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from dotenv import load_dotenv
from litellm import Router

# Base paths
PROJECT_ROOT: Path = Path(__file__).resolve().parent
ENV_PATH: Path = PROJECT_ROOT / ".env"

# Load environment variables from .env file at project root
load_dotenv(dotenv_path=ENV_PATH)


def _require(name: str) -> str:
    """Retrieve a required environment variable or raise an actionable error."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Please set '{name}' in your .env file at {ENV_PATH}."
        )
    return value


# Required environment variables
BRAIN_EMAIL: str = _require("BRAIN_EMAIL")
BRAIN_PASSWORD: str = _require("BRAIN_PASSWORD")
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _require("TELEGRAM_CHAT_ID")


def _load_gemini_api_keys() -> list[str]:
    """Dynamically load and sort all GEMINI_API_KEY_* environment variables."""
    keys: list[tuple[int, str]] = []
    for k, v in os.environ.items():
        if k.startswith("GEMINI_API_KEY_") and v.strip():
            suffix = k[len("GEMINI_API_KEY_") :]
            idx = int(suffix) if suffix.isdigit() else 9999
            keys.append((idx, v.strip()))
    keys.sort(key=lambda item: item[0])
    return [k[1] for k in keys]


GEMINI_API_KEYS: list[str] = _load_gemini_api_keys()

# Module-level typed constants
BRAIN_BASE_URL: str = "https://api.worldquantbrain.com"
TOKEN_TTL_SECONDS: int = 14400
TOKEN_REFRESH_MARGIN_SECONDS: int = 300
MAX_CONCURRENT_SIMULATIONS: int = 3
POLL_INTERVAL_SECONDS: int = 5
POLL_TIMEOUT_SECONDS: int = 1800
MIN_SHARPE: float = 1.25
MIN_FITNESS: float = 1.0
MIN_TURNOVER: float = 0.01
MAX_TURNOVER: float = 0.70
DB_PATH: Path = PROJECT_ROOT / os.getenv("DB_PATH", "alphas.db")
TOP_N: int = int(os.getenv("TOP_N", "4"))

MODEL_LIST = [
    {"model_name": "proposer-primary", "litellm_params": {"model": "gemini/gemini-2.5-flash", "api_key": os.getenv("GEMINI_API_KEY_1")}},
    {"model_name": "proposer-fallback", "litellm_params": {"model": "groq/llama-3.3-70b-versatile", "api_key": os.getenv("GROQ_API_KEY")}},
    {"model_name": "critic-primary", "litellm_params": {"model": "openrouter/qwen/qwen3-coder:free", "api_key": os.getenv("OPENROUTER_API_KEY")}},
    {"model_name": "critic-fallback", "litellm_params": {"model": "openai/@cf/meta/llama-3.1-8b-instruct", "api_key": os.getenv("CLOUDFLARE_API_TOKEN"), "api_base": f"https://api.cloudflare.com/client/v4/accounts/{os.getenv('CLOUDFLARE_ACCOUNT_ID')}/ai/v1"}}
]
FALLBACKS = [
    {"proposer-primary": ["proposer-fallback"]},
    {"critic-primary": ["critic-fallback"]}
]
ROUTER = Router(model_list=MODEL_LIST, fallbacks=FALLBACKS, num_retries=1, timeout=60, cooldown_time=60)


@dataclass(frozen=True)
class Settings:
    """Frozen application settings container."""

    # Project root
    project_root: Path = PROJECT_ROOT

    # Brain credentials & Telegram notifications
    brain_email: str = BRAIN_EMAIL
    brain_password: str = BRAIN_PASSWORD
    telegram_bot_token: str = TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = TELEGRAM_CHAT_ID

    # API endpoints & auth timing
    brain_base_url: str = BRAIN_BASE_URL
    token_ttl_seconds: int = TOKEN_TTL_SECONDS
    token_refresh_margin_seconds: int = TOKEN_REFRESH_MARGIN_SECONDS

    # Simulation engine parameters
    max_concurrent_simulations: int = MAX_CONCURRENT_SIMULATIONS
    poll_interval_seconds: int = POLL_INTERVAL_SECONDS
    poll_timeout_seconds: int = POLL_TIMEOUT_SECONDS

    # Alpha quality thresholds
    min_sharpe: float = MIN_SHARPE
    min_fitness: float = MIN_FITNESS
    min_turnover: float = MIN_TURNOVER
    max_turnover: float = MAX_TURNOVER

    # Database & selection
    db_path: Path = DB_PATH
    top_n: int = TOP_N

    # Gemini LLM API keys
    gemini_api_keys: list[str] = field(default_factory=lambda: list(GEMINI_API_KEYS))

    def __getattr__(self, name: str):
        """Allow case-insensitive access (e.g., settings.BRAIN_BASE_URL)."""
        lower = name.lower()
        if lower in self.__dataclass_fields__:
            return getattr(self, lower)
        upper = name.upper()
        if upper in self.__dataclass_fields__:
            return getattr(self, upper)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


# Singleton settings instance
settings: Settings = Settings()


def _mask(value: str) -> str:
    """Mask secret value showing only the first 2 and last 2 characters."""
    if not value:
        return ""
    s = str(value)
    if len(s) <= 4:
        return "****"
    return f"{s[:2]}****{s[-2:]}"


if __name__ == "__main__":
    print("=== config.py loaded successfully ===")
    print(f"  PROJECT_ROOT:                 {PROJECT_ROOT}")
    print(f"  BRAIN_BASE_URL:               {BRAIN_BASE_URL}")
    print(f"  BRAIN_EMAIL:                  {_mask(BRAIN_EMAIL)}")
    print(f"  BRAIN_PASSWORD:               {_mask(BRAIN_PASSWORD)}")
    print(f"  TELEGRAM_BOT_TOKEN:           {_mask(TELEGRAM_BOT_TOKEN)}")
    print(f"  TELEGRAM_CHAT_ID:             {_mask(TELEGRAM_CHAT_ID)}")
    print(f"  TOKEN_TTL_SECONDS:            {TOKEN_TTL_SECONDS}")
    print(f"  TOKEN_REFRESH_MARGIN_SECONDS: {TOKEN_REFRESH_MARGIN_SECONDS}")
    print(f"  MAX_CONCURRENT_SIMULATIONS:   {MAX_CONCURRENT_SIMULATIONS}")
    print(f"  POLL_INTERVAL_SECONDS:        {POLL_INTERVAL_SECONDS}")
    print(f"  POLL_TIMEOUT_SECONDS:         {POLL_TIMEOUT_SECONDS}")
    print(f"  MIN_SHARPE:                   {MIN_SHARPE}")
    print(f"  MIN_FITNESS:                  {MIN_FITNESS}")
    print(f"  MIN_TURNOVER:                 {MIN_TURNOVER}")
    print(f"  MAX_TURNOVER:                 {MAX_TURNOVER}")
    print(f"  DB_PATH:                      {DB_PATH}")
    print(f"  TOP_N:                        {TOP_N}")
    print(f"  GEMINI_API_KEYS:              {[_mask(k) for k in GEMINI_API_KEYS]}")
