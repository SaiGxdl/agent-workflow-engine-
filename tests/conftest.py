# tests/conftest.py
import sys
from pathlib import Path

# Add the project root (parent of tests/) to sys.path so tests can import modules
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
