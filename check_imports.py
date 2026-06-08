"""
check_imports.py
----------------
Quick health check for the project's core dependencies. It tries to import
each of the 9 packages we installed and prints the installed version of each.

Run this any time you want to confirm the environment is good — especially
after setting the project up on a new machine with:
    pip install -r requirements.txt

Usage:
    python check_imports.py
"""

import importlib
from importlib import metadata

# Each entry is (name as installed via pip, module name used in `import`).
# These differ for a couple of packages — e.g. you `pip install python-dotenv`
# but `import dotenv` in code.
PACKAGES = [
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("yfinance", "yfinance"),
    ("sqlalchemy", "sqlalchemy"),
    ("requests", "requests"),
    ("python-dotenv", "dotenv"),
    ("anthropic", "anthropic"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
]

all_ok = True

for pip_name, module_name in PACKAGES:
    try:
        importlib.import_module(module_name)        # Does the package import?
        version = metadata.version(pip_name)        # What exact version is installed?
        print(f"✓ {pip_name} ({version})")
    except Exception as err:                         # Keep going so we see every result.
        all_ok = False
        print(f"✗ {pip_name} — FAILED: {err}")

print()
if all_ok:
    print("All dependencies installed successfully")
else:
    print("Some dependencies are missing or broken — see the ✗ lines above.")
