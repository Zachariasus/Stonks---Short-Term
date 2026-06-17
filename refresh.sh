#!/bin/bash
# Daily data pipeline: fetch S&P 500 prices/fundamentals → validate/correct bad
# bars → score every stock → (re)generate the flag watchlist → refresh news.
# Run automatically by the launchd job (com.stonks.dailyrefresh), or manually:
#     ./refresh.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
else
  echo "ERROR: venv not found. Run: python -m venv venv && pip install -r requirements.txt"
  exit 1
fi

# exec so the python process replaces bash (clean signal handling under launchd).
exec python -m data.scheduler --now
