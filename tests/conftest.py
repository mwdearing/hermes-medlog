"""Keep every test away from the developer's real config file and data: tests set their own MEDLOG_* variables."""
import os
import sys
from pathlib import Path

os.environ["MEDLOG_CONFIG"] = "/nonexistent/medlog-tests/config.json"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
