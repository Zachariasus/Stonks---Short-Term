"""
data/config.py
--------------
Central place for loading the project's API keys from the .env file.

Other modules import their keys from here, e.g.:
    from data.config import ANTHROPIC_API_KEY

The keys live in the .env file at the project root (which is never committed
to Git). If a key is missing or still set to the placeholder value, this
module PRINTS A WARNING so you know setup is incomplete — but it does not
crash, so the rest of the app can still be developed and run.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# The value written into .env / .env.example for keys that aren't filled in yet.
PLACEHOLDER = "your_key_here"

# This file lives in data/, so the project root is one folder up. Build the
# path to the .env there and load it into the environment. Using __file__
# means this works no matter which directory you run the app from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)


# Claude AI — powers the single-stock grader (AI-generated letter grade)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Alpha Vantage — earnings estimates and fundamentals
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

# FRED (Federal Reserve) — macro data: yield curve, PMI, credit spreads
FRED_API_KEY = os.getenv("FRED_API_KEY")

# NewsAPI — financial news aggregation
NEWS_API_KEY = os.getenv("NEWS_API_KEY")


# Human-readable name -> current value. Used only by the check below.
_REQUIRED_KEYS = {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "ALPHA_VANTAGE_API_KEY": ALPHA_VANTAGE_API_KEY,
    "FRED_API_KEY": FRED_API_KEY,
    "NEWS_API_KEY": NEWS_API_KEY,
}


def check_keys():
    """Warn (never crash) about any key that is missing or still a placeholder.

    Returns the list of key names that still need attention.
    """
    missing = [
        name
        for name, value in _REQUIRED_KEYS.items()
        if not value or value == PLACEHOLDER
    ]
    if missing:
        print(
            "⚠️  Warning: these API keys are not set yet "
            f"(missing or still '{PLACEHOLDER}'): {', '.join(missing)}.\n"
            f"    Edit your .env file at: {ENV_PATH}"
        )
    return missing


# Run the check once when this module is imported, so problems surface early.
# It only warns — it never raises.
_missing = check_keys()


# Allow `python data/config.py` to be run directly as a quick self-test.
if __name__ == "__main__":
    if not _missing:
        print("✅ All four API keys are set.")
