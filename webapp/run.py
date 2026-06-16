"""
webapp/run.py
=============
Launcher for the FastAPI backend.

Run from the PROJECT ROOT (with the venv active):
    python webapp/run.py

Production-style LOCAL launcher: no reload (filesystem watching is a dev-only
convenience), bound to 127.0.0.1 (loopback only — not reachable from other devices
on the network). FastAPI serves both the API and the built React frontend, so
http://localhost:8000 is the whole app.
"""

import sys
from pathlib import Path

# Put the project root on sys.path so uvicorn can import "webapp.main" even when
# this file is launched as `python webapp/run.py` (Python puts the script's own
# directory — webapp/ — on sys.path[0], not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    # host=127.0.0.1 → local-only; reload=False → one stable process.
    uvicorn.run("webapp.main:app", host="127.0.0.1", port=8000, reload=False)
