#!/bin/bash
# Start the Stonks trading app
# Usage: ./start.sh
#        ./start.sh --refresh    (run a data refresh first, then start)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
else
  echo "ERROR: venv not found. Run: python -m venv venv && pip install -r requirements.txt"
  exit 1
fi

# Optional: run a data refresh before starting
if [ "$1" == "--refresh" ]; then
  echo "Running data refresh..."
  python -m data.scheduler --now
fi

# Open the browser after a short delay (macOS)
(sleep 2 && open http://localhost:8000) &

echo ""
echo "  Stonks app starting at http://localhost:8000"
echo "  Press Ctrl+C to stop."
echo ""

# Start the app
python webapp/run.py
