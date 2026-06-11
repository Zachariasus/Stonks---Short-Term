"""
webapp/run.py
=============
Launcher for the FastAPI backend.

Run from the PROJECT ROOT (with the venv active):
    python webapp/run.py

reload=True restarts the server automatically when you edit the code (handy
during development). The interactive API docs are then at http://localhost:8000/docs
"""

import uvicorn

if __name__ == "__main__":
    # Pass the app as an import string ("webapp.main:app") rather than the object
    # so that reload=True works (the reloader needs to re-import the module).
    uvicorn.run("webapp.main:app", host="0.0.0.0", port=8000, reload=True)
