from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "appwrite" / "functions" / "gal-mirror" / "main.py"


def test_mirror_limits_supabase_timeout_before_app_import() -> None:
    script = """
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("gal_mirror_adapter", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

from app.config import get_settings

assert get_settings().supabase_timeout_seconds == 2.0
"""
    env = os.environ.copy()
    env["SUPABASE_TIMEOUT_SECONDS"] = "6.0"
    result = subprocess.run(
        [sys.executable, "-c", script, str(ADAPTER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
